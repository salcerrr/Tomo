import gzip
import os
import socket
import sys
import threading
import time
import uuid

from collections import OrderedDict

from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    Response
)

from werkzeug.utils import secure_filename

# La base tiene que estar lista ANTES de importar el resto de los módulos:
# jikan_client, argentina_catalog y library cargan sus datos al importarse.
from tomo import db
from tomo import store

try:
    store.ready()
except db.DatabaseError as error:
    sys.exit(f"\n{error}\n")

from tomo.sources.manga_list import (
    load_mal_list_bytes,
    MangaListError
)

from tomo.sources.jikan_client import (
    jikan,
    JikanError
)

from tomo.sources.argentina_catalog import argentina_catalog

from tomo.library import library
from tomo.config import BASE_DIR

from tomo import auth
from tomo.auth import (
    admin_required,
    current_user,
    is_admin,
    login_required,
    user_id,
)


# Las plantillas y los archivos estáticos viven en la raíz del proyecto,
# no dentro del paquete.
app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)

# Detrás de un proxy (Render, nginx...) todos los pedidos parecen venir de la
# IP del proxy, y los límites de intentos por IP se compartirían entre todos.
# TOMO_PROXY_HOPS = cuántos proxies hay delante (1 en Render). Con 0 (por
# defecto, desarrollo local) no se confía en esos encabezados.
try:
    proxy_hops = int(os.environ.get("TOMO_PROXY_HOPS", "0") or 0)
except ValueError:
    proxy_hops = 0

if proxy_hops > 0:

    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=proxy_hops,
        x_proto=proxy_hops,
        x_host=proxy_hops,
    )

# Que el navegador siempre pida los archivos de static/ de nuevo,
# así no se queda con un CSS o JS viejo.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024

auth.init_app(app, auth.load_secret_key())

# Listas de MyAnimeList ya leídas, una por usuario. Se guardan solo las
# últimas usadas para que la memoria no crezca con la cantidad de cuentas.
MAX_CACHED_LISTS = 50

user_lists = OrderedDict()
user_lists_lock = threading.Lock()


def remember_list(uid, parsed):

    with user_lists_lock:

        user_lists[uid] = parsed
        user_lists.move_to_end(uid)

        while len(user_lists) > MAX_CACHED_LISTS:
            user_lists.popitem(last=False)

# Cada cuánto, como mínimo, un usuario común puede pedir que se actualice
# el catálogo compartido (el administrador no tiene límite).
REFRESH_COOLDOWN = 3600

discovery_jobs = {}
discovery_jobs_lock = threading.Lock()

# Búsquedas de Descubrir que pueden correr a la vez en todo el servidor
# (cada una hace muchas consultas a la API de Tenrai).
MAX_RUNNING_JOBS = 5


def get_list():
    """La lista de MyAnimeList del usuario de este pedido."""

    uid = user_id()

    with user_lists_lock:

        cached = user_lists.get(uid)

        if cached is not None:
            user_lists.move_to_end(uid)

    if cached is not None:
        return cached

    raw = store.load_mal_export(uid)

    if raw is None:

        raise MangaListError(
            "Todavía no subiste tu exportación de MyAnimeList."
        )

    parsed = load_mal_list_bytes(raw)

    remember_list(uid, parsed)

    return parsed


def catalog_status():
    """Estado del catálogo compartido + los conteos personales."""

    manual = store.load_manual(user_id())

    return {
        **argentina_catalog.status(),
        "manual_count": sum(
            1 for e in manual.values() if not e.get("hidden")
        ),
        "hidden_count": sum(
            1 for e in manual.values() if e.get("hidden")
        ),
    }


def format_manga(manga):

    images = (
        manga.get("images", {})
        .get("jpg", {})
    )

    published = manga.get(
        "published",
        {}
    )

    return {
        "mal_id": manga.get("mal_id"),
        "title": manga.get("title"),
        "title_english": manga.get("title_english"),
        "url": manga.get("url"),

        "image": (
            images.get("large_image_url")
            or images.get("image_url")
        ),

        "score": manga.get("score"),
        "scored_by": manga.get("scored_by"),
        "members": manga.get("members"),
        "chapters": manga.get("chapters"),
        "volumes": manga.get("volumes"),
        "synopsis": manga.get("synopsis"),
        "published": published.get("string"),
        "status": manga.get("status"),

        "genres": [
            item.get("name")
            for item in manga.get("genres", [])
            if item.get("name")
        ],

        "themes": [
            item.get("name")
            for item in manga.get("themes", [])
            if item.get("name")
        ],

        # Información del foro: se completa en la segunda etapa.
        "forum_status": "pending",
        "latest_chapter": None,
        "chapter_date": None,
        "chapter_url": None,
        "chapter_title": None
    }


def update_job(job_id, values):

    with discovery_jobs_lock:

        job = discovery_jobs.get(job_id)

        if job:
            job.update(values)


def add_job_section(job_id, section):

    with discovery_jobs_lock:

        job = discovery_jobs.get(job_id)

        if job:

            job["sections"].append(section)

            job["completed_count"] = len(
                job["sections"]
            )


def clean_old_jobs():

    cutoff = time.time() - 3600

    with discovery_jobs_lock:

        expired = [
            job_id
            for job_id, job in discovery_jobs.items()
            if job.get("created_at", 0) < cutoff
        ]

        for job_id in expired:
            del discovery_jobs[job_id]


def run_discovery(job_id, reading_ids):

    # --------------------------------
    # Primera etapa: detectar las revistas a partir de los mangas
    # que están en Reading en la lista de MyAnimeList.
    # --------------------------------

    try:

        update_job(
            job_id,
            {
                "phase": "magazines",
                "message": "Detectando las revistas de tu lista...",
                "current_magazine": "Revistas de tu lista",
                "current_page": 0,
                "total_pages": 0
            }
        )

        def report_detection_progress(done, total_to_check):

            update_job(
                job_id,
                {
                    "phase": "magazines",
                    "current_page": done,
                    "total_pages": total_to_check,
                    "message": (
                        f"Detectando revistas: manga "
                        f"{done} de {total_to_check}"
                    )
                }
            )

        detected = jikan.detect_magazines(
            reading_ids,
            progress_callback=report_detection_progress
        )

    except JikanError as error:

        update_job(
            job_id,
            {
                "status": "completed",
                "phase": "finished",
                "message": "No se pudieron detectar las revistas.",
                "fatal_error": str(error),
                "errors": 1
            }
        )

        return

    magazines = [
        {
            "name": magazine["name"],
            "mal_id": magazine["mal_id"],
            "available": True
        }
        for magazine in detected["magazines"]
    ]

    total = len(magazines)

    update_job(
        job_id,
        {
            "total_magazines": total,
            "magazines": [item["name"] for item in magazines],
            "lookup_failed": detected["failed"]
        }
    )

    if total == 0:

        update_job(
            job_id,
            {
                "status": "completed",
                "phase": "finished",
                "message": "Tu lista no tiene mangas en Reading "
                           "con revistas conocidas."
            }
        )

        return

    update_job(
        job_id,
        {
            "phase": "search",
            "message": "Comenzando las búsquedas...",
            "current_page": 0,
            "total_pages": 0
        }
    )

    for index, magazine in enumerate(magazines):

        name = magazine["name"]

        update_job(
            job_id,
            {
                "phase": "search",
                "current_index": index + 1,
                "current_magazine": name,
                "current_page": 0,
                "total_pages": 0,
                "message": f"Preparando {name}..."
            }
        )

        if not magazine.get("available"):

            add_job_section(
                job_id,
                {
                    "name": name,
                    "status": "unavailable",
                    "count": 0,
                    "results": [],
                    "error": magazine.get("error")
                }
            )

            continue

        def report_progress(page, total_pages):

            update_job(
                job_id,
                {
                    "current_page": page,
                    "total_pages": total_pages,
                    "message": (
                        f"{name}: página {page} "
                        f"de {total_pages}"
                    )
                }
            )

        try:

            data = jikan.search_manga(
                magazine["mal_id"],
                reading_ids,
                order_by="score",
                progress_callback=report_progress
            )

            results = [
                format_manga(manga)
                for manga in data["results"]
            ]

            add_job_section(
                job_id,
                {
                    "name": name,
                    "status": "completed",
                    "count": len(results),
                    "excluded": data["excluded"],
                    "pages_checked": data["pages_checked"],
                    "cache_hit": data.get("cache_hit", False),
                    "results": results
                }
            )

        except JikanError as error:

            add_job_section(
                job_id,
                {
                    "name": name,
                    "status": "error",
                    "count": 0,
                    "results": [],
                    "error": str(error)
                }
            )

            # Si falla la conexión, no seguimos realizando
            # solicitudes innecesarias a Tenrai.

            for remaining in magazines[index + 1:]:

                add_job_section(
                    job_id,
                    {
                        "name": remaining["name"],
                        "status": "error",
                        "count": 0,
                        "results": [],
                        "error": (
                            "No se consultó porque la conexión "
                            "con Tenrai falló."
                        )
                    }
                )

            break

        except Exception as error:

            add_job_section(
                job_id,
                {
                    "name": name,
                    "status": "error",
                    "count": 0,
                    "results": [],
                    "error": f"Error inesperado: {error}"
                }
            )

    # --------------------------------
    # Segunda etapa: foros de capítulos
    # --------------------------------

    with discovery_jobs_lock:

        job = discovery_jobs.get(job_id)

        if not job:
            return

        errors = sum(
            1
            for section in job["sections"]
            if section["status"] in ("error", "unavailable")
        )

        mangas = [
            manga
            for section in job["sections"]
            if section["status"] == "completed"
            for manga in section["results"]
        ]

        job["phase"] = "forum"
        job["message"] = "Consultando los últimos capítulos..."
        job["forum_total"] = len(mangas)
        job["forum_completed"] = 0
        job["forum_errors"] = 0

    for index, manga in enumerate(mangas, start=1):

        manga_id = manga.get("mal_id")

        try:

            forum_data = jikan.get_latest_chapter_discussion(
                manga_id
            )

        except JikanError as error:

            forum_data = {
                "forum_status": "error",
                "latest_chapter": None,
                "chapter_date": None,
                "chapter_url": None,
                "chapter_title": None,
                "forum_error": str(error)
            }

        except Exception as error:

            forum_data = {
                "forum_status": "error",
                "latest_chapter": None,
                "chapter_date": None,
                "chapter_url": None,
                "chapter_title": None,
                "forum_error": f"Error inesperado: {error}"
            }

        with discovery_jobs_lock:

            job = discovery_jobs.get(job_id)

            if not job:
                return

            # Actualizamos el objeto original del manga.
            # Así el servidor conserva el resultado completo.

            for section in job["sections"]:

                if section["status"] != "completed":
                    continue

                for stored_manga in section.get("results", []):

                    if stored_manga["mal_id"] == manga_id:

                        stored_manga.update(forum_data)
                        break

            # Guardamos una actualización incremental para el navegador.

            job["forum_updates"].append({
                "mal_id": manga_id,
                **forum_data
            })

            job["forum_completed"] = index

            if forum_data["forum_status"] == "error":
                job["forum_errors"] += 1

            job["message"] = (
                f"Consultando capítulos: "
                f"{index} de {len(mangas)}"
            )

    # --------------------------------
    # Finalizar trabajo
    # --------------------------------

    with discovery_jobs_lock:

        job = discovery_jobs.get(job_id)

        if job:

            job["status"] = "completed"
            job["phase"] = "finished"
            job["message"] = "Catálogo completado."
            job["errors"] = errors
            job["current_index"] = total


@app.route("/")
def index():

    return render_template("index.html")


@app.route("/api/list-info")
@login_required
def list_info():

    try:

        data = get_list()

        return jsonify({
            "ok": True,
            "username": data["username"],
            "total": data["total"],
            "reading_count": len(data["reading_ids"]),
            "counts": data["counts"]
        })

    except MangaListError as error:

        return jsonify({
            "ok": False,
            "error": str(error)
        }), 400


@app.route("/api/discover")
@login_required
def discover():

    try:

        user_list = get_list()

        clean_old_jobs()

        uid = user_id()

        with discovery_jobs_lock:

            running = [
                (existing_id, existing)
                for existing_id, existing in discovery_jobs.items()
                if existing["status"] == "running"
            ]

        # Si ya tenés una búsqueda en curso (por ejemplo, recargaste la
        # página), se retoma esa en lugar de empezar otra.
        for existing_id, existing in running:

            if existing["user_id"] == uid:

                return jsonify({
                    "ok": True,
                    "job_id": existing_id,
                    "magazines": [],
                    "total_magazines": 0
                }), 202

        if len(running) >= MAX_RUNNING_JOBS:

            return jsonify({
                "ok": False,
                "error": "Hay muchas búsquedas en curso. Probá en unos minutos."
            }), 503

        job_id = uuid.uuid4().hex

        job = {
            "user_id": uid,
            "status": "running",
            "phase": "catalogue",
            "message": "Preparando catálogo...",
            "current_index": 0,
            "current_magazine": "",
            "current_page": 0,
            "total_pages": 0,
            "completed_count": 0,
            "total_magazines": 0,
            "magazines": [],
            "lookup_failed": 0,
            "fatal_error": None,
            "sections": [],
            "errors": 0,
            "forum_updates": [],
            "forum_total": 0,
            "forum_completed": 0,
            "forum_errors": 0,
            "created_at": time.time()
        }

        with discovery_jobs_lock:
            discovery_jobs[job_id] = job

        thread = threading.Thread(
            target=run_discovery,
            args=(
                job_id,
                set(user_list["reading_ids"])
            ),
            daemon=True
        )

        thread.start()

        return jsonify({
            "ok": True,
            "job_id": job_id,
            # Las revistas se detectan a partir de tu lista de Reading
            # y se informan por el estado de la búsqueda.
            "magazines": [],
            "total_magazines": 0
        }), 202

    except MangaListError as error:

        return jsonify({
            "ok": False,
            "error": str(error)
        }), 400


@app.route("/api/discover-status/<job_id>")
@login_required
def discover_status(job_id):

    try:

        offset = max(
            0,
            int(request.args.get("after", 0))
        )

    except ValueError:
        offset = 0

    try:

        forum_offset = max(
            0,
            int(request.args.get("forum_after", 0))
        )

    except ValueError:
        forum_offset = 0

    with discovery_jobs_lock:

        job = discovery_jobs.get(job_id)

        # Una búsqueda ajena se trata como inexistente.
        if job is None or job["user_id"] != user_id():

            return jsonify({
                "ok": False,
                "error": "La búsqueda no existe o expiró."
            }), 404

        response = {
            "status": job["status"],
            "phase": job["phase"],
            "message": job["message"],
            "current_index": job["current_index"],
            "current_magazine": job["current_magazine"],
            "current_page": job["current_page"],
            "total_pages": job["total_pages"],
            "completed_count": job["completed_count"],
            "total_magazines": job["total_magazines"],
            "magazines": job["magazines"],
            "lookup_failed": job["lookup_failed"],
            "fatal_error": job["fatal_error"],
            "sections": job["sections"][offset:],
            "errors": job["errors"],

            "forum_updates": job["forum_updates"][forum_offset:],
            "forum_total": job["forum_total"],
            "forum_completed": job["forum_completed"],
            "forum_errors": job["forum_errors"]
        }

    return jsonify({
        "ok": True,
        **response
    })


@app.route("/api/upload-list", methods=["POST"])
@login_required
def upload_list():

    uploaded = request.files.get("file")

    if not uploaded or not uploaded.filename:

        return jsonify({
            "ok": False,
            "error": "No seleccionaste ningún archivo."
        }), 400

    filename = secure_filename(
        uploaded.filename
    ).lower()

    if not (
        filename.endswith(".xml")
        or filename.endswith(".gz")
    ):

        return jsonify({
            "ok": False,
            "error": "El archivo debe ser XML o XML.GZ."
        }), 400

    try:

        raw = uploaded.read()

        parsed = load_mal_list_bytes(raw)

        if parsed["total"] == 0:

            raise MangaListError(
                "La exportación no contiene mangas."
            )

        # Se guarda siempre comprimida, venga como .xml o como .xml.gz.
        if raw[:2] != b"\x1f\x8b":
            raw = gzip.compress(raw)

        uid = user_id()

        store.save_mal_export(
            uid,
            parsed["username"],
            parsed["total"],
            raw
        )

        remember_list(uid, parsed)

        # La caché depende de los IDs de Reading.
        # La limpiamos al cargar una nueva exportación.
        jikan.clear_search_cache()

        return jsonify({
            "ok": True,
            "username": parsed["username"],
            "total": parsed["total"],
            "reading_count": len(parsed["reading_ids"])
        })

    except MangaListError as error:

        return jsonify({
            "ok": False,
            "error": str(error)
        }), 400


# --------------------------------
# Catálogo de Argentina
# --------------------------------


def clean_int(value, minimum=0, maximum=2000):
    """Convierte a entero dentro de un rango; None si no sirve."""

    if value is None or value == "":
        return None

    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError("Tiene que ser un número entero.")

    if number < minimum or number > maximum:
        raise ValueError(
            f"Tiene que estar entre {minimum} y {maximum}."
        )

    return number


def library_summary(mal_id, owned_library):
    """Resumen de la biblioteca del usuario para una serie."""

    entry = owned_library.get(mal_id)

    if entry is None:
        return {
            "in_library": False,
            "owned_count": 0,
            "special_count": 0
        }

    return {
        "in_library": True,
        "owned_count": len(entry["owned"]),
        "special_count": len(entry["special"])
    }


@app.route("/api/argentina/status")
@login_required
def argentina_status():

    return jsonify({
        "ok": True,
        **catalog_status()
    })


@app.route("/api/argentina/refresh", methods=["POST"])
@login_required
def argentina_refresh():

    admin = is_admin(current_user())

    # Forzar (releer todo) es solo del administrador. Un usuario común
    # puede pedir una actualización normal, pero no si el catálogo se
    # actualizó hace poco: es un recurso compartido y cada una tarda ~10 min.
    force = admin and request.args.get("force", "0") == "1"

    finished = argentina_catalog.status().get("job_finished_at") or 0

    recently_updated = time.time() - finished < REFRESH_COOLDOWN

    started = False

    if admin or not recently_updated:
        started = argentina_catalog.start_refresh(force=force)

    return jsonify({
        "ok": True,
        "started": started,
        **catalog_status()
    }), 202


@app.route("/api/argentina/list")
@login_required
def argentina_list():

    try:
        user_list = get_list()

    except MangaListError as error:

        return jsonify({
            "ok": False,
            "error": str(error)
        }), 400

    mangas = user_list.get("mangas", [])

    uid = user_id()

    manual = store.load_manual(uid)
    owned_library = store.load_library(uid)

    found, rest = argentina_catalog.match_list(mangas, manual)

    by_id = {manga["mal_id"]: manga for manga in mangas}

    items = []

    for info in found:

        manga = by_id.get(info["mal_id"], {})

        items.append({
            **info,
            "title": manga.get("title") or info.get("title"),
            "user_status": manga.get("status"),
            **library_summary(info["mal_id"], owned_library)
        })

    # Mangas cargados a mano que no están en tu lista de MAL.
    in_list = set(by_id)

    for mal_id, entry in list(manual.items()):

        if entry.get("hidden") or mal_id in in_list:
            continue

        info = argentina_catalog.resolve(mal_id, None, manual)

        if info:
            items.append({
                **info,
                "user_status": None,
                **library_summary(mal_id, owned_library)
            })

    items.sort(key=lambda item: (item.get("title") or "").lower())

    hidden = {
        mal_id for mal_id, entry in manual.items()
        if entry.get("hidden")
    }

    return jsonify({
        "ok": True,
        "items": items,
        "not_found": [
            {
                "mal_id": manga["mal_id"],
                "title": manga.get("title"),
                "user_status": manga.get("status"),
                "hidden": manga["mal_id"] in hidden
            }
            for manga in sorted(
                rest,
                key=lambda m: (m.get("title") or "").lower()
            )
        ],
        "total_in_list": len(mangas),
        "catalog": catalog_status()
    })


@app.route("/api/argentina/search")
@login_required
def argentina_search():
    """Busca un manga en MyAnimeList para cargarlo a mano."""

    query = (request.args.get("q") or "").strip()

    if len(query) < 2:

        return jsonify({
            "ok": False,
            "error": "Escribí al menos 2 letras."
        }), 400

    try:

        response = jikan.get(
            "/manga",
            {
                "q": query[:100],
                "limit": 10,
                "order_by": "members",
                "sort": "desc"
            }
        )

    except JikanError as error:

        return jsonify({
            "ok": False,
            "error": str(error)
        }), 502

    results = []

    for manga in response.get("data") or []:

        mal_id = manga.get("mal_id")

        if not mal_id:
            continue

        results.append({
            "mal_id": mal_id,
            "title": manga.get("title"),
            "title_english": manga.get("title_english"),
            "type": manga.get("type"),
            "volumes": manga.get("volumes"),
            "image": (
                ((manga.get("images") or {}).get("jpg") or {})
                .get("image_url")
            ),
            "url": manga.get("url")
        })

    return jsonify({
        "ok": True,
        "results": results
    })


@app.route("/api/argentina/calendar", methods=["POST"])
@login_required
def argentina_calendar():
    """Renueva el calendario de salidas si pasaron más de 12 horas."""

    updated = argentina_catalog.refresh_calendar(
        force=request.args.get("force") == "1" and is_admin(current_user())
    )

    return jsonify({
        "ok": True,
        "updated": updated,
        "catalog": catalog_status(),
    })


@app.route("/api/argentina/cover/<int:mal_id>")
@login_required
def argentina_cover(mal_id):
    """Portada de MyAnimeList para mangas que no la traen de Ivrea."""

    cached = argentina_catalog.covers.get(mal_id)

    if cached is not None:
        return jsonify({"ok": True, "image": cached or None})

    try:
        data = jikan.get(f"/manga/{mal_id}").get("data") or {}

    except JikanError as error:
        return jsonify({"ok": False, "error": str(error)}), 502

    image = ((data.get("images") or {}).get("jpg") or {}).get("image_url")

    # Solo se recuerda si la consulta anduvo; sin portada queda vacío.
    argentina_catalog.set_cover(mal_id, image)

    return jsonify({"ok": True, "image": image})


@app.route("/api/argentina/debug")
@login_required
@admin_required
def argentina_debug():
    """ZIP con las fichas de Ivrea que no se pudieron leer."""

    import io
    import zipfile

    from tomo.sources.argentina_catalog import DEBUG_DIR

    argentina_catalog.write_debug_summary()

    if not DEBUG_DIR.exists():
        return jsonify({"ok": False, "error": "No hay fichas para diagnosticar."}), 404

    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(DEBUG_DIR.iterdir()):
            if path.is_file():
                archive.write(path, path.name)

    return Response(
        buffer.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": "attachment; filename=ivrea_diagnostico.zip"},
    )


@app.route("/api/argentina/manual", methods=["POST"])
@login_required
def argentina_manual_set():

    body = request.get_json(silent=True) or {}

    try:

        mal_id = clean_int(body.get("mal_id"), 1, 10_000_000)
        volumes = clean_int(body.get("volumes_ar"), 0, 2000)

    except ValueError as error:

        return jsonify({
            "ok": False,
            "error": str(error)
        }), 400

    title = str(body.get("title") or "").strip()[:200]
    publisher = str(body.get("publisher") or "").strip()[:80]

    if not mal_id or not title or not publisher:

        return jsonify({
            "ok": False,
            "error": "Faltan el manga o la editorial."
        }), 400

    status = str(body.get("status") or "").strip()[:40] or None
    image = str(body.get("image") or "").strip()[:500] or None

    # Si lo marcabas como "no se publica", deja de estarlo: la entrada
    # manual reemplaza a la de "oculto".
    store.save_manual_entry(user_id(), mal_id, {
        "mal_id": mal_id,
        "title": title,
        "publisher": publisher,
        "volumes_ar": volumes,
        "status": status,
        "image": image,
        "added_at": time.time(),
    })

    return jsonify({"ok": True})


@app.route("/api/argentina/manual/<int:mal_id>", methods=["DELETE"])
@login_required
def argentina_manual_remove(mal_id):

    store.delete_manual_entry(user_id(), mal_id)

    return jsonify({"ok": True})


@app.route("/api/argentina/hide/<int:mal_id>", methods=["POST"])
@login_required
def argentina_hide(mal_id):

    body = request.get_json(silent=True) or {}

    uid = user_id()

    if body.get("hidden", True):

        store.save_manual_entry(
            uid, mal_id, {"mal_id": mal_id, "hidden": True}
        )

    else:

        # Solo se quita si era un "oculto"; una carga manual se conserva.
        current = store.load_manual(uid).get(mal_id)

        if current and current.get("hidden"):
            store.delete_manual_entry(uid, mal_id)

    return jsonify({"ok": True})


# --------------------------------
# Biblioteca virtual
# --------------------------------


def library_item(entry, manual=None):

    if manual is None:
        manual = store.load_manual(user_id())

    info = argentina_catalog.resolve(
        entry["mal_id"],
        [entry.get("title")],
        manual
    ) or {}

    published = info.get("volumes_ar")

    return {
        **entry,
        "title": entry.get("title") or info.get("title") or "",
        "image": entry.get("image") or info.get("image"),
        "publisher": info.get("publisher"),
        "ar_status": info.get("ar_status"),
        "published": published,
        "url": info.get("url")
    }


@app.route("/api/library")
@login_required
def library_list():

    uid = user_id()

    manual = store.load_manual(uid)

    items = [
        library_item(entry, manual)
        for entry in library.all_entries(uid)
    ]

    items.sort(key=lambda item: (item.get("title") or "").lower())

    return jsonify({
        "ok": True,
        "stats": library.stats(uid),
        "items": items
    })


@app.route("/api/library/<int:mal_id>", methods=["POST"])
@login_required
def library_add(mal_id):

    body = request.get_json(silent=True) or {}

    title = str(body.get("title") or "").strip()[:200]

    uid = user_id()

    info = argentina_catalog.resolve(
        mal_id, [title], store.load_manual(uid)
    ) or {}

    entry = library.add_series(
        uid,
        mal_id,
        title=title or info.get("title") or "",
        image=body.get("image") or info.get("image")
    )

    return jsonify({
        "ok": True,
        "item": library_item(entry)
    })


@app.route("/api/library/<int:mal_id>", methods=["PUT"])
@login_required
def library_update(mal_id):

    body = request.get_json(silent=True) or {}

    try:

        owned = body.get("owned")
        special = body.get("special")

        if owned is not None:
            owned = [int(v) for v in owned]

        if special is not None:
            special = [int(v) for v in special]

        total = None
        clear_total = False

        if "total" in body:

            if body["total"] in (None, ""):
                clear_total = True
            else:
                total = clean_int(body["total"], 1, 2000)

    except (TypeError, ValueError):

        return jsonify({
            "ok": False,
            "error": "Los tomos tienen que ser números enteros."
        }), 400

    entry = library.update(
        user_id(),
        mal_id,
        owned=owned,
        special=special,
        total=total,
        notes=body.get("notes"),
        clear_total=clear_total
    )

    if entry is None:

        return jsonify({
            "ok": False,
            "error": "Esa serie no está en tu biblioteca."
        }), 404

    return jsonify({
        "ok": True,
        "item": library_item(entry)
    })


@app.route("/api/library/<int:mal_id>", methods=["DELETE"])
@login_required
def library_remove(mal_id):

    library.remove_series(user_id(), mal_id)

    return jsonify({"ok": True})


@app.route("/api/health")
def health():

    return jsonify({
        "ok": True,
        "service": "Tomo"
    })


def main():
    """Arranca el servidor de desarrollo (python app.py)."""

    print()
    print("================================")
    print("          TOMO")
    print("================================")
    print()

    # Con TOMO_LAN=1 la app también se puede abrir desde otros aparatos
    # de la misma red WiFi (por ejemplo, el celular).
    lan = os.environ.get("TOMO_LAN") == "1"

    print("Abrí http://127.0.0.1:5000")

    # Actualización automática del catálogo (TOMO_AUTO_REFRESH_HOURS=0 la apaga).
    from tomo import scheduler

    if scheduler.start() is not None:

        print(
            "Catálogo de Argentina: se actualiza solo cada "
            f"{scheduler.interval_hours():g} h."
        )

    if lan:

        try:
            # No envía nada: solo averigua qué IP usa esta PC en la red.
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("10.255.255.255", 1))
            address = probe.getsockname()[0]
            probe.close()

            print(f"Desde el celular (mismo WiFi): http://{address}:5000")

        except OSError:
            print("Desde el celular: usá la IP de esta PC (ipconfig) y :5000")

    print()

    print("Presioná Ctrl+C para cerrar.")
    print()

    app.run(
        host="0.0.0.0" if lan else "127.0.0.1",
        port=5000,
        debug=False,
        threaded=True
    )