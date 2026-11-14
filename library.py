"""
Biblioteca virtual de tomos físicos.

Guarda, por serie, qué tomos tenés y cuáles son edición especial.
Todo es local (data/library.json).
"""

from __future__ import annotations

import json
import threading
from typing import Optional

from config import DATA_DIR

LIBRARY_PATH = DATA_DIR / "library.json"

MAX_VOLUME = 2000


def _clean_numbers(values) -> list[int]:

    return sorted({
        int(v) for v in (values or [])
        if 0 < int(v) <= MAX_VOLUME
    })


class Library:
    """
    {
      "entries": {
        "<mal_id>": {
          "mal_id": 123,
          "title": "Dandadan",
          "image": "https://...",
          "total": 20,               # tomos publicados (opcional)
          "owned": [1, 2, 5],
          "special": [2],            # subconjunto de owned
          "notes": ""
        }
      }
    }
    Una serie existe en la biblioteca desde que se agrega, aunque
    todavía no tenga ningún tomo marcado.
    """

    def __init__(self):

        self.lock = threading.RLock()
        self._entries: dict[int, dict] = {}

        self.load()

    # --------------------------------
    # Persistencia
    # --------------------------------

    def load(self) -> None:

        with self.lock:

            self._entries = {}

            try:

                if not LIBRARY_PATH.exists():
                    return

                with open(LIBRARY_PATH, "r", encoding="utf-8") as file:
                    stored = json.load(file)

                for mal_id, value in (stored.get("entries") or {}).items():

                    owned = _clean_numbers(value.get("owned"))

                    total = value.get("total")

                    self._entries[int(mal_id)] = {
                        "mal_id": int(mal_id),
                        "title": value.get("title") or "",
                        "image": value.get("image"),
                        "total": (
                            int(total)
                            if isinstance(total, int) and total > 0
                            else None
                        ),
                        "owned": owned,
                        "special": [
                            v for v in _clean_numbers(value.get("special"))
                            if v in owned
                        ],
                        "notes": value.get("notes") or "",
                    }

            except (OSError, ValueError, TypeError, AttributeError):
                self._entries = {}

    def _save(self) -> None:

        payload = {
            "entries": {
                str(mal_id): entry
                for mal_id, entry in self._entries.items()
            }
        }

        LIBRARY_PATH.parent.mkdir(parents=True, exist_ok=True)

        temporary = LIBRARY_PATH.with_name("library.json.tmp")

        try:

            with open(temporary, "w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)

            temporary.replace(LIBRARY_PATH)

        except OSError:
            pass

    # --------------------------------
    # Operaciones
    # --------------------------------

    def get(self, mal_id: int) -> Optional[dict]:

        with self.lock:

            entry = self._entries.get(int(mal_id))

            return dict(entry) if entry else None

    def add_series(
        self,
        mal_id: int,
        title: str = "",
        image: Optional[str] = None,
        total: Optional[int] = None,
    ) -> dict:
        """Agrega la serie a la biblioteca (si ya está, no la pisa)."""

        mal_id = int(mal_id)

        with self.lock:

            entry = self._entries.get(mal_id)

            if entry is None:

                entry = {
                    "mal_id": mal_id,
                    "title": title or "",
                    "image": image,
                    "total": total if total and total > 0 else None,
                    "owned": [],
                    "special": [],
                    "notes": "",
                }

                self._entries[mal_id] = entry

            else:

                if title and not entry["title"]:
                    entry["title"] = title

                if image and not entry["image"]:
                    entry["image"] = image

            self._save()

            return dict(entry)

    def update(
        self,
        mal_id: int,
        owned: Optional[list] = None,
        special: Optional[list] = None,
        total: Optional[int] = None,
        notes: Optional[str] = None,
        clear_total: bool = False,
    ) -> Optional[dict]:
        """Actualiza los datos de una serie que ya está en la biblioteca."""

        mal_id = int(mal_id)

        with self.lock:

            entry = self._entries.get(mal_id)

            if entry is None:
                return None

            if owned is not None:
                entry["owned"] = _clean_numbers(owned)

            if special is not None:
                entry["special"] = _clean_numbers(special)

            # Una edición especial siempre es un tomo que se tiene.
            entry["special"] = [
                v for v in entry["special"] if v in entry["owned"]
            ]

            if clear_total:
                entry["total"] = None

            elif total is not None:
                entry["total"] = (
                    int(total) if 0 < int(total) <= MAX_VOLUME else None
                )

            if notes is not None:
                entry["notes"] = str(notes)[:2000]

            self._save()

            return dict(entry)

    def remove_series(self, mal_id: int) -> bool:

        with self.lock:

            existed = self._entries.pop(int(mal_id), None) is not None

            if existed:
                self._save()

            return existed

    def all_entries(self) -> list[dict]:

        with self.lock:
            return [dict(entry) for entry in self._entries.values()]

    def stats(self) -> dict:

        with self.lock:

            return {
                "series": len(self._entries),
                "volumes": sum(
                    len(e["owned"]) for e in self._entries.values()
                ),
                "special": sum(
                    len(e["special"]) for e in self._entries.values()
                ),
            }


# Instancia global
library = Library()
