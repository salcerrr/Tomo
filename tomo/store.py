"""
Capa de acceso a datos de Tomo.

Todas las consultas SQL viven en este archivo: el resto de la app
(library.py, argentina_catalog.py, jikan_client.py, app.py) no escribe
SQL, solo llama a estas funciones.

Los catálogos compartidos (Ivrea, stacks, calendario) se guardan como
documentos JSONB, porque son datos externos con forma variable. Los datos
personales (biblioteca, correcciones manuales, exportación de MAL) tienen
tablas propias.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Optional

import psycopg
from psycopg.types.json import Jsonb

from tomo import db

MAX_VOLUME = 2000

_write_lock = threading.RLock()

# Última versión guardada de cada tabla de documentos, para escribir solo
# lo que cambió en lugar de reescribir todo cada vez.
_snapshots: dict[str, dict] = {}

_legacy_lock = threading.Lock()
_legacy_checked = False


# --------------------------------
# Inicio
# --------------------------------

def ready() -> None:
    """
    Deja la base lista: espera a PostgreSQL, aplica las migraciones y, la
    primera vez, importa los archivos JSON de versiones anteriores.
    """

    global _legacy_checked

    db.ensure_ready()

    if _legacy_checked:
        return

    with _legacy_lock:

        if _legacy_checked:
            return

        from tomo import legacy_import

        legacy_import.import_if_needed()

        _legacy_checked = True


def clean_volume_numbers(values) -> list[int]:

    return sorted({
        int(v) for v in (values or [])
        if 0 < int(v) <= MAX_VOLUME
    })


# --------------------------------
# Metadatos
# --------------------------------

def get_meta(key: str, default: Any = None) -> Any:

    with db.connection() as conn:

        row = conn.execute(
            "SELECT value FROM app_meta WHERE key = %s", (key,)
        ).fetchone()

    return row["value"] if row else default


def set_meta_if_absent(key: str, value: Any) -> None:

    with db.connection() as conn:

        conn.execute(
            "INSERT INTO app_meta (key, value) VALUES (%s, %s) "
            "ON CONFLICT (key) DO NOTHING",
            (key, Jsonb(value))
        )


def set_meta(key: str, value: Any, conn=None) -> None:

    sql = (
        "INSERT INTO app_meta (key, value) VALUES (%s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
    )

    if conn is not None:
        conn.execute(sql, (key, Jsonb(value)))
        return

    with db.connection() as own:
        own.execute(sql, (key, Jsonb(value)))


# --------------------------------
# Tablas clave -> valor con escritura incremental
# --------------------------------

def _encode(value: Any, as_json: bool) -> str:

    if as_json:
        return json.dumps(value, sort_keys=True, ensure_ascii=False)

    return str(value)


def _load_map(table: str, key_col: str, val_col: str, as_json: bool) -> dict:

    with db.connection() as conn:

        rows = conn.execute(
            f"SELECT {key_col} AS k, {val_col} AS v FROM {table}"
        ).fetchall()

    result = {row["k"]: row["v"] for row in rows}

    with _write_lock:
        _snapshots[table] = {
            key: _encode(value, as_json) for key, value in result.items()
        }

    return result


def _sync_map(
    conn, table: str, key_col: str, val_col: str,
    mapping: dict, as_json: bool
) -> dict:
    """
    Deja la tabla igual a `mapping`: inserta lo nuevo, actualiza lo que
    cambió y borra lo que ya no está. Devuelve la nueva instantánea, que
    el llamador guarda recién cuando la transacción se confirma.
    """

    previous = _snapshots.get(table, {})

    encoded = {
        key: _encode(value, as_json) for key, value in mapping.items()
    }

    changed = [
        (key, Jsonb(mapping[key]) if as_json else mapping[key])
        for key, text in encoded.items()
        if previous.get(key) != text
    ]

    removed = [key for key in previous if key not in encoded]

    sql = (
        f"INSERT INTO {table} ({key_col}, {val_col}) VALUES (%s, %s) "
        f"ON CONFLICT ({key_col}) DO UPDATE SET {val_col} = EXCLUDED.{val_col}"
    )

    if changed:

        with conn.cursor() as cursor:
            cursor.executemany(sql, changed)

    if removed:

        cast = "text[]" if table == "ivrea_titles" else "int[]"

        conn.execute(
            f"DELETE FROM {table} WHERE {key_col} = ANY(%s::{cast})",
            (removed,)
        )

    return encoded


# --------------------------------
# Catálogos compartidos
# --------------------------------

def load_stacks() -> tuple[dict[int, dict], float]:

    entries = _load_map("argentina_stacks", "mal_id", "data", True)

    return entries, float(get_meta("stacks_updated_at", 0) or 0)


def save_stacks(entries: dict[int, dict], updated_at: float) -> None:

    with _write_lock:

        with db.connection() as conn:

            snapshot = _sync_map(
                conn, "argentina_stacks", "mal_id", "data", entries, True
            )

            set_meta("stacks_updated_at", updated_at, conn)

        _snapshots["argentina_stacks"] = snapshot


def load_ivrea() -> tuple[dict[str, dict], dict[int, str], float]:

    entries = _load_map("ivrea_titles", "slug", "data", True)
    links = _load_map("ivrea_links", "mal_id", "slug", False)

    return entries, links, float(get_meta("ivrea_updated_at", 0) or 0)


def save_ivrea(
    entries: dict[str, dict], links: dict[int, str], updated_at: float
) -> None:

    with _write_lock:

        with db.connection() as conn:

            titles = _sync_map(
                conn, "ivrea_titles", "slug", "data", entries, True
            )

            link_snapshot = _sync_map(
                conn, "ivrea_links", "mal_id", "slug", links, False
            )

            set_meta("ivrea_updated_at", updated_at, conn)

        _snapshots["ivrea_titles"] = titles
        _snapshots["ivrea_links"] = link_snapshot


def load_calendar() -> tuple[list[dict], float]:

    with db.connection() as conn:

        rows = conn.execute(
            "SELECT data FROM ivrea_calendar ORDER BY position"
        ).fetchall()

    return (
        [row["data"] for row in rows],
        float(get_meta("calendar_updated_at", 0) or 0),
    )


def save_calendar(entries: list[dict], updated_at: float) -> None:

    with _write_lock:

        with db.connection() as conn:

            conn.execute("DELETE FROM ivrea_calendar")

            with conn.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO ivrea_calendar (position, data) "
                    "VALUES (%s, %s)",
                    [(i, Jsonb(entry)) for i, entry in enumerate(entries)]
                )

            set_meta("calendar_updated_at", updated_at, conn)


def load_covers() -> dict[int, str]:

    with db.connection() as conn:

        rows = conn.execute("SELECT mal_id, url FROM covers").fetchall()

    return {row["mal_id"]: row["url"] for row in rows}


def set_cover(mal_id: int, url: Optional[str]) -> None:

    with db.connection() as conn:

        conn.execute(
            "INSERT INTO covers (mal_id, url) VALUES (%s, %s) "
            "ON CONFLICT (mal_id) DO UPDATE SET url = EXCLUDED.url",
            (int(mal_id), url or "")
        )


# --------------------------------
# Estado de la actualización del catálogo
# --------------------------------

JOB_STATE_KEY = "catalog_job"


def load_job_state() -> dict:
    """Último estado guardado por quien esté (o estuvo) actualizando."""

    state = get_meta(JOB_STATE_KEY, {})

    return state if isinstance(state, dict) else {}


def save_job_state(state: dict) -> None:

    set_meta(JOB_STATE_KEY, state)


def load_catalog_versions() -> tuple[float, float, float]:
    """
    Fechas de última actualización de (stacks, ivrea, calendario). Sirven
    para saber si otro proceso cambió el catálogo desde que lo cargamos.
    """

    keys = ["stacks_updated_at", "ivrea_updated_at", "calendar_updated_at"]

    with db.connection() as conn:

        rows = conn.execute(
            "SELECT key, value FROM app_meta WHERE key = ANY(%s::text[])",
            (keys,)
        ).fetchall()

    found = {row["key"]: row["value"] for row in rows}

    return tuple(float(found.get(key) or 0) for key in keys)  # type: ignore[return-value]


# --------------------------------
# Usuarios
# --------------------------------

# Dueño de los datos que existían antes de que hubiera cuentas (ver
# migrations/0002_users.sql).
LEGACY_USER_ID = 1

_USER_COLUMNS = "id, email, name, password_hash, created_at"


def _user_row(row) -> Optional[dict]:

    if row is None:
        return None

    return {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"] or "",
        "password_hash": row["password_hash"],
        "created_at": row["created_at"],
    }


def get_user(user_id: int) -> Optional[dict]:

    with db.connection() as conn:

        row = conn.execute(
            f"SELECT {_USER_COLUMNS} FROM users "
            "WHERE id = %s AND password_hash IS NOT NULL",
            (int(user_id),)
        ).fetchone()

    return _user_row(row)


def legacy_unclaimed() -> bool:
    """¿Hay datos de antes de las cuentas esperando a su dueño?"""

    with db.connection() as conn:

        row = conn.execute(
            "SELECT (password_hash IS NULL) AS free FROM users WHERE id = %s",
            (LEGACY_USER_ID,)
        ).fetchone()

        if not row or not row["free"]:
            return False

        return bool(conn.execute(
            """
            SELECT
                EXISTS (SELECT 1 FROM library_series WHERE user_id = %(u)s)
                OR EXISTS (SELECT 1 FROM mal_exports WHERE user_id = %(u)s)
                OR EXISTS (SELECT 1 FROM argentina_manual WHERE user_id = %(u)s)
                AS has_data
            """,
            {"u": LEGACY_USER_ID}
        ).fetchone()["has_data"])


def get_user_by_email(email: str) -> Optional[dict]:

    with db.connection() as conn:

        row = conn.execute(
            f"SELECT {_USER_COLUMNS} FROM users "
            "WHERE lower(email) = lower(%s) AND password_hash IS NOT NULL",
            (email,)
        ).fetchone()

    return _user_row(row)


class EmailTaken(Exception):
    pass


def create_user(email: str, name: str, password_hash: str) -> dict:
    """
    Crea una cuenta. Si todavía existe la cuenta sin reclamar (la que
    es dueña de los datos de antes de las cuentas), la toma en lugar de
    crear una nueva, así esos datos quedan con quien se registra primero.
    """

    try:

        with db.connection() as conn:

            claimed = conn.execute(
                f"UPDATE users SET email = %s, name = %s, "
                "password_hash = %s, last_login_at = now() "
                "WHERE id = %s AND password_hash IS NULL "
                f"RETURNING {_USER_COLUMNS}",
                (email, name, password_hash, LEGACY_USER_ID)
            ).fetchone()

            if claimed is not None:
                return _user_row(claimed)  # type: ignore[return-value]

            row = conn.execute(
                "INSERT INTO users (email, name, password_hash, last_login_at) "
                "VALUES (%s, %s, %s, now()) "
                f"RETURNING {_USER_COLUMNS}",
                (email, name, password_hash)
            ).fetchone()

    except psycopg.errors.UniqueViolation:

        raise EmailTaken() from None

    return _user_row(row)  # type: ignore[return-value]


def touch_login(user_id: int) -> None:

    with db.connection() as conn:

        conn.execute(
            "UPDATE users SET last_login_at = now() WHERE id = %s",
            (int(user_id),)
        )


def delete_user(user_id: int) -> None:
    """Borra la cuenta y, en cascada, todo lo personal."""

    with db.connection() as conn:

        conn.execute("DELETE FROM users WHERE id = %s", (int(user_id),))


# --------------------------------
# Correcciones manuales (personales)
# --------------------------------

def load_manual(user_id: int) -> dict[int, dict]:

    with db.connection() as conn:

        rows = conn.execute(
            "SELECT mal_id, data FROM argentina_manual WHERE user_id = %s",
            (int(user_id),)
        ).fetchall()

    return {row["mal_id"]: row["data"] for row in rows}


def save_manual_entry(user_id: int, mal_id: int, data: dict) -> None:

    with db.connection() as conn:

        conn.execute(
            """
            INSERT INTO argentina_manual (user_id, mal_id, data)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, mal_id) DO UPDATE SET data = EXCLUDED.data
            """,
            (int(user_id), int(mal_id), Jsonb(data))
        )


def delete_manual_entry(user_id: int, mal_id: int) -> bool:

    with db.connection() as conn:

        cursor = conn.execute(
            "DELETE FROM argentina_manual WHERE user_id = %s AND mal_id = %s",
            (int(user_id), int(mal_id))
        )

        return cursor.rowcount > 0


# --------------------------------
# Biblioteca física (personal)
# --------------------------------

def load_library(user_id: int, mal_id: Optional[int] = None) -> dict[int, dict]:
    """La biblioteca de un usuario (o solo una serie si se pasa mal_id)."""

    only = ["AND mal_id = %s", (int(mal_id),)] if mal_id is not None else ["", ()]

    with db.connection() as conn:

        series = conn.execute(
            "SELECT mal_id, title, image, total, notes "
            "FROM library_series WHERE user_id = %s " + only[0] +
            " ORDER BY created_at, mal_id",
            (int(user_id), *only[1])
        ).fetchall()

        volumes = conn.execute(
            "SELECT mal_id, volume, is_special "
            "FROM library_volumes WHERE user_id = %s " + only[0] +
            " ORDER BY mal_id, volume",
            (int(user_id), *only[1])
        ).fetchall()

    entries = {
        row["mal_id"]: {
            "mal_id": row["mal_id"],
            "title": row["title"] or "",
            "image": row["image"],
            "total": row["total"],
            "owned": [],
            "special": [],
            "notes": row["notes"] or "",
        }
        for row in series
    }

    for row in volumes:

        entry = entries.get(row["mal_id"])

        if entry is None:
            continue

        entry["owned"].append(row["volume"])

        if row["is_special"]:
            entry["special"].append(row["volume"])

    return entries


def upsert_library_entry(conn, user_id: int, entry: dict) -> None:

    user_id = int(user_id)
    mal_id = int(entry["mal_id"])

    owned = clean_volume_numbers(entry.get("owned"))
    special = set(clean_volume_numbers(entry.get("special"))) & set(owned)

    total = entry.get("total")

    conn.execute(
        """
        INSERT INTO library_series (user_id, mal_id, title, image, total, notes)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, mal_id) DO UPDATE SET
            title = EXCLUDED.title,
            image = EXCLUDED.image,
            total = EXCLUDED.total,
            notes = EXCLUDED.notes,
            updated_at = now()
        """,
        (
            user_id,
            mal_id,
            entry.get("title") or "",
            entry.get("image"),
            total if isinstance(total, int) and total > 0 else None,
            entry.get("notes") or "",
        )
    )

    conn.execute(
        "DELETE FROM library_volumes "
        "WHERE user_id = %s AND mal_id = %s "
        "AND NOT (volume = ANY(%s::int[]))",
        (user_id, mal_id, owned)
    )

    if owned:

        with conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO library_volumes (user_id, mal_id, volume, is_special)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id, mal_id, volume)
                DO UPDATE SET is_special = EXCLUDED.is_special
                """,
                [
                    (user_id, mal_id, volume, volume in special)
                    for volume in owned
                ]
            )


def save_library_entry(user_id: int, entry: dict) -> None:

    with db.connection() as conn:
        upsert_library_entry(conn, user_id, entry)


def delete_library_entry(user_id: int, mal_id: int) -> bool:

    with db.connection() as conn:

        # Los tomos se borran solos (ON DELETE CASCADE).
        cursor = conn.execute(
            "DELETE FROM library_series WHERE user_id = %s AND mal_id = %s",
            (int(user_id), int(mal_id))
        )

        return cursor.rowcount > 0


# --------------------------------
# Exportación de MyAnimeList (personal)
# --------------------------------

def save_mal_export(
    user_id: int, username: str, total: int, content_gz: bytes
) -> None:

    with db.connection() as conn:

        conn.execute(
            """
            INSERT INTO mal_exports (user_id, username, total, content_gz)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                username = EXCLUDED.username,
                total = EXCLUDED.total,
                content_gz = EXCLUDED.content_gz,
                uploaded_at = now()
            """,
            (int(user_id), username or "", int(total), content_gz)
        )


def load_mal_export(user_id: int) -> Optional[bytes]:

    with db.connection() as conn:

        row = conn.execute(
            "SELECT content_gz FROM mal_exports WHERE user_id = %s",
            (int(user_id),)
        ).fetchone()

    return bytes(row["content_gz"]) if row else None


# --------------------------------
# Caché de la API de Tenrai
# --------------------------------

def purge_cache(
    max_ages: dict[str, float],
    stale_prefix: str,
    keep_namespace: str
) -> None:
    """
    Borra lo vencido de cada espacio de la caché y los espacios de
    búsqueda de versiones anteriores de la consulta.
    """

    now = time.time()

    with db.connection() as conn:

        conn.execute(
            "DELETE FROM api_cache "
            "WHERE namespace LIKE %s AND namespace <> %s",
            (stale_prefix + "%", keep_namespace)
        )

        for namespace, max_age in max_ages.items():

            conn.execute(
                "DELETE FROM api_cache "
                "WHERE namespace = %s AND saved_at < %s",
                (namespace, now - max_age)
            )


def load_cache(
    namespace: str, max_age: float, limit: int
) -> dict[str, tuple[float, Any]]:

    with db.connection() as conn:

        rows = conn.execute(
            "SELECT key, saved_at, data FROM api_cache "
            "WHERE namespace = %s AND saved_at > %s "
            "ORDER BY saved_at DESC LIMIT %s",
            (namespace, time.time() - max_age, limit)
        ).fetchall()

    return {row["key"]: (row["saved_at"], row["data"]) for row in rows}


def save_cache(namespace: str, items: dict[str, tuple[float, Any]]) -> None:

    if not items:
        return

    with db.connection() as conn:

        with conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO api_cache (namespace, key, saved_at, data)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (namespace, key) DO UPDATE SET
                    saved_at = EXCLUDED.saved_at,
                    data = EXCLUDED.data
                """,
                [
                    (namespace, str(key), saved, Jsonb(data))
                    for key, (saved, data) in items.items()
                ]
            )