"""
Importa a PostgreSQL los archivos de data/ de las versiones anteriores
de Tomo (library.json, ivrea_catalog.json, etc.).

Corre sola, una única vez, la primera vez que la app se conecta a una
base vacía. También se puede ejecutar a mano:

    python -m tomo.legacy_import             # importa si todavía no se importó
    python -m tomo.legacy_import --force     # vuelve a importar (pisa los datos)
    python -m tomo.legacy_import --dir RUTA  # usa otra carpeta de datos

Los archivos originales no se tocan ni se borran.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

from tomo import db
from tomo import store
from tomo.config import DATA_DIR

FLAG = "legacy_json_imported"


def _read_json(path: Path) -> dict:

    try:

        if not path.exists():
            return {}

        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)

        return data if isinstance(data, dict) else {}

    except (OSError, ValueError):
        return {}


def _int_keys(mapping: Optional[dict]) -> dict[int, Any]:

    result = {}

    for key, value in (mapping or {}).items():

        try:
            result[int(key)] = value
        except (TypeError, ValueError):
            continue

    return result


def _import_library(data_dir: Path) -> int:

    stored = _read_json(data_dir / "library.json")

    count = 0

    with db.connection() as conn:

        for mal_id, value in (stored.get("entries") or {}).items():

            try:
                mal_id = int(mal_id)
            except (TypeError, ValueError):
                continue

            store.upsert_library_entry(
                conn, store.LEGACY_USER_ID, {**value, "mal_id": mal_id}
            )

            count += 1

    return count


def _import_mal_list(data_dir: Path) -> int:

    path = data_dir / "mangalist.xml.gz"

    if not path.exists():
        return 0

    # Se importa tal cual está, pero se valida con el mismo lector que
    # usa la app para no guardar un archivo roto.
    from tomo.sources.manga_list import MangaListError, load_mal_list

    try:
        parsed = load_mal_list(path)
    except MangaListError:
        return 0

    raw = path.read_bytes()

    if raw[:2] != b"\x1f\x8b":
        raw = gzip.compress(raw)

    store.save_mal_export(
        store.LEGACY_USER_ID, parsed["username"], parsed["total"], raw
    )

    return 1


def _import_catalogs(data_dir: Path) -> dict[str, int]:

    counts = {}

    stored = _read_json(data_dir / "argentina_catalog.json")
    stacks = _int_keys(stored.get("entries"))

    if stacks:
        store.save_stacks(stacks, float(stored.get("updated_at") or 0))

    counts["stacks"] = len(stacks)

    stored = _read_json(data_dir / "ivrea_catalog.json")
    titles = dict(stored.get("entries") or {})

    if titles:
        store.save_ivrea(
            titles,
            _int_keys(stored.get("links")),
            float(stored.get("updated_at") or 0),
        )

    counts["ivrea"] = len(titles)

    stored = _read_json(data_dir / "ivrea_calendar.json")
    calendar = list(stored.get("entries") or [])

    if calendar:
        store.save_calendar(calendar, float(stored.get("updated_at") or 0))

    counts["calendar"] = len(calendar)

    stored = _read_json(data_dir / "argentina_manual.json")
    manual = _int_keys(stored.get("entries"))

    for mal_id, value in manual.items():
        store.save_manual_entry(store.LEGACY_USER_ID, mal_id, value)

    counts["manual"] = len(manual)

    stored = _read_json(data_dir / "covers.json")
    covers = _int_keys(stored.get("entries"))

    for mal_id, url in covers.items():
        store.set_cover(mal_id, url)

    counts["covers"] = len(covers)

    return counts


def run_import(data_dir: Path = DATA_DIR) -> dict[str, int]:

    counts = {"library": _import_library(data_dir)}
    counts["mal_list"] = _import_mal_list(data_dir)
    counts.update(_import_catalogs(data_dir))

    # La caché de Tenrai (cache.json) no se importa: se vuelve a llenar
    # sola al usar la app y no contiene datos propios.

    store.set_meta(FLAG, {"at": time.time(), "counts": counts})

    return counts


def import_if_needed(data_dir: Path = DATA_DIR) -> Optional[dict[str, int]]:

    if store.get_meta(FLAG) is not None:
        return None

    counts = run_import(data_dir)

    if any(counts.values()):

        summary = ", ".join(f"{name}: {n}" for name, n in counts.items())

        print(f"[Tomo] Datos de data/ importados a PostgreSQL ({summary}).")

    return counts


def main() -> None:

    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dir", type=Path, default=DATA_DIR)
    args = parser.parse_args()

    try:
        db.ensure_ready()
    except db.DatabaseError as error:
        sys.exit(f"\n{error}\n")

    if args.force:
        counts = run_import(args.dir)
    else:
        counts = import_if_needed(args.dir)

    if counts is None:
        print("Ya se había importado. Usá --force para volver a hacerlo.")
    else:
        print("Importado:", counts)


if __name__ == "__main__":
    main()