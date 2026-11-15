import gzip
import io

from pathlib import Path

# defusedxml rechaza los trucos de XML malicioso (entidades que se expanden
# hasta agotar la memoria, entidades externas, etc.).
import defusedxml.ElementTree as ET

# Tope de tamaño del XML ya descomprimido. Una exportación real pesa unos
# pocos MB; esto frena archivos .gz que se expanden a gigabytes.
MAX_XML_BYTES = 64 * 1024 * 1024


class MangaListError(Exception):
    pass


def parse_export(raw: bytes):
    """Lee el XML de una exportación (comprimido con gzip o no)."""

    try:

        # Se detecta GZIP por su firma y no por la extensión: los
        # archivos subidos pueden venir como .xml o como .xml.gz.
        if raw[:2] == b"\x1f\x8b":

            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as archive:
                content = archive.read(MAX_XML_BYTES + 1)

            if len(content) > MAX_XML_BYTES:
                raise MangaListError(
                    "El archivo descomprimido es demasiado grande."
                )

        else:

            content = raw

        return ET.fromstring(content)

    except Exception as error:

        raise MangaListError(
            f"No se pudo leer la exportación: {error}"
        )


def read_export(path: Path):

    if not path.exists():
        raise MangaListError(
            f"No se encontró la exportación: {path}"
        )

    try:
        raw = path.read_bytes()
    except OSError as error:
        raise MangaListError(
            f"No se pudo leer la exportación: {error}"
        )

    return parse_export(raw)


def load_mal_list(path: Path):

    return summarize_export(read_export(path))


def load_mal_list_bytes(raw: bytes):

    return summarize_export(parse_export(raw))


def summarize_export(root):

    info = root.find("myinfo")

    username = ""

    if info is not None:
        username = info.findtext("user_name", "")

    reading_ids = set()
    all_ids = set()

    # Todos los mangas con su estado y progreso
    mangas = []

    counts = {
        "Reading": 0,
        "Completed": 0,
        "On-Hold": 0,
        "Dropped": 0,
        "Plan to Read": 0,
        "Unknown": 0,
    }

    total = 0

    for manga in root.findall("manga"):

        total += 1

        status = (
            manga.findtext("my_status", "")
            .strip()
        )

        manga_id_raw = (
            manga.findtext("manga_mangadb_id", "")
            .strip()
        )

        title = (
            manga.findtext("manga_title", "")
            .strip()
        )

        chapters_read = (
            manga.findtext("my_read_chapters", "0")
            .strip()
        )

        volumes_read = (
            manga.findtext("my_read_volumes", "0")
            .strip()
        )

        score = (
            manga.findtext("my_score", "0")
            .strip()
        )

        if status in counts:
            counts[status] += 1
        else:
            counts["Unknown"] += 1

        if not manga_id_raw.isdigit():
            continue

        manga_id = int(manga_id_raw)
        all_ids.add(manga_id)

        if status == "Reading":
            reading_ids.add(manga_id)

        mangas.append({
            "mal_id": manga_id,
            "title": title,
            "status": status if status in counts else "Unknown",
            "chapters_read": int(chapters_read) if chapters_read.isdigit() else 0,
            "volumes_read": int(volumes_read) if volumes_read.isdigit() else 0,
            "score": int(score) if score.isdigit() else 0,
        })

    return {
        "username": username,
        "total": total,
        "reading_ids": reading_ids,
        "all_ids": all_ids,
        "mangas": mangas,
        "counts": counts,
    }