import atexit
import json
import os
import re
import requests
import time
import threading
import unicodedata

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from config import (
    DATA_DIR,
    JIKAN_BASE_URL,
    MAGAZINES,
)


class JikanError(Exception):
    pass


class JikanClient:

    MAX_ATTEMPTS = 5
    MAX_PAGES = 1000

    MIN_REQUEST_INTERVAL = 0.55

    # Los resultados de búsqueda se guardan también en disco, así que
    # el TTL es más largo que antes (6 horas).
    SEARCH_CACHE_TTL = 21600
    MAX_SEARCH_CACHE = 100

    # Archivo donde se persisten las cachés entre ejecuciones.
    # Para forzar una recarga completa, borrá este archivo.
    DISK_CACHE_PATH = DATA_DIR / "cache.json"
    DISK_SAVE_INTERVAL = 10

    # Filtros fijos de la búsqueda de mangas: todos los géneros y
    # themes, pero siempre únicamente mangas en publicación.
    SEARCH_STATUS = "publishing"

    # Versión de la consulta. Forma parte de la clave de caché para que
    # resultados de consultas anteriores (por ejemplo, Romance) nunca se
    # reutilicen como si fueran el catálogo de todos los géneros.
    # Cambiar este valor cada vez que cambien los parámetros de búsqueda.
    SEARCH_CACHE_VERSION = "all-genres-publishing-v2"

    FORUM_CACHE_TTL = 86400
    MAX_FORUM_CACHE = 5000

    # Revistas (serializations) de cada manga. Casi nunca cambian,
    # así que se guardan por mucho tiempo.
    SERIALIZATION_CACHE_TTL = 30 * 86400

    # Si fallan tantas consultas seguidas, se asume un problema de
    # conexión con Tenrai y se corta la detección de revistas.
    MAX_CONSECUTIVE_LOOKUP_FAILURES = 5

    def __init__(self):

        self.session = requests.Session()

        self.session.headers.update({
            "User-Agent": "MangaRomanceFinder/1.0"
        })

        self.last_request = 0
        self.lock = threading.Lock()

        self.magazine_cache = None
        self.magazine_cache_time = 0

        self.search_cache = {}
        self.search_cache_lock = threading.Lock()

        self.forum_cache = {}
        self.forum_cache_lock = threading.Lock()

        self.serial_cache = {}
        self.serial_cache_lock = threading.Lock()

        self.disk_lock = threading.Lock()
        self.last_disk_save = 0

        self.load_disk_cache()

        atexit.register(self.save_disk_cache)

    # --------------------------------
    # Caché persistente en disco
    # --------------------------------

    def load_disk_cache(self):
        """
        Carga las cachés guardadas por ejecuciones anteriores.
        Cualquier problema con el archivo se ignora: simplemente
        se empieza con la caché vacía.
        """

        try:

            with open(
                self.DISK_CACHE_PATH,
                "r",
                encoding="utf-8"
            ) as file:
                stored = json.load(file)

            now = time.time()

            if stored.get("version") == self.SEARCH_CACHE_VERSION:

                for key, (saved, data) in stored.get(
                    "search", {}
                ).items():

                    if now - saved < self.SEARCH_CACHE_TTL:
                        self.search_cache[key] = (saved, data)

            for key, (saved, data) in stored.get(
                "forum", {}
            ).items():

                if now - saved < self.FORUM_CACHE_TTL:
                    self.forum_cache[int(key)] = (saved, data)

            for key, (saved, data) in stored.get(
                "serializations", {}
            ).items():

                if now - saved < self.SERIALIZATION_CACHE_TTL:
                    self.serial_cache[int(key)] = (saved, data)

        except (OSError, ValueError, TypeError, AttributeError):

            self.search_cache = {}
            self.forum_cache = {}
            self.serial_cache = {}

    def save_disk_cache(self, throttle=False):

        if (
            throttle
            and time.time() - self.last_disk_save
            < self.DISK_SAVE_INTERVAL
        ):
            return

        with self.disk_lock:

            self.last_disk_save = time.time()

            with self.search_cache_lock:
                search = dict(self.search_cache)

            with self.forum_cache_lock:
                forum = dict(self.forum_cache)

            with self.serial_cache_lock:
                serializations = dict(self.serial_cache)

            payload = {
                "version": self.SEARCH_CACHE_VERSION,
                "serializations": {
                    str(key): value
                    for key, value in serializations.items()
                },
                "search": search,
                "forum": {
                    str(key): value
                    for key, value in forum.items()
                }
            }

            temporary = self.DISK_CACHE_PATH.with_name(
                "cache.json.tmp"
            )

            try:

                temporary.parent.mkdir(
                    parents=True,
                    exist_ok=True
                )

                with open(
                    temporary,
                    "w",
                    encoding="utf-8"
                ) as file:
                    json.dump(payload, file)

                os.replace(temporary, self.DISK_CACHE_PATH)

            except (OSError, TypeError, ValueError):
                # La caché en disco es opcional: si falla, seguimos.
                pass

    def normalize(self, text):

        text = unicodedata.normalize(
            "NFKD",
            text or ""
        )

        text = "".join(
            char for char in text
            if not unicodedata.combining(char)
        )

        return "".join(
            char.lower()
            for char in text
            if char.isalnum()
        )

    def get_retry_delay(self, attempt, retry_after=None):

        if retry_after:

            try:
                return max(0, float(retry_after))

            except (TypeError, ValueError):
                pass

            try:

                retry_date = parsedate_to_datetime(
                    retry_after
                )

                if retry_date.tzinfo is None:

                    retry_date = retry_date.replace(
                        tzinfo=timezone.utc
                    )

                now = datetime.now(timezone.utc)

                return max(
                    0,
                    (retry_date - now).total_seconds()
                )

            except (TypeError, ValueError, OverflowError):
                pass

        return min(2 ** attempt, 30)

    def get(self, endpoint, params=None):

        base_url = JIKAN_BASE_URL.rstrip("/")
        endpoint = endpoint.lstrip("/")

        url = f"{base_url}/{endpoint}"

        with self.lock:

            for attempt in range(self.MAX_ATTEMPTS):

                elapsed = (
                    time.monotonic() - self.last_request
                )

                if elapsed < self.MIN_REQUEST_INTERVAL:

                    time.sleep(
                        self.MIN_REQUEST_INTERVAL - elapsed
                    )

                try:

                    response = self.session.get(
                        url,
                        params=params,
                        timeout=(10, 30)
                    )

                    self.last_request = time.monotonic()

                except requests.RequestException as error:

                    self.last_request = time.monotonic()

                    if attempt == self.MAX_ATTEMPTS - 1:

                        raise JikanError(
                            f"Error de conexión con Tenrai: {error}"
                        )

                    time.sleep(
                        self.get_retry_delay(attempt)
                    )

                    continue

                if response.status_code == 429:

                    if attempt == self.MAX_ATTEMPTS - 1:

                        raise JikanError(
                            "Tenrai rechazó la solicitud por "
                            "límite de peticiones (HTTP 429). "
                            "Intentá nuevamente más tarde."
                        )

                    delay = self.get_retry_delay(
                        attempt,
                        response.headers.get("Retry-After")
                    )

                    time.sleep(delay)
                    continue

                if response.status_code >= 500:

                    if attempt == self.MAX_ATTEMPTS - 1:

                        raise JikanError(
                            f"Tenrai devolvió un error "
                            f"HTTP {response.status_code}."
                        )

                    time.sleep(
                        self.get_retry_delay(attempt)
                    )

                    continue

                try:

                    response.raise_for_status()

                except requests.HTTPError as error:

                    raise JikanError(
                        f"Error HTTP {response.status_code} "
                        f"consultando Tenrai: {error}"
                    )

                try:

                    data = response.json()

                except ValueError:

                    raise JikanError(
                        "Tenrai devolvió una respuesta "
                        "que no contiene JSON válido."
                    )

                if not isinstance(data, dict):

                    raise JikanError(
                        "Tenrai devolvió una respuesta "
                        "con un formato inesperado."
                    )

                return data

            raise JikanError(
                "Tenrai no respondió después de varios intentos."
            )

    def get_magazines(
        self,
        force_refresh=False,
        progress_callback=None
    ):

        if (
            self.magazine_cache is not None
            and not force_refresh
            and time.time() - self.magazine_cache_time < 21600
        ):

            if progress_callback:
                progress_callback(1, 1)

            return self.magazine_cache

        magazines = []
        page = 1

        while True:

            response = self.get(
                "/magazines",
                {
                    "page": page
                }
            )

            page_data = response.get("data", [])

            if not isinstance(page_data, list):

                raise JikanError(
                    "Tenrai devolvió un catálogo de revistas inválido."
                )

            magazines.extend(page_data)

            pagination = response.get(
                "pagination",
                {}
            )

            total_pages = pagination.get(
                "last_visible_page",
                page
            )

            if progress_callback:

                progress_callback(
                    page,
                    total_pages
                )

            if not pagination.get("has_next_page"):
                break

            page += 1

            if page > self.MAX_PAGES:

                raise JikanError(
                    "Se superó el límite de seguridad "
                    "al recuperar las revistas."
                )

        self.magazine_cache = magazines
        self.magazine_cache_time = time.time()

        return magazines

    def resolve_magazines(self, progress_callback=None):

        catalogue = self.get_magazines(
            progress_callback=progress_callback
        )

        lookup = {}

        for magazine in catalogue:

            name = magazine.get("name", "")
            magazine_id = magazine.get("mal_id")

            if not name or not magazine_id:
                continue

            normalized = self.normalize(name)

            lookup.setdefault(
                normalized,
                []
            ).append({
                "mal_id": magazine_id,
                "name": name
            })

        resolved = {}

        for requested in MAGAZINES:

            matches = lookup.get(
                self.normalize(requested),
                []
            )

            if len(matches) == 1:

                resolved[requested] = matches[0]

            elif len(matches) > 1:

                resolved[requested] = {
                    "ambiguous": True,
                    "matches": matches
                }

        return resolved

    # --------------------------------
    # Revistas detectadas a partir de la lista de MAL
    # --------------------------------

    def get_manga_serializations(self, manga_id):
        """
        Devuelve las revistas en las que se publica un manga como
        [{"mal_id": ..., "name": ...}]. Usa la caché en disco.
        """

        with self.serial_cache_lock:

            cached = self.serial_cache.get(manga_id)

            if (
                cached
                and time.time() - cached[0]
                < self.SERIALIZATION_CACHE_TTL
            ):
                return cached[1], True

        response = self.get(f"/manga/{manga_id}")

        data = response.get("data")

        if not isinstance(data, dict):

            raise JikanError(
                f"Tenrai devolvió datos inválidos para el manga "
                f"{manga_id}."
            )

        # Si el campo no existe, Tenrai no está entregando las
        # revistas: es mejor avisar que devolver un catálogo vacío.
        if "serializations" not in data:

            raise JikanError(
                "Tenrai no devolvió el campo 'serializations' "
                f"para el manga {manga_id}."
            )

        magazines = []

        for item in data.get("serializations") or []:

            magazine_id = item.get("mal_id")
            name = item.get("name")

            if magazine_id and name:

                magazines.append({
                    "mal_id": magazine_id,
                    "name": name
                })

        with self.serial_cache_lock:

            self.serial_cache[manga_id] = (
                time.time(),
                magazines
            )

        self.save_disk_cache(throttle=True)

        return magazines, False

    def detect_magazines(
        self,
        manga_ids,
        progress_callback=None
    ):
        """
        Obtiene las revistas únicas de una colección de mangas.
        Devuelve un diccionario con la lista ordenada de revistas
        y la cantidad de mangas que no pudieron consultarse.
        """

        magazines = {}
        failed = 0
        consecutive_failures = 0
        last_error = None

        ids = sorted(manga_ids)

        for index, manga_id in enumerate(ids, start=1):

            try:

                found, _ = self.get_manga_serializations(manga_id)

                consecutive_failures = 0

                for magazine in found:
                    magazines[magazine["mal_id"]] = magazine["name"]

            except JikanError as error:

                failed += 1
                consecutive_failures += 1
                last_error = error

                if (
                    consecutive_failures
                    >= self.MAX_CONSECUTIVE_LOOKUP_FAILURES
                ):

                    raise JikanError(
                        "No se pudieron detectar las revistas: "
                        f"{last_error}"
                    )

            if progress_callback:
                progress_callback(index, len(ids))

        self.save_disk_cache()

        if ids and failed == len(ids):

            raise JikanError(
                "No se pudieron detectar las revistas: "
                f"{last_error}"
            )

        return {
            "magazines": sorted(
                (
                    {"mal_id": magazine_id, "name": name}
                    for magazine_id, name in magazines.items()
                ),
                key=lambda item: item["name"].lower()
            ),
            "failed": failed
        }

    def clear_search_cache(self):
        # La caché guarda los resultados SIN excluir los mangas de
        # Reading (la exclusión se aplica al leerla), así que subir una
        # nueva exportación no obliga a volver a descargar nada.
        pass

    def get_latest_chapter_discussion(self, manga_id):
        """
        Obtiene la discusión del capítulo más alto encontrado
        en la última página del foro de MyAnimeList.
        """

        now = time.time()

        with self.forum_cache_lock:

            cached = self.forum_cache.get(manga_id)

            if cached:

                cached_time, cached_data = cached

                if now - cached_time < self.FORUM_CACHE_TTL:
                    return dict(cached_data)

                del self.forum_cache[manga_id]

        # La primera página permite conocer la cantidad total.
        first_response = self.get(
            f"/manga/{manga_id}/forum",
            {
                "page": 1
            }
        )

        pagination = first_response.get(
            "pagination",
            {}
        )

        try:

            last_page = max(
                1,
                int(pagination.get("last_visible_page", 1))
            )

        except (TypeError, ValueError):
            last_page = 1

        # Consultamos la última página para obtener los hilos
        # más recientes del foro.
        if last_page > 1:

            response = self.get(
                f"/manga/{manga_id}/forum",
                {
                    "page": last_page
                }
            )

        else:
            response = first_response

        discussions = response.get("data", [])

        if not isinstance(discussions, list):

            raise JikanError(
                "Tenrai devolvió discusiones de foro inválidas."
            )

        chapter_pattern = re.compile(
            r"\b(?:chapter|ch\.?)\s*#?\s*"
            r"(\d+(?:\.\d+)?)\b",
            re.IGNORECASE
        )

        candidates = []

        for discussion in discussions:

            if not isinstance(discussion, dict):
                continue

            title = discussion.get("title") or ""

            match = chapter_pattern.search(title)

            if not match:
                continue

            try:
                chapter = float(match.group(1))

            except (TypeError, ValueError):
                continue

            candidates.append({
                "chapter": chapter,
                "date": discussion.get("date"),
                "url": discussion.get("url"),
                "title": title,
                "topic_id": discussion.get("mal_id")
            })

        if not candidates:

            result = {
                "forum_status": "empty",
                "latest_chapter": None,
                "chapter_date": None,
                "chapter_url": None,
                "chapter_title": None
            }

        else:

            latest = max(
                candidates,
                key=lambda item: (
                    item["chapter"],
                    item["date"] or ""
                )
            )

            chapter = latest["chapter"]

            if chapter.is_integer():
                chapter = int(chapter)

            result = {
                "forum_status": "found",
                "latest_chapter": chapter,
                "chapter_date": latest["date"],
                "chapter_url": latest["url"],
                "chapter_title": latest["title"],
                "forum_topic_id": latest["topic_id"]
            }

        with self.forum_cache_lock:

            if len(self.forum_cache) >= self.MAX_FORUM_CACHE:

                oldest_key = min(
                    self.forum_cache,
                    key=lambda key: self.forum_cache[key][0]
                )

                del self.forum_cache[oldest_key]

            self.forum_cache[manga_id] = (
                time.time(),
                result
            )

        self.save_disk_cache(throttle=True)

        return dict(result)

    def search_manga(
        self,
        magazine_id,
        reading_ids,
        order_by="score",
        progress_callback=None
    ):

        allowed_orders = [
            "score",
            "popularity",
            "members",
            "start_date"
        ]

        if order_by not in allowed_orders:
            order_by = "score"

        reading_ids = frozenset(reading_ids)

        # La clave no incluye reading_ids: se guardan los resultados
        # completos y la exclusión se aplica al devolverlos.
        cache_key = (
            f"{self.SEARCH_CACHE_VERSION}|"
            f"{magazine_id}|{order_by}"
        )

        now = time.time()

        with self.search_cache_lock:

            cached = self.search_cache.get(cache_key)

            if cached:

                cached_time, cached_data = cached

                if now - cached_time < self.SEARCH_CACHE_TTL:

                    result = self.build_search_result(
                        cached_data["manga"],
                        cached_data["pages_checked"],
                        reading_ids,
                        True
                    )

                    if progress_callback:
                        progress_callback(
                            result["pages_checked"],
                            result["pages_checked"]
                        )

                    return result

                del self.search_cache[cache_key]

        all_manga = []
        pages_checked = 0
        page = 1

        while True:

            response = self.get(
                "/manga",
                {
                    "status": self.SEARCH_STATUS,
                    "magazines": str(magazine_id),
                    "type": "manga",
                    "order_by": order_by,
                    "sort": "desc",
                    "limit": 25,
                    "page": page
                }
            )

            pages_checked += 1

            manga_list = response.get("data", [])

            if not isinstance(manga_list, list):

                raise JikanError(
                    "Tenrai devolvió resultados de manga inválidos."
                )

            for manga in manga_list:

                manga_id = manga.get("mal_id")

                if manga_id is None:
                    continue

                all_manga.append(manga)

            pagination = response.get(
                "pagination",
                {}
            )

            total_pages = pagination.get(
                "last_visible_page",
                page
            )

            if progress_callback:

                progress_callback(
                    page,
                    total_pages
                )

            if not pagination.get("has_next_page"):
                break

            page += 1

            if page > self.MAX_PAGES:

                raise JikanError(
                    "Se superó el límite de seguridad "
                    "de páginas de resultados."
                )

        with self.search_cache_lock:

            if len(self.search_cache) >= self.MAX_SEARCH_CACHE:

                oldest_key = min(
                    self.search_cache,
                    key=lambda key: self.search_cache[key][0]
                )

                del self.search_cache[oldest_key]

            self.search_cache[cache_key] = (
                time.time(),
                {
                    "manga": all_manga,
                    "pages_checked": pages_checked
                }
            )

        self.save_disk_cache()

        return self.build_search_result(
            all_manga,
            pages_checked,
            reading_ids,
            False
        )

    def build_search_result(
        self,
        all_manga,
        pages_checked,
        reading_ids,
        cache_hit
    ):
        """
        Excluye únicamente los mangas que están en Reading.
        """

        results = []
        excluded = 0

        for manga in all_manga:

            if manga.get("mal_id") in reading_ids:

                excluded += 1
                continue

            results.append(manga)

        return {
            "results": results,
            "excluded": excluded,
            "pages_checked": pages_checked,
            "cache_hit": cache_hit
        }


jikan = JikanClient()