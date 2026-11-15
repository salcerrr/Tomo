"""
Conexión a PostgreSQL y migraciones del esquema.

- Un pool de conexiones compartido por toda la app (Flask atiende cada
  pedido en un hilo distinto y las búsquedas largas corren en hilos de
  fondo).
- Las migraciones son archivos SQL numerados en migrations/. Se aplican
  una sola vez, en orden, al iniciar la app.
"""

from __future__ import annotations

import atexit
import logging
import re
import threading
import time
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

from tomo.config import DATABASE_URL, MIGRATIONS_DIR

log = logging.getLogger("tomo.db")

# Cuánto esperar a que PostgreSQL esté disponible al arrancar (por
# ejemplo, mientras termina de iniciar el contenedor de Docker).
STARTUP_WAIT_SECONDS = 20

# Número arbitrario para que dos procesos no migren a la vez.
_MIGRATION_LOCK_ID = 727274

_pool: ConnectionPool[Any] | None = None
_pool_lock = threading.Lock()

_ready = False
_ready_lock = threading.Lock()


class DatabaseError(Exception):
    pass


def _masked_url() -> str:

    return re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", DATABASE_URL)


def wait_for_database(seconds: float = STARTUP_WAIT_SECONDS) -> None:
    """Reintenta conectarse hasta que PostgreSQL responda o se acabe el tiempo."""

    deadline = time.monotonic() + seconds
    last_error = None

    while True:

        try:

            with psycopg.connect(DATABASE_URL, connect_timeout=3):
                return

        except psycopg.OperationalError as error:

            last_error = error

            if time.monotonic() >= deadline:
                break

            time.sleep(1)

    detail = str(last_error).strip().splitlines()[-1] if last_error else ""

    raise DatabaseError(
        "No pude conectarme a PostgreSQL.\n"
        f"  Dirección: {_masked_url()}\n"
        f"  Detalle:   {detail}\n"
        "Revisá que PostgreSQL esté corriendo (con Docker: "
        "'docker compose up -d') y que DATABASE_URL sea correcta. "
        "Más info en el README."
    )


def get_pool() -> ConnectionPool[Any]:

    global _pool

    if _pool is None:

        with _pool_lock:

            if _pool is None:

                pool: ConnectionPool[Any] = ConnectionPool(
                    DATABASE_URL,
                    min_size=1,
                    max_size=10,
                    kwargs={"row_factory": dict_row, "connect_timeout": 5},
                    check=ConnectionPool.check_connection,
                    open=False,
                )

                pool.open()

                # Cerrar el pool al salir evita el aviso de Python al
                # apagar la app con Ctrl+C.
                atexit.register(close)

                _pool = pool

    assert _pool is not None

    return _pool


@contextmanager
def connection():
    """
    Entrega una conexión del pool. Si el bloque termina bien, confirma la
    transacción; si lanza una excepción, la deshace.
    """

    try:

        with get_pool().connection(timeout=15) as conn:
            yield conn

    except PoolTimeout:

        raise DatabaseError(
            "PostgreSQL no respondió a tiempo.\n"
            f"  Dirección: {_masked_url()}\n"
            "Si acabás de crear el contenedor, esperá un minuto y volvé a "
            "intentar. Si sigue igual, mirá 'docker logs tomo-db'."
        ) from None


@contextmanager
def advisory_lock(key: int):
    """
    Candado de PostgreSQL para que una tarea no corra dos veces a la vez,
    ni siquiera desde procesos distintos (la app web y un cron, por ejemplo).

        with db.advisory_lock(123) as obtained:
            if obtained:
                ...  # somos los únicos corriendo esto

    No espera: entrega False si otro ya lo tiene. El candado vive en una
    conexión propia y se libera solo si el proceso se cae.
    """

    with connection() as conn:

        obtained = bool(
            conn.execute(
                "SELECT pg_try_advisory_lock(%s) AS ok", (key,)
            ).fetchone()["ok"]
        )

        # Cierra la transacción; el candado de sesión sigue tomado.
        conn.commit()

        try:

            yield obtained

        finally:

            if obtained:

                conn.execute("SELECT pg_advisory_unlock(%s)", (key,))
                conn.commit()


def migrate() -> list[str]:
    """Aplica las migraciones pendientes. Devuelve los nombres aplicados."""

    applied_now = []

    with connection() as conn:

        # El lock se libera solo al terminar la transacción.
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK_ID,))

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version     INTEGER PRIMARY KEY,
                name        TEXT        NOT NULL,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )

        done = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations")
        }

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):

            version = int(path.name.split("_", 1)[0])

            if version in done:
                continue

            log.info("Aplicando migración %s", path.name)

            # El SQL sale de nuestros propios archivos de migrations/.
            conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]

            conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (%s, %s)",
                (version, path.name),
            )

            applied_now.append(path.name)

    return applied_now


def ensure_ready() -> None:
    """Espera a la base y deja el esquema al día. Se puede llamar varias veces."""

    global _ready

    if _ready:
        return

    with _ready_lock:

        if _ready:
            return

        wait_for_database()
        migrate()

        _ready = True


def close() -> None:

    global _pool, _ready

    with _pool_lock:

        if _pool is not None:
            _pool.close()
            _pool = None

    _ready = False