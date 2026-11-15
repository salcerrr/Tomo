"""
Biblioteca virtual de tomos físicos.

Guarda, por usuario y por serie, qué tomos tiene y cuáles son edición
especial. Los datos viven en PostgreSQL (library_series y
library_volumes). No hay copia en memoria: cada operación lee y escribe
la base, así varios usuarios (y varios procesos) ven siempre lo último.
"""

from __future__ import annotations

from typing import Optional

from tomo import store
from tomo.store import MAX_VOLUME, clean_volume_numbers as _clean_numbers


class Library:
    """
    Cada entrada es:
      {
        "mal_id": 123,
        "title": "Dandadan",
        "image": "https://...",
        "total": 20,               # tomos publicados (opcional)
        "owned": [1, 2, 5],
        "special": [2],            # subconjunto de owned
        "notes": ""
      }
    Una serie existe en la biblioteca desde que se agrega, aunque
    todavía no tenga ningún tomo marcado.
    """

    def get(self, user_id: int, mal_id: int) -> Optional[dict]:

        return store.load_library(user_id, int(mal_id)).get(int(mal_id))

    def add_series(
        self,
        user_id: int,
        mal_id: int,
        title: str = "",
        image: Optional[str] = None,
        total: Optional[int] = None,
    ) -> dict:
        """Agrega la serie a la biblioteca (si ya está, no la pisa)."""

        mal_id = int(mal_id)

        existing = self.get(user_id, mal_id)

        if existing is None:

            entry = {
                "mal_id": mal_id,
                "title": title or "",
                "image": image,
                "total": total if total and total > 0 else None,
                "owned": [],
                "special": [],
                "notes": "",
            }

        else:

            entry = dict(existing)

            if title and not entry["title"]:
                entry["title"] = title

            if image and not entry["image"]:
                entry["image"] = image

        if entry != existing:
            store.save_library_entry(user_id, entry)

        return entry

    def update(
        self,
        user_id: int,
        mal_id: int,
        owned: Optional[list] = None,
        special: Optional[list] = None,
        total: Optional[int] = None,
        notes: Optional[str] = None,
        clear_total: bool = False,
    ) -> Optional[dict]:
        """Actualiza los datos de una serie que ya está en la biblioteca."""

        current = self.get(user_id, mal_id)

        if current is None:
            return None

        entry = dict(current)

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

        store.save_library_entry(user_id, entry)

        return entry

    def remove_series(self, user_id: int, mal_id: int) -> bool:

        return store.delete_library_entry(user_id, int(mal_id))

    def all_entries(self, user_id: int) -> list[dict]:

        return list(store.load_library(user_id).values())

    def stats(self, user_id: int) -> dict:

        entries = self.all_entries(user_id)

        return {
            "series": len(entries),
            "volumes": sum(len(e["owned"]) for e in entries),
            "special": sum(len(e["special"]) for e in entries),
        }


# Instancia global
library = Library()
