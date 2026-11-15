"""
Catálogo de mangas publicados en Argentina por editoriales argentinas.

Combina tres fuentes. Si una serie aparece en más de una, gana la más
confiable:

  1. Manual  - lo que cargás vos. Siempre gana y nunca se pisa al
               actualizar el catálogo.
  2. Ivrea   - catálogo oficial de Ivrea Argentina (tomos publicados y
               estado de cada serie). Se empareja con MAL por título.
  3. Stacks  - listas "Manga published in Argentina" de MyAnimeList
               (comunitarias: sirven de base, pero pueden estar
               desactualizadas o incompletas).

Todo se guarda en PostgreSQL y la actualización corre en segundo plano.
"""

from __future__ import annotations

import html
import logging
import re
import threading
import time
import unicodedata
from datetime import date
from typing import Any, Optional

import requests

from tomo import db, store
from tomo.config import DATA_DIR, JIKAN_BASE_URL

# --------------------------------
# Configuración
# --------------------------------

IVREA_BASE = "https://www.ivrea.com.ar"

# Páginas desde las que se juntan los enlaces /titulo/<slug>/.
IVREA_INDEX_PATHS = [
    "/catalogo/",
    "/shonen/",
    "/seinen/",
    "/shojo/",
    "/yuri/",
    "/yaoi-bl/",
    "/manhwa-webcomics/",
    "/novelas/",
    "/comics/",
]

# IDs de los stacks "Manga published in Argentina" de MAL.
ARGENTINA_STACK_IDS = [
    58271, 42144, 42676, 58160, 42280, 42710, 42711, 42712, 42311,
    42713, 42714, 42716, 42321, 42765, 42766, 42347, 42877, 42796,
    42797, 42387, 58170, 42452, 58224, 43034, 42800, 42801, 42802,
]

log = logging.getLogger("tomo.catalog")

CALENDAR_URL_PATHS = ["/proximas/", "/proximas-archivo33333/"]
CALENDAR_TTL = 12 * 3600
DEBUG_DIR = DATA_DIR / "ivrea_debug"
MAX_DEBUG_PAGES = 15

# Sube cuando mejora el lector de fichas: fuerza a leerlas de nuevo.
PARSER_VERSION = 2

STACKS_TTL = 7 * 24 * 3600
IVREA_TTL = 7 * 24 * 3600       # series en curso: se vuelven a leer

# Candado de la base que impide dos actualizaciones del catálogo a la vez.
REFRESH_LOCK_ID = 727301

# Cada cuánto se mira si otro proceso cambió el catálogo en la base.
RELOAD_CHECK_INTERVAL = 15

# Una actualización que no da señales de vida en este tiempo se da por caída.
JOB_STALE_SECONDS = 90
HEARTBEAT_SECONDS = 20

# Cada cuánto se guarda el avance de la actualización en la base.
STATE_SAVE_INTERVAL = 1.0
IVREA_RETRY_TTL = 24 * 3600     # páginas que no se pudieron leer

REQUEST_INTERVAL = 0.55         # Tenrai
IVREA_INTERVAL = 1.0            # un pedido por segundo al sitio de Ivrea
MAX_CONSECUTIVE_FAILURES = 8

USER_AGENT = "Tomo/1.0 (herramienta personal de biblioteca de manga)"


class ArgentinaCatalogError(Exception):
    pass


# --------------------------------
# Utilidades de texto
# --------------------------------

def normalize_title(value: Optional[str]) -> str:
    """Minúsculas, sin acentos ni símbolos: 'Ōoku' -> 'ooku'."""

    if not value:
        return ""

    text = unicodedata.normalize("NFKD", value)
    text = "".join(c for c in text if not unicodedata.combining(c))

    return re.sub(r"[^a-z0-9]+", "", text.lower())


def title_keys(*titles: Optional[str]) -> set[str]:
    """Claves de comparación de uno o varios títulos."""

    keys = set()

    for title in titles:
        if not title:
            continue

        keys.add(normalize_title(title))

        # Sin lo que está entre paréntesis: "Fukushima (1-F)" -> "Fukushima"
        stripped = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", title)
        keys.add(normalize_title(stripped))

    keys.discard("")

    return keys


_PUBLISHER_NAMES = {
    "ivrea": "Ivrea",
    "ivrea argentina": "Ivrea",
    "panini comics": "Panini",
    "panini": "Panini",
    "ovni press": "Ovni Press",
    "ovni": "Ovni Press",
    "distrito manga": "Distrito Manga",
    "distrito": "Distrito Manga",
    "kemuri": "Kemuri",
    "planeta cómic": "Planeta Cómic",
    "planeta comic": "Planeta Cómic",
    "planeta": "Planeta Cómic",
    "utopía": "Utopía",
    "utopia": "Utopía",
    "utopia editorial": "Utopía",
    "moztros": "Moztros",
    "deuxstudio editorial": "Deuxstudio",
    "deuxstudio": "Deuxstudio",
    "pop fiction": "Pop Fiction",
    "larp editores": "LARP Editores",
    "editorial vertice": "Vértice",
    "editorial vértice": "Vértice",
    "deuxstudio editoria": "Deuxstudio",
    "vertice": "Vértice",
}


def _normalize_publisher(name: Optional[str]) -> Optional[str]:
    """
    Unifica nombres de editoriales. Si viene una lista ("A (Defunct) / B"),
    descarta las que ya no existen y deja las demás.
    """

    if not name or not name.strip():
        return None

    parts = [p.strip() for p in re.split(r"\s*/\s*", name) if p.strip()]

    alive = [p for p in parts if not re.search(r"defunct", p, re.I)]

    chosen = alive or parts

    result: list[str] = []

    for part in chosen:

        clean = re.sub(r"\s*\([^)]*\)", "", part).strip()

        if not clean:
            continue

        clean = _PUBLISHER_NAMES.get(clean.lower(), clean)

        if clean not in result:
            result.append(clean)

    return " / ".join(result) or None


def _normalize_status(raw: Optional[str]) -> Optional[str]:
    """
    Estado de la edición argentina, siempre en español:
    En curso, Completa, Discontinuada, En pausa o Próximamente.
    Si hay varias ediciones ("Completed (original), Publishing (Kanzenban)")
    manda la última, que es la vigente.
    """

    if not raw or not raw.strip():
        return None

    plain = re.sub(r"\([^)]*\)", " ", raw)

    segments = [
        seg.strip(" ?.")
        for seg in re.split(r"\s*[/,]\s*", plain)
        if seg.strip(" ?.")
    ]

    if not segments:
        return None

    text = segments[-1].lower()

    rules = [
        (("publishing", "ongoing", "en curso", "en emisi"), "En curso"),
        (("complet", "finished", "finalizad"), "Completa"),
        (("discontinu", "cancel", "descontinu"), "Discontinuada"),
        (("hold", "hiatus", "pausa", "hiato"), "En pausa"),
        (("to be released", "upcoming", "pr\u00f3xim", "proxim"), "Próximamente"),
    ]

    for words, label in rules:
        if any(word in text for word in words):
            return label

    return segments[-1].capitalize()


# --------------------------------
# Stacks de MyAnimeList
# --------------------------------

def _parse_note(note: str) -> dict[str, Any]:
    """Extrae editorial, estado, tomos y título local de la nota."""

    result: dict[str, Any] = {
        "publisher": None,
        "status": None,
        "volumes_ar": None,
        "title_local": None,
    }

    for line in (note or "").splitlines():

        line = line.strip()
        lower = line.lower()

        if lower.startswith("publisher:"):
            result["publisher"] = line.split(":", 1)[1].strip() or None

        elif lower.startswith("status:"):
            result["status"] = line.split(":", 1)[1].strip() or None

        elif lower.startswith("volumes:"):
            match = re.search(r"(\d+)", line.split(":", 1)[1])
            if match:
                result["volumes_ar"] = int(match.group(1))

        elif lower.startswith("title:"):
            result["title_local"] = line.split(":", 1)[1].strip() or None

    return result


# --------------------------------
# Páginas de Ivrea
# --------------------------------

_SLUG_RE = re.compile(
    r"""href=["'](?:https?://(?:www\.)?ivrea\.com\.ar)?/titulo/"""
    r"""([A-Za-z0-9_%.\-]+)/?["']""",
    re.IGNORECASE,
)

_LABELS = (
    r"AUTOR(?:ES)?|ESTADO EN JAP[ÓO]N|ESTADO EN ARGENTINA|"
    r"NOMBRE ORIGINAL (?:JAPON[ÉE]S|COREANO|CHINO)|ESTADO|SERIE DE|"
    r"IDEA ORIGINAL|REALIZACI[ÓO]N|GUI[ÓO]N|DIBUJO|COLOREADO|"
    r"G[ÉE]NERO|FORMATO|EDITORIAL|"
    r"DEMOGRAF[ÍI]A|A[ÑN]O|PRECIO|P[ÁA]GINAS|ISBN"
)


def extract_slugs(page_html: str) -> list[str]:
    """Slugs únicos de los enlaces /titulo/<slug>/ de una página."""

    seen: dict[str, None] = {}

    for slug in _SLUG_RE.findall(page_html or ""):
        seen.setdefault(slug.lower(), None)

    return list(seen)


def html_to_text(page_html: str) -> str:
    """Texto plano por líneas, sin scripts ni estilos."""

    text = re.sub(
        r"(?is)<(script|style|noscript)\b.*?</\1>",
        " ",
        page_html or ""
    )

    text = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr|ul|ol)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)

    lines = [
        re.sub(r"[ \t\r\f\v ]+", " ", line).strip()
        for line in text.split("\n")
    ]

    return "\n".join(line for line in lines if line)


def _field(text: str, label_pattern: str) -> Optional[str]:
    """Valor que sigue a una etiqueta del tipo '• AUTOR: valor'."""

    match = re.search(
        rf"(?:{label_pattern})\s*:\s*(.+?)"
        rf"(?=\s*(?:•|\n|(?:{_LABELS})\s*:)|$)",
        text,
        re.IGNORECASE | re.DOTALL,
    )

    if not match:
        return None

    value = match.group(1).strip(" \t•-–—")

    return value or None


def parse_ivrea_title(slug: str, page_html: str) -> dict[str, Any]:
    """
    Lee la ficha de una serie de Ivrea. Si no encuentra los tomos
    publicados, la marca como parse_ok=False en vez de inventarlos.
    """

    text = html_to_text(page_html)

    title = None

    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", page_html or "")

    if match:

        title = html.unescape(re.sub(r"\s+", " ", match.group(1))).strip()

        title = re.split(
            r"\s+[–—-]\s+(?:Editorial\s+)?Ivrea",
            title,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip()

    if not title:

        match = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", page_html or "")

        if match:
            title = html.unescape(
                re.sub(r"(?s)<[^>]+>", " ", match.group(1))
            ).strip()

    title_jp = _field(
        text,
        r"NOMBRE ORIGINAL (?:JAPON[ÉE]S|COREANO|CHINO)",
    )
    author = _field(text, r"AUTOR(?:ES|A)?")

    # Ivrea escribe "ESTADO EN ARGENTINA" en algunas fichas y solo
    # "ESTADO" en la mayoría (ahí el estado es el de la edición argentina).
    argentina = (
        _field(text, r"ESTADO EN ARGENTINA")
        or _field(text, r"ESTADO")
    )
    japan = _field(text, r"ESTADO EN JAP[ÓO]N")

    # "SERIE DE: 17 TOMOS" es el largo total de la serie.
    series_of = _field(text, r"SERIE DE")

    def split_state(raw: Optional[str]):

        if not raw:
            return None, None

        upper = raw.upper()

        volumes = None

        found = re.search(r"(\d+)\s*TOMOS?", upper)

        if found:
            volumes = int(found.group(1))

        elif re.search(r"TOMO\s+[ÚU]NICO", upper):
            volumes = 1

        # El estado es lo primero: "COMPLETA (3 TOMOS)" -> "Completa".
        status = re.split(r"\s*[(–—-]\s*", raw, maxsplit=1)[0].strip()

        # Si lo que quedó es solo un número, no es un estado.
        if not status or re.match(r"\d", status):
            status = None

        return (
            _normalize_status(status) if status else None,
            volumes,
        )

    status_ar, volumes_ar = split_state(argentina)
    status_jp, volumes_jp = split_state(japan)

    # Sin número en el estado: se usa "SERIE DE: N TOMOS" si la edición
    # está completa; si no, el total argentino no se conoce.
    if volumes_ar is None and series_of:

        found = re.search(r"(\d+)", series_of)

        if found and (status_ar or "").lower().startswith("complet"):
            volumes_ar = int(found.group(1))

    # Comics y libros: no tienen "ESTADO", solo "TOMO ÚNICO" suelto.
    if volumes_ar is None and status_ar is None:

        if re.search(r"(?im)^\s*•?\s*TOMO\s*\n?\s*[ÚU]NICO\s*$", text):
            volumes_ar = 1
            status_ar = "Completa"

    image = None

    # La portada de la ficha es la única imagen "vc_single_image-img".
    cover = re.search(
        r"""(?is)<img[^>]*class=["'][^"']*vc_single_image-img[^"']*["']"""
        r"""[^>]*>""",
        page_html or "",
    )

    if cover:

        source = re.search(r"""\ssrc=["']([^"']+)["']""", cover.group(0))

        if source:
            image = html.unescape(source.group(1))

    match = re.search(
        r"""(?is)<meta[^>]+property=["']og:image["'][^>]+"""
        r"""content=["']([^"']+)["']""",
        page_html or "",
    )

    if match and not image:
        image = html.unescape(match.group(1))

    return {
        "slug": slug,
        "url": f"{IVREA_BASE}/titulo/{slug}/",
        "title": title,
        "title_jp": title_jp,
        "author": author,
        "status_ar": status_ar,
        "volumes_ar": volumes_ar,
        "status_jp": status_jp,
        "volumes_jp": volumes_jp,
        "image": image,
        "parse_ok": volumes_ar is not None or status_ar is not None,
        # Para diagnosticar fichas que no se pudieron leer.
        "text_head": (
            None
            if (volumes_ar is not None or status_ar is not None)
            else text[:600]
        ),
        "parser": PARSER_VERSION,
        "fetched_at": time.time(),
    }



# --------------------------------
# Calendario de salidas de Ivrea
# --------------------------------

_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5,
    "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9,
    "octubre": 10, "noviembre": 11, "diciembre": 12,
}

_MONTH_NAMES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

_DATE_RE = re.compile(
    r"^\s*(\d{1,2})\s+DE\s+([A-ZÁÉÍÓÚÑ]+)\s*$", re.IGNORECASE
)
_MONTH_ONLY_RE = re.compile(r"^\s*([A-ZÁÉÍÓÚÑ]+)\s*$", re.IGNORECASE)
_RELEASE_RE = re.compile(r"^(.*?)\s*#\s*(\d+)\s*$", re.DOTALL)
_EDITION_RE = re.compile(r"\(([^)]*EDICI[ÓO]N[^)]*)\)", re.IGNORECASE)
_LINK_MARK_RE = re.compile(r"⟦([^⟧]+)⟧")


def _month_number(word: str) -> Optional[int]:

    plain = (
        unicodedata.normalize("NFKD", word.lower())
        .encode("ascii", "ignore").decode()
    )

    if plain == "setiembre":
        plain = "septiembre"

    plain_months = [
        unicodedata.normalize("NFKD", m).encode("ascii", "ignore").decode()
        for m in _MONTH_NAMES
    ]

    return plain_months.index(plain) + 1 if plain in plain_months else None


def _guess_date(day: int, month: int, today: date) -> Optional[date]:
    """El calendario no trae el año: se toma el más cercano a hoy."""

    try:
        found = date(today.year, month, day)
    except ValueError:
        return None

    # Más de ~7 meses en el pasado: es del año que viene.
    if (found - today).days < -200:
        found = found.replace(year=today.year + 1)

    return found


def _calendar_lines(page_html: str) -> list[str]:
    """Texto por líneas; los enlaces a fichas quedan marcados ⟦slug⟧."""

    text = re.sub(
        r"(?is)<(script|style|noscript)\b.*?</\1>", " ", page_html or ""
    )

    text = re.sub(
        r"""(?is)<a\b[^>]*href=["'][^"']*/titulo/([A-Za-z0-9_%.\-]+)/?["'][^>]*>""",
        lambda m: f" ⟦{m.group(1).lower()}⟧ ",
        text,
    )

    text = re.sub(
        r"(?i)<br\s*/?>|</?(p|div|li|h[1-6]|tr|ul|ol|section|article)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)

    lines = [
        re.sub(r"[ \t\r\f\v\u00a0]+", " ", line).strip()
        for line in text.split("\n")
    ]

    return [line for line in lines if line]


def parse_ivrea_calendar(
    page_html: str, today: Optional[date] = None
) -> list[dict]:
    """
    Lee el calendario de salidas de Ivrea (/proximas/).

    Estructura: encabezados de fecha ("30 DE OCTUBRE" o solo "DICIEMBRE"),
    debajo los lanzamientos ("DANDADAN #24", "ICHI THE WITCH (EDICIÓN
    ESPECIAL LIMITADA) #1") con enlace a su ficha, y una sección
    "REEDICIONES POR TOMO AGOTADO" con los tomos que vuelven a imprimirse.
    """

    today = today or date.today()

    events: list[dict] = []
    section: Optional[dict] = None
    reprint = False
    last: Optional[dict] = None

    for raw in _calendar_lines(page_html):

        slugs = _LINK_MARK_RE.findall(raw)

        line = _LINK_MARK_RE.sub("", raw).strip()
        line = re.sub(r"^null\s+", "", line, flags=re.IGNORECASE).strip()

        if not line:

            # Enlace suelto ("MÁS INFO"): pertenece al último lanzamiento.
            if slugs and last and not last["slug"]:
                last["slug"] = slugs[0]

            continue

        upper = line.upper()

        found = _DATE_RE.match(line)

        day_month = _month_number(found.group(2)) if found else None

        if found and day_month:

            day_date = _guess_date(int(found.group(1)), day_month, today)

            section = (
                {"date": day_date, "precision": "day"} if day_date else None
            )
            reprint = False
            last = None

            continue

        found = _MONTH_ONLY_RE.match(line)

        only_month = _month_number(found.group(1)) if found else None

        if found and only_month:

            month_date = _guess_date(1, only_month, today)

            section = (
                {"date": month_date, "precision": "month"}
                if month_date else None
            )
            reprint = False
            last = None

            continue

        if "REEDICION" in unicodedata.normalize("NFKD", upper).encode(
            "ascii", "ignore"
        ).decode().upper() and len(line) < 90 and not _RELEASE_RE.match(line):

            reprint = True
            last = None

            continue

        release = _RELEASE_RE.match(line)

        if release and section:

            title = release.group(1)

            edition = None

            edition_match = _EDITION_RE.search(title)

            if edition_match:
                edition = edition_match.group(1).strip().capitalize()
                title = _EDITION_RE.sub(" ", title)

            title = re.sub(r"\s+", " ", title).strip(" -–—:")

            if not title:
                continue

            last = {
                "slug": slugs[0] if slugs else None,
                "title": title,
                "volume": int(release.group(2)),
                "kind": (
                    "reedicion" if reprint
                    else "especial" if edition
                    else "tomo"
                ),
                "edition": edition,
                "date": section["date"].isoformat(),
                "precision": section["precision"],
                "last": False,
                "new": False,
            }

            events.append(last)

            continue

        # "NUEVA SERIE - MUY PRONTO ANUNCIO": un recuadro sin datos.
        if "PRONTO ANUNCIO" in upper:
            last = None
            continue

        # Etiquetas del lanzamiento anterior ("¡ÚLTIMO TOMO!").
        if last:

            if "ÚLTIMO TOMO" in upper or "ULTIMO TOMO" in upper:
                last["last"] = True

            if "NUEVA SERIE" in upper and len(line) < 40:
                last["new"] = True

            if slugs and not last["slug"]:
                last["slug"] = slugs[0]

    # Sin repetidos (el mismo calendario puede estar en dos páginas).
    unique: dict[tuple, dict] = {}

    for event in events:

        key = (
            normalize_title(event["title"]), event["volume"],
            event["kind"], event["date"],
        )

        unique.setdefault(key, event)

    return list(unique.values())


def format_date_es(iso: str, precision: str = "day") -> str:
    """'2026-10-30' -> '30 de octubre'; por mes: 'diciembre'."""

    try:
        day = date.fromisoformat(iso)
    except ValueError:
        return ""

    month = _MONTH_NAMES[day.month - 1]

    return month if precision == "month" else f"{day.day} de {month}"


# --------------------------------
# Catálogo
# --------------------------------

class ArgentinaCatalog:

    def __init__(self):

        self.lock = threading.RLock()

        self.stacks: dict[int, dict] = {}
        self.stacks_updated_at = 0.0

        self.ivrea: dict[str, dict] = {}
        self.ivrea_updated_at = 0.0
        self.links: dict[int, str] = {}
        self.covers: dict[int, str] = {}
        self.calendar: list[dict] = []
        self.calendar_updated_at = 0.0
        self._calendar_busy = False

        self._owns_job = False
        self._last_reload_check = 0.0
        self._last_state_save = 0.0
        self._background: list[threading.Thread] = []

        self._calendar_index: dict[str, list[dict]] = {}

        self._ivrea_index: dict[str, Optional[str]] = {}

        self.state = {
            "running": False,
            "phase": "idle",
            "message": "",
            "done": 0,
            "total": 0,
            "errors": 0,
            "last_error": None,
            "finished_at": 0.0,
        }

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

        self._last_request = {"tenrai": 0.0, "ivrea": 0.0}

        self.load()

    # --------------------------------
    # Persistencia
    # --------------------------------

    def load(self) -> None:

        with self.lock:

            store.ready()

            self.stacks, self.stacks_updated_at = store.load_stacks()

            self.ivrea, self.links, self.ivrea_updated_at = (
                store.load_ivrea()
            )

            self.calendar, self.calendar_updated_at = store.load_calendar()

            self.covers = store.load_covers()

            self._rebuild_ivrea_index()

    # Los catálogos compartidos se pueden volver a descargar: si guardar
    # falla (por ejemplo, se cayó la base) se registra el error y la
    # actualización sigue en memoria en lugar de cortarse a la mitad.

    def set_cover(self, mal_id: int, url: Optional[str]) -> None:

        with self.lock:

            store.set_cover(mal_id, url)

            self.covers[int(mal_id)] = url or ""

    def _save_stacks(self) -> None:

        try:
            store.save_stacks(self.stacks, self.stacks_updated_at)
        except Exception:
            log.exception("No se pudo guardar el catálogo de stacks")

    def _save_ivrea(self) -> None:

        try:
            store.save_ivrea(self.ivrea, self.links, self.ivrea_updated_at)
        except Exception:
            log.exception("No se pudo guardar el catálogo de Ivrea")

    def _rebuild_ivrea_index(self) -> None:
        """
        Título normalizado -> slug.

        Si varias fichas comparten título (por ejemplo "One Piece" y sus
        ediciones 3 en 1) se elige la principal: la que tiene ese título
        exacto, o la que más se parece al nombre de la serie. Si sigue sin
        haber una clara, el título no se usa para emparejar.
        """

        candidates: dict[str, set[str]] = {}

        for slug, entry in self.ivrea.items():

            for key in title_keys(entry.get("title_jp"), entry.get("title")):
                candidates.setdefault(key, set()).add(slug)

        index: dict[str, Optional[str]] = {}

        for key, slugs in candidates.items():

            if len(slugs) == 1:
                index[key] = next(iter(slugs))
                continue

            # 1) la ficha cuyo slug es justo el nombre ("one-piece")
            exact_slug = [
                s for s in slugs if re.sub(r"[^a-z0-9]", "", s) == key
            ]

            if len(exact_slug) == 1:
                index[key] = exact_slug[0]
                continue

            # 2) la única ficha cuyo título en español es justo ese nombre
            exact_title = [
                s for s in slugs
                if normalize_title(self.ivrea[s].get("title")) == key
            ]

            index[key] = exact_title[0] if len(exact_title) == 1 else None

        self._ivrea_index = index

        self._rebuild_calendar_index()

    # --------------------------------
    # HTTP
    # --------------------------------

    def _throttle(self, kind: str, interval: float) -> None:

        elapsed = time.time() - self._last_request[kind]

        if elapsed < interval:
            time.sleep(interval - elapsed)

        self._last_request[kind] = time.time()

    def _get(self, kind: str, url: str, interval: float, **kwargs):
        """GET con pausa entre pedidos y reintento ante HTTP 429."""

        last = None

        for attempt in range(3):

            self._throttle(kind, interval)

            try:
                response = self.session.get(url, timeout=30, **kwargs)

            except requests.ConnectionError:
                # Cortes de conexión sueltos: se reintenta antes de fallar.
                if attempt == 2:
                    raise

                time.sleep(2 * (attempt + 1))
                continue

            last = response

            if response.status_code != 429:
                return response

            try:
                wait = float(response.headers.get("Retry-After", ""))
            except ValueError:
                wait = 3 * (attempt + 1)

            time.sleep(min(wait, 30))

        return last

    # --------------------------------
    # Actualización en segundo plano
    # --------------------------------

    def is_running(self) -> bool:
        """¿Hay una actualización en curso (en este proceso o en otro)?"""

        with self.lock:

            if self.state["running"]:
                return True

        return self._remote_running()

    def _remote_running(self) -> bool:
        """
        ¿Otro proceso (por ejemplo `python -m tomo.jobs refresh`) está
        actualizando? Se sabe por el estado que va dejando en la base: si
        dejó de dar señales de vida, se lo da por caído.
        """

        try:
            job = store.load_job_state()

        except Exception:
            return False

        return bool(job.get("running")) and (
            time.time() - float(job.get("heartbeat") or 0) < JOB_STALE_SECONDS
        )

    def _job_state(self) -> dict:
        """El estado de la actualización, venga de este proceso o de otro."""

        with self.lock:

            local = dict(self.state)

            if self._owns_job or local["running"]:
                return local

        try:
            remote = store.load_job_state()

        except Exception:
            return local

        if not remote:
            return local

        heartbeat = float(remote.get("heartbeat") or 0)

        running = (
            bool(remote.get("running"))
            and time.time() - heartbeat < JOB_STALE_SECONDS
        )

        merged = {**local, **remote, "running": running}

        merged.pop("heartbeat", None)

        if not running:
            merged["phase"] = "idle"

        return merged

    def _set_state(self, **values) -> None:

        with self.lock:

            self.state.update(values)

            snapshot = dict(self.state)

            owner = self._owns_job

        # Solo quien está actualizando deja su avance en la base; si no,
        # pisaría el estado de otro proceso.
        if owner:
            self._save_state(snapshot, force="running" in values)

    def _save_state(self, snapshot: dict, force: bool = False) -> None:

        now = time.time()

        if not force and now - self._last_state_save < STATE_SAVE_INTERVAL:
            return

        self._last_state_save = now

        try:
            store.save_job_state({**snapshot, "heartbeat": now})

        except Exception:
            log.exception("No se pudo guardar el avance de la actualización")

    def _heartbeat(self, stop: threading.Event) -> None:
        """Mientras dura la actualización, avisa cada tanto que sigue viva."""

        while not stop.wait(HEARTBEAT_SECONDS):

            with self.lock:
                snapshot = dict(self.state)

            self._save_state(snapshot, force=True)

    def _begin(self) -> bool:
        """Marca el inicio de una actualización en este proceso."""

        with self.lock:

            if self.state["running"]:
                return False

            self.state.update({
                "running": True,
                "phase": "starting",
                "message": "Preparando la actualización...",
                "done": 0,
                "total": 0,
                "errors": 0,
                "last_error": None,
            })

            self._owns_job = True

        return True

    def start_refresh(self, force: bool = False) -> bool:
        """
        Arranca la actualización en segundo plano. False si ya hay una en
        curso, en este proceso o en otro.
        """

        if self._remote_running():
            return False

        if not self._begin():
            return False

        thread = threading.Thread(
            target=self.run_refresh,
            args=(force, True),
            daemon=True,
        )

        thread.start()

        return True

    def run_refresh(self, force: bool = False, begun: bool = False) -> bool:
        """
        Actualiza todo el catálogo y espera a que termine. Con el candado
        de la base, nunca corren dos a la vez aunque se lancen desde
        procesos distintos. False si no pudo arrancar.
        """

        if not begun and not self._begin():
            return False

        try:

            with db.advisory_lock(REFRESH_LOCK_ID) as obtained:

                if not obtained:

                    with self.lock:

                        self.state.update(
                            running=False,
                            phase="idle",
                            message="Ya hay otra actualización en curso.",
                        )

                        self._owns_job = False

                    return False

                self._save_state(dict(self.state), force=True)

                stop = threading.Event()

                threading.Thread(
                    target=self._heartbeat, args=(stop,), daemon=True
                ).start()

                try:

                    self._set_state(
                        phase="calendar",
                        message="Calendario de salidas de Ivrea...",
                    )

                    self.refresh_calendar(force=True)
                    self._refresh_stacks(force)
                    self._refresh_ivrea(force)

                except Exception as error:      # nunca dejar el estado colgado

                    self._set_state(last_error=f"Error inesperado: {error}")

                finally:

                    stop.set()

                    self._join_background()

                    self._set_state(
                        running=False,
                        phase="idle",
                        message="Catálogo actualizado.",
                        finished_at=time.time(),
                    )

                    with self.lock:
                        self._owns_job = False

            return True

        except Exception as error:      # p. ej. se cayó la base

            log.exception("No se pudo actualizar el catálogo")

            with self.lock:

                self.state.update(
                    running=False,
                    phase="idle",
                    last_error=f"No se pudo actualizar: {error}",
                )

                self._owns_job = False

            return False

    def _join_background(self) -> None:
        """Espera las tareas cortas que dejó el calendario (fichas de tomos salidos)."""

        for thread in list(self._background):
            thread.join(timeout=120)

        self._background.clear()

    def reload_if_changed(self, force: bool = False) -> bool:
        """
        Si otro proceso actualizó el catálogo en la base, lo vuelve a
        cargar. Se consulta como máximo cada pocos segundos.
        """

        now = time.time()

        with self.lock:

            # Quien está actualizando es quien manda: su memoria es la nueva.
            if self._owns_job:
                return False

            if not force and now - self._last_reload_check < RELOAD_CHECK_INTERVAL:
                return False

            self._last_reload_check = now

            known = (
                self.stacks_updated_at,
                self.ivrea_updated_at,
                self.calendar_updated_at,
            )

        try:
            current = store.load_catalog_versions()

        except Exception:
            return False

        if current == known:
            return False

        self.load()

        return True

    # ---- stacks de MAL

    def stacks_stale(self) -> bool:

        return (
            not self.stacks
            or time.time() - self.stacks_updated_at > STACKS_TTL
        )

    def _refresh_stacks(self, force: bool) -> None:

        if not force and not self.stacks_stale():
            return

        total = len(ARGENTINA_STACK_IDS)

        self._set_state(phase="stacks", done=0, total=total)

        fresh: dict[int, dict] = {}
        failures = 0
        consecutive = 0

        for index, stack_id in enumerate(ARGENTINA_STACK_IDS, start=1):

            self._set_state(
                done=index - 1,
                message=f"Listas de MyAnimeList: {index} de {total}",
            )

            try:

                response = self._get(
                    "tenrai",
                    f"{JIKAN_BASE_URL}/stacks/{stack_id}",
                    REQUEST_INTERVAL,
                )

                if response is None or response.status_code != 200:
                    raise ArgentinaCatalogError(
                        f"HTTP {getattr(response, 'status_code', '?')}"
                    )

                entries = (
                    (response.json().get("data") or {}).get("entries") or []
                )

                consecutive = 0

            except (ArgentinaCatalogError, requests.RequestException,
                    ValueError) as error:

                failures += 1
                consecutive += 1

                self._set_state(
                    errors=self.state["errors"] + 1,
                    last_error=f"Lista de MAL {stack_id}: {error}",
                )

                if consecutive >= MAX_CONSECUTIVE_FAILURES:
                    break

                continue

            for entry in entries:

                mal_id = entry.get("mal_id")

                if not mal_id:
                    continue

                parsed = _parse_note(entry.get("note") or "")

                record = {
                    "mal_id": int(mal_id),
                    "title": entry.get("title") or "",
                    "title_english": entry.get("title_english"),
                    "title_local": parsed["title_local"],
                    "publisher": _normalize_publisher(parsed["publisher"]),
                    "status": parsed["status"],
                    "volumes_ar": parsed["volumes_ar"],
                    "image": (
                        ((entry.get("images") or {}).get("jpg") or {})
                        .get("image_url")
                    ),
                    "url": entry.get("url"),
                }

                existing = fresh.get(int(mal_id))

                if (
                    existing is None
                    or (record["volumes_ar"] or 0)
                    >= (existing.get("volumes_ar") or 0)
                ):
                    fresh[int(mal_id)] = record

        # Si falló todo, se conserva lo que ya había.
        if fresh and failures < total:

            with self.lock:

                self.stacks = fresh
                self.stacks_updated_at = time.time()

                self._save_stacks()

    # ---- Calendario de salidas

    def refresh_calendar(self, force: bool = False) -> bool:
        """Baja el calendario de salidas de Ivrea. True si se actualizó."""

        with self.lock:

            if self._calendar_busy:
                return False

            fresh = (
                self.calendar_updated_at
                and time.time() - self.calendar_updated_at < CALENDAR_TTL
            )

            if fresh and not force:
                return False

            self._calendar_busy = True

        try:

            collected: list[dict] = []
            failure: Optional[str] = None

            for path in CALENDAR_URL_PATHS:

                try:

                    response = self._get(
                        "ivrea", f"{IVREA_BASE}{path}", IVREA_INTERVAL
                    )

                except requests.RequestException as error:
                    failure = str(error)
                    continue

                if response is None or response.status_code != 200:
                    failure = f"HTTP {getattr(response, 'status_code', '?')}"
                    continue

                # Se guarda siempre, para poder revisar el formato real.
                name = "calendario" if path == CALENDAR_URL_PATHS[0] else (
                    "calendario_2"
                )

                self._dump_debug(name, response.text, force=True)

                collected.extend(parse_ivrea_calendar(response.text))

            merged: dict[tuple, dict] = {}

            for event in collected:

                key = (
                    normalize_title(event["title"]), event["volume"],
                    event["kind"], event["date"],
                )

                merged.setdefault(key, event)

            entries = list(merged.values())

            if not entries:
                raise ArgentinaCatalogError(
                    "No pude leer el calendario de salidas"
                    + (f" ({failure})" if failure else "")
                    + "."
                )

            with self.lock:

                self.calendar = entries
                self.calendar_updated_at = time.time()
                self._rebuild_calendar_index()

                try:
                    store.save_calendar(entries, self.calendar_updated_at)
                except Exception:
                    log.exception("No se pudo guardar el calendario")

            self._set_state(calendar_error=None)

            released = threading.Thread(
                target=self._refresh_released_pages, daemon=True
            )

            self._background.append(released)

            released.start()

            return True

        except ArgentinaCatalogError as error:

            self._set_state(calendar_error=str(error))

            return False

        finally:

            with self.lock:
                self._calendar_busy = False

    def _rebuild_calendar_index(self) -> None:
        """slug de Ivrea -> lanzamientos del calendario de esa serie."""

        index: dict[str, list[dict]] = {}

        for event in self.calendar:

            slug = None

            # El título del encabezado es más confiable que el enlace.
            for key in title_keys(event.get("title")):

                found = self._ivrea_index.get(key)

                if found:
                    slug = found
                    break

            slug = slug or (event.get("slug") or None)

            if slug and slug in self.ivrea:
                index.setdefault(slug, []).append(event)

        self._calendar_index = index

    def released_volume(self, slug: Optional[str]) -> Optional[int]:
        """Tomo más alto del calendario con fecha de hoy o anterior."""

        if not slug:
            return None

        today = date.today()

        with self.lock:
            events = list(self._calendar_index.get(slug, []))

        volumes = []

        for event in events:

            if event.get("kind") != "tomo" or event.get("precision") != "day":
                continue

            try:
                when = date.fromisoformat(event["date"])
            except (KeyError, ValueError):
                continue

            if when <= today:
                volumes.append(int(event["volume"]))

        return max(volumes) if volumes else None

    def _refresh_released_pages(self, limit: int = 8) -> None:
        """
        Vuelve a leer las fichas de series cuyo calendario ya muestra un
        tomo salido que la ficha guardada no cuenta (pocas y de a una).
        """

        with self.lock:

            stale = [
                slug for slug in self._calendar_index
                if slug in self.ivrea
                and (self.released_volume(slug) or 0)
                > (self.ivrea[slug].get("volumes_ar") or 0)
            ][:limit]

        changed = False

        for slug in stale:

            try:

                response = self._get(
                    "ivrea", f"{IVREA_BASE}/titulo/{slug}/", IVREA_INTERVAL
                )

            except requests.RequestException:
                continue

            if response is None or response.status_code != 200:
                continue

            entry = parse_ivrea_title(slug, response.text)

            if not entry.get("parse_ok"):
                continue

            with self.lock:
                self.ivrea[slug] = entry

            changed = True

        if changed:

            with self.lock:
                self.ivrea_updated_at = time.time()
                self._rebuild_ivrea_index()
                self._save_ivrea()

    def upcoming_for(self, slug: Optional[str], limit: int = 8) -> list[dict]:
        """Próximas salidas (tomos, ediciones especiales y reediciones)."""

        if not slug:
            return []

        today = date.today()

        with self.lock:
            events = list(self._calendar_index.get(slug, []))

        result = []

        for event in events:

            try:
                when = date.fromisoformat(event["date"])
            except (KeyError, ValueError):
                continue

            month_only = event.get("precision") == "month"

            if month_only:
                if (when.year, when.month) < (today.year, today.month):
                    continue
            elif when < today:
                continue

            result.append((when, event, month_only))

        # Por fecha; las que solo traen el mes van después de ese mes.
        result.sort(key=lambda r: (r[0].year, r[0].month, r[2], r[0].day,
                                   r[1]["volume"]))

        items = []

        for when, event, month_only in result[:limit]:

            items.append({
                "kind": event["kind"],
                "volume": event["volume"],
                "edition": event.get("edition"),
                "date": event["date"],
                "date_label": format_date_es(event["date"], event["precision"]),
                "days_left": None if month_only else (when - today).days,
                "last": bool(event.get("last")),
                "new": bool(event.get("new")),
            })

        return items

    # ---- Ivrea

    def _dump_debug(
        self, slug: str, page_html: str, force: bool = False
    ) -> None:
        """Guarda el HTML de unas pocas fichas ilegibles para revisarlas."""

        try:

            DEBUG_DIR.mkdir(parents=True, exist_ok=True)

            if (
                not force
                and len(list(DEBUG_DIR.glob("*.html"))) >= MAX_DEBUG_PAGES
            ):
                return

            safe = re.sub(r"[^A-Za-z0-9_.-]", "_", slug)[:100]

            (DEBUG_DIR / f"{safe}.html").write_text(
                page_html, encoding="utf-8", errors="replace"
            )

        except OSError:
            pass

    def write_debug_summary(self) -> Optional[str]:
        """Resumen de texto de las fichas ilegibles (data/ivrea_debug)."""

        with self.lock:

            lines = []

            for slug, entry in self.ivrea.items():

                if entry.get("parse_ok"):
                    continue

                lines.append(f"=== {slug}")
                lines.append(f"título: {entry.get('title')}")
                lines.append((entry.get("text_head") or "(sin texto)"))
                lines.append("")

        if not lines:
            return None

        try:

            DEBUG_DIR.mkdir(parents=True, exist_ok=True)

            path = DEBUG_DIR / "resumen.txt"

            path.write_text("\n".join(lines), encoding="utf-8")

            return str(path)

        except OSError:
            return None

    def ivrea_needs_fetch(self, slug: str, force: bool) -> bool:

        entry = self.ivrea.get(slug)

        if entry is None or force:
            return True

        if entry.get("parser") != PARSER_VERSION:
            return True

        age = time.time() - float(entry.get("fetched_at", 0) or 0)

        if not entry.get("parse_ok"):
            # Las que se guardaron sin diagnóstico se vuelven a leer una vez.
            return age > IVREA_RETRY_TTL

        completed = (entry.get("status_ar") or "").lower().startswith("complet")

        return (not completed) and age > IVREA_TTL

    def _collect_ivrea_slugs(self) -> list[str]:

        slugs: dict[str, None] = {}
        failures = 0

        for path in IVREA_INDEX_PATHS:

            try:

                response = self._get(
                    "ivrea",
                    f"{IVREA_BASE}{path}",
                    IVREA_INTERVAL,
                )

                if response is None or response.status_code != 200:
                    raise ArgentinaCatalogError(
                        f"HTTP {getattr(response, 'status_code', '?')}"
                    )

                for slug in extract_slugs(response.text):
                    slugs.setdefault(slug, None)

            except (ArgentinaCatalogError, requests.RequestException) as error:

                failures += 1

                self._set_state(
                    errors=self.state["errors"] + 1,
                    last_error=f"Ivrea {path}: {error}",
                )

        if not slugs and failures:
            raise ArgentinaCatalogError(
                "No se pudo leer el catálogo de Ivrea."
            )

        return list(slugs)

    def _refresh_ivrea(self, force: bool) -> None:

        self._set_state(
            phase="ivrea_index",
            message="Buscando series en el sitio de Ivrea...",
            done=0,
            total=0,
        )

        try:
            slugs = self._collect_ivrea_slugs()
        except ArgentinaCatalogError as error:
            self._set_state(last_error=str(error))
            return

        pending = [s for s in slugs if self.ivrea_needs_fetch(s, force)]
        total = len(pending)

        self._set_state(phase="ivrea_titles", done=0, total=total)

        consecutive = 0

        for index, slug in enumerate(pending, start=1):

            self._set_state(
                done=index - 1,
                message=f"Series de Ivrea: {index} de {total}",
            )

            try:

                response = self._get(
                    "ivrea",
                    f"{IVREA_BASE}/titulo/{slug}/",
                    IVREA_INTERVAL,
                )

                if response is None or response.status_code != 200:
                    raise ArgentinaCatalogError(
                        f"HTTP {getattr(response, 'status_code', '?')}"
                    )

                entry = parse_ivrea_title(slug, response.text)

                if not entry["parse_ok"]:
                    self._dump_debug(slug, response.text)

                consecutive = 0

            except (ArgentinaCatalogError, requests.RequestException) as error:

                consecutive += 1

                self._set_state(
                    errors=self.state["errors"] + 1,
                    last_error=f"Ivrea {slug}: {error}",
                )

                if consecutive >= MAX_CONSECUTIVE_FAILURES:
                    break

                continue

            with self.lock:
                self.ivrea[slug] = entry

            # Guardado parcial: si se corta, no se pierde lo avanzado.
            if index % 25 == 0:

                with self.lock:

                    self.ivrea_updated_at = time.time()
                    self._rebuild_ivrea_index()
                    self._save_ivrea()

        with self.lock:

            self.ivrea_updated_at = time.time()
            self._rebuild_ivrea_index()
            self._save_ivrea()

        self.write_debug_summary()

    # --------------------------------
    # Consultas
    # --------------------------------

    def resolve(
        self,
        mal_id: int,
        titles: Optional[list[str]] = None,
        manual: Optional[dict[int, dict]] = None,
    ) -> Optional[dict]:
        """
        Datos de edición argentina de un manga, o None si no se sabe
        que se publique. titles sirve para emparejar con Ivrea.
        manual son las correcciones del usuario (mal_id -> datos):
        tienen prioridad sobre las fuentes automáticas.
        """

        mal_id = int(mal_id)

        with self.lock:

            # "manual" trae las correcciones de todas las series; acá se
            # toma la de esta (con otro nombre, para no pisar el parámetro).
            correction = (manual or {}).get(mal_id)

            if correction and correction.get("hidden"):
                return None

            if correction:
                return {
                    "mal_id": mal_id,
                    "title": correction.get("title"),
                    "publisher": _normalize_publisher(correction.get("publisher")),
                    "volumes_ar": correction.get("volumes_ar"),
                    "ar_status": _normalize_status(correction.get("status")),
                    "image": correction.get("image") or self.covers.get(mal_id) or None,
                    "url": None,
                    "sources": ["manual"],
                }

            stack = self.stacks.get(mal_id)
            ivrea = None

            slug = self.links.get(mal_id)

            if slug and slug in self.ivrea:
                ivrea = self.ivrea[slug]

            elif titles:

                candidates: set[str] = {
                    found
                    for key in title_keys(*titles)
                    if (found := self._ivrea_index.get(key))
                }

                if stack:
                    candidates |= {
                        found
                        for key in title_keys(
                            stack.get("title"),
                            stack.get("title_english"),
                        )
                        if (found := self._ivrea_index.get(key))
                    }

                if len(candidates) == 1:

                    slug = next(iter(candidates))
                    ivrea = self.ivrea.get(slug)

                    if ivrea:
                        self.links[mal_id] = slug

            if not stack and not ivrea:
                return None

            sources = []

            if ivrea:
                sources.append("ivrea")

            if stack:
                sources.append("stacks")

            volumes = None
            status = None

            if ivrea and ivrea.get("volumes_ar") is not None:
                volumes = ivrea["volumes_ar"]
                status = ivrea.get("status_ar")

            if volumes is None and stack:
                volumes = stack.get("volumes_ar")

            if status is None and stack:
                status = stack.get("status")

            # Si el calendario dice que un tomo ya salió (hoy o antes) y la
            # ficha guardada todavía no lo cuenta, vale el del calendario.
            released = self.released_volume((ivrea or {}).get("slug"))

            if released and (volumes is None or released > volumes):
                volumes = released

            return {
                "upcoming": self.upcoming_for((ivrea or {}).get("slug")),
                "mal_id": mal_id,
                "title": (
                    (stack or {}).get("title")
                    or (ivrea or {}).get("title")
                ),
                "publisher": (
                    "Ivrea" if ivrea
                    else _normalize_publisher((stack or {}).get("publisher"))
                ),
                "volumes_ar": volumes,
                "ar_status": _normalize_status(status),
                "image": (
                    (stack or {}).get("image")
                    or (ivrea or {}).get("image")
                    or self.covers.get(mal_id)
                    or None
                ),
                "url": (ivrea or {}).get("url"),
                "sources": sources,
            }

    def match_list(
        self,
        mangas: list[dict],
        manual: Optional[dict[int, dict]] = None,
    ) -> tuple[list[dict], list[dict]]:
        """
        Separa la lista del usuario en (publicados en Argentina,
        resto). Cada manga trae mal_id y title.
        """

        self.reload_if_changed()

        found = []
        rest = []

        links_before = dict(self.links)

        for manga in mangas:

            info = self.resolve(
                manga["mal_id"],
                [manga["title"]] if manga.get("title") else None,
                manual,
            )

            if info:
                found.append({**info, "list_title": manga.get("title")})
            else:
                rest.append(manga)

        if self.links != links_before:

            with self.lock:
                self._save_ivrea()

        return found, rest

    # --------------------------------
    # Estado
    # --------------------------------

    def status(self) -> dict:
        """Estado del catálogo compartido (igual para todos)."""

        self.reload_if_changed()

        job = self._job_state()

        with self.lock:

            unread = sum(
                1 for entry in self.ivrea.values()
                if not entry.get("parse_ok")
            )

            return {
                "stacks_count": len(self.stacks),
                "stacks_updated_at": self.stacks_updated_at,
                "ivrea_count": len(self.ivrea),
                "ivrea_unread": unread,
                "ivrea_updated_at": self.ivrea_updated_at,
                "calendar_count": len(self.calendar),
                "calendar_error": job.get("calendar_error"),
                "calendar_updated_at": self.calendar_updated_at,
                "empty": not self.stacks and not self.ivrea,
                **{f"job_{k}": v for k, v in job.items()},
            }


# Instancia global
argentina_catalog = ArgentinaCatalog()