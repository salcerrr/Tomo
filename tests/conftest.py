"""
Configuración de las pruebas.

Las pruebas usan SU PROPIA base de datos (por defecto `tomo_test`, en el mismo
PostgreSQL local). Nunca tocan la base real: antes de empezar se la borra y
se la vuelve a crear, y por seguridad el programa se niega a correr si el
nombre de la base no termina en `_test`.

Para usar otra dirección: variable TOMO_TEST_DATABASE_URL.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import psycopg
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEST_URL = os.environ.get(
    "TOMO_TEST_DATABASE_URL",
    "postgresql://tomo:tomo@localhost:5432/tomo_test",
)

TEST_DB_NAME = urlparse(TEST_URL).path.lstrip("/")

if not TEST_DB_NAME.endswith("_test"):
    pytest.exit(
        f"La base de pruebas '{TEST_DB_NAME}' no termina en '_test'. "
        "Se niega a correr para no borrar datos reales.",
        returncode=2,
    )

# Tiene que estar definido ANTES de importar cualquier módulo de tomo.
os.environ["DATABASE_URL"] = TEST_URL
os.environ["SECRET_KEY"] = "clave-solo-para-pruebas-" * 3
os.environ["TOMO_AUTO_REFRESH_HOURS"] = "0"
os.environ.pop("TOMO_ADMIN_EMAILS", None)
os.environ.pop("TOMO_PROXY_HOPS", None)
os.environ.pop("TOMO_HTTPS", None)


def _create_test_database() -> None:
    """Crea la base de pruebas si no existe (usa la base 'postgres')."""

    parsed = urlparse(TEST_URL)

    admin_url = urlunparse(parsed._replace(path="/postgres"))

    try:

        with psycopg.connect(admin_url, autocommit=True, connect_timeout=5) as conn:

            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (TEST_DB_NAME,),
            ).fetchone()

            if not exists:
                conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')

    except psycopg.errors.InsufficientPrivilege:

        pytest.exit(
            f"El usuario de la base no puede crear '{TEST_DB_NAME}'.\n"
            f"Creala a mano (CREATE DATABASE {TEST_DB_NAME} OWNER ...) o "
            "dale permiso: ALTER USER tomo CREATEDB;",
            returncode=2,
        )

    except psycopg.OperationalError as error:

        pytest.exit(
            "No pude conectarme a PostgreSQL para correr las pruebas.\n"
            f"  {str(error).strip().splitlines()[-1]}\n"
            "Levantá la base (docker compose up -d) o definí "
            "TOMO_TEST_DATABASE_URL.",
            returncode=2,
        )


def _reset_schema() -> None:

    with psycopg.connect(TEST_URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")


_create_test_database()
_reset_schema()

from tomo import db, store  # noqa: E402

# Las pruebas no deben importar los data/*.json reales de quien las corre.
store._legacy_checked = True

db.ensure_ready()

from tomo import auth  # noqa: E402
from tomo import server  # noqa: E402
from tomo.sources.argentina_catalog import argentina_catalog  # noqa: E402


def _truncate_everything() -> None:

    with db.connection() as conn:

        tables = [
            row["tablename"]
            for row in conn.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' "
                "AND tablename <> 'schema_migrations'"
            ).fetchall()
        ]

        conn.execute(
            "TRUNCATE " + ", ".join(tables) + " RESTART IDENTITY CASCADE"
        )

        # La cuenta "sin reclamar" que crea la migración 0002.
        conn.execute(
            "INSERT INTO users (id, email, name) "
            "VALUES (1, 'sin-reclamar@tomo.local', '')"
        )

        conn.execute("SELECT setval(pg_get_serial_sequence('users', 'id'), 1)")


@pytest.fixture(autouse=True)
def clean_state():
    """Cada prueba arranca con la base vacía y la memoria limpia."""

    _truncate_everything()

    auth.reset_rate_limits()

    server.user_lists.clear()
    server.discovery_jobs.clear()

    argentina_catalog.__init__()  # type: ignore[misc]

    yield


@pytest.fixture
def app():
    return server.app


@pytest.fixture
def catalog():
    return argentina_catalog


@pytest.fixture
def new_client(app):
    """Fábrica de clientes HTTP, cada uno con su propia sesión (cookies)."""

    def make():
        return app.test_client()

    return make


@pytest.fixture
def register(new_client):
    """register('a@b.com') -> cliente con la cuenta creada y la sesión abierta."""

    counter = {"n": 0}

    def make(email=None, password="clave-segura-1", name=""):

        counter["n"] += 1

        client = new_client()

        response = client.post("/api/auth/register", json={
            "email": email or f"usuario{counter['n']}@test.com",
            "password": password,
            "name": name,
        })

        assert response.status_code == 201, response.get_json()

        client.user = response.get_json()["user"]

        return client

    return make


@pytest.fixture
def admin(register):
    """La primera cuenta que se crea es la administradora."""
    return register("admin@test.com")


@pytest.fixture
def user(register, admin):
    """Una cuenta común (se crea después de la administradora)."""
    return register("comun@test.com")


MAL_XML = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b"<myanimelist>"
    b"<myinfo><user_name>lector</user_name></myinfo>"
    b"<manga><manga_mangadb_id>11</manga_mangadb_id>"
    b"<manga_title>Dandadan</manga_title>"
    b"<my_status>Reading</my_status></manga>"
    b"<manga><manga_mangadb_id>22</manga_mangadb_id>"
    b"<manga_title>Kagurabachi</manga_title>"
    b"<my_status>Completed</my_status></manga>"
    b"</myanimelist>"
)


@pytest.fixture
def mal_xml():
    return MAL_XML
