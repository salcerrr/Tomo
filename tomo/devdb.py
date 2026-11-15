"""
Deja lista la base de datos local cuando se arranca con `python app.py`.

Si PostgreSQL ya responde, no hace nada. Si no, y la base es la local
(localhost), intenta arreglarlo solo:

  1. Si Docker Desktop está instalado pero apagado, lo abre y espera a
     que termine de iniciar.
  2. Levanta el contenedor con `docker compose up -d`.
  3. Espera a que PostgreSQL acepte conexiones.

Si no puede, explica qué falta. Con una base remota (DATABASE_URL apunta
a otro servidor) no toca nada.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import NoReturn, Optional
from urllib.parse import urlparse

from tomo.config import BASE_DIR, DATABASE_URL

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}

# Cuánto esperar a que Docker Desktop termine de arrancar (tarda un rato
# cuando recién se abre) y a que PostgreSQL esté listo dentro del contenedor.
DOCKER_START_SECONDS = 180
DATABASE_START_SECONDS = 90


def say(message: str = "") -> None:

    print(f"[Tomo] {message}" if message else "", flush=True)


def _target() -> tuple[str, int]:

    parsed = urlparse(DATABASE_URL)

    return parsed.hostname or "", parsed.port or 5432


def _is_listening(host: str, port: int) -> bool:

    try:

        with socket.create_connection((host or "localhost", port), timeout=2):
            return True

    except OSError:
        return False


def _wait_for_port(host: str, port: int, seconds: float) -> bool:

    deadline = time.monotonic() + seconds

    while time.monotonic() < deadline:

        if _is_listening(host, port):
            return True

        time.sleep(1)

    return False


def _docker_ready(docker: str) -> bool:
    """¿El motor de Docker responde? (Docker Desktop puede estar apagado.)"""

    try:

        result = subprocess.run(
            [docker, "info"],
            capture_output=True,
            timeout=20,
        )

        return result.returncode == 0

    except (OSError, subprocess.SubprocessError):
        return False


def _docker_desktop_path() -> Optional[Path]:

    if sys.platform != "win32":
        return None

    for variable in ("ProgramFiles", "ProgramW6432", "LOCALAPPDATA"):

        base = os.environ.get(variable)

        if not base:
            continue

        for relative in (
            "Docker/Docker/Docker Desktop.exe",
            "Programs/Docker/Docker/Docker Desktop.exe",
        ):

            candidate = Path(base) / relative

            if candidate.exists():
                return candidate

    return None


def _start_docker_desktop() -> bool:

    path = _docker_desktop_path()

    if path is None:
        return False

    try:

        subprocess.Popen([str(path)], close_fds=True)

        return True

    except OSError:
        return False


def _fail(lines: list[str]) -> NoReturn:

    say()

    for line in lines:
        say(line)

    say()

    sys.exit(1)


def ensure_local_database() -> None:
    """Se llama al arrancar la app; sale con un mensaje claro si no puede."""

    host, port = _target()

    if host not in LOCAL_HOSTS:
        return

    if _is_listening(host, port):
        return

    say(f"PostgreSQL no responde en {host or 'localhost'}:{port}. Intento arreglarlo...")

    docker = shutil.which("docker")

    if docker is None:

        _fail([
            "No encontré PostgreSQL ni Docker en esta computadora.",
            "Instalá Docker Desktop (https://www.docker.com/products/docker-desktop/),",
            "abrilo una vez y volvé a ejecutar Tomo: desde ahí lo hago todo solo.",
            "Si preferís PostgreSQL instalado a mano, mirá el README.",
        ])

    # --- 1) Docker Desktop apagado
    if not _docker_ready(docker):

        say("Docker está apagado. Abriendo Docker Desktop...")

        if not _start_docker_desktop() and sys.platform == "win32":

            _fail([
                "Docker está instalado pero no pude abrir Docker Desktop.",
                "Abrilo a mano desde el menú Inicio, esperá a que diga",
                "'Engine running' y volvé a ejecutar Tomo.",
            ])

        deadline = time.monotonic() + DOCKER_START_SECONDS
        waited = 0

        while time.monotonic() < deadline:

            if _docker_ready(docker):
                break

            time.sleep(3)

            waited += 3

            if waited % 15 == 0:
                say(f"Esperando a Docker Desktop... ({waited} s)")

        else:

            _fail([
                f"Docker Desktop no terminó de iniciar en {DOCKER_START_SECONDS} segundos.",
                "Abrilo a mano, esperá a que diga 'Engine running' y volvé a",
                "ejecutar Tomo. Si no abre, reiniciá la computadora.",
            ])

        say("Docker listo.")

    # --- 2) Levantar el contenedor
    compose_file = BASE_DIR / "docker-compose.yml"

    if not compose_file.exists():

        _fail([
            "Falta el archivo docker-compose.yml en la carpeta de Tomo.",
            "Copialo desde el proyecto original y volvé a intentar.",
        ])

    say("Iniciando la base de datos (docker compose up -d)...")

    result = subprocess.run(
        [docker, "compose", "up", "-d"],
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:

        detail = (result.stderr or result.stdout or "").strip()

        _fail([
            "No pude levantar la base con Docker:",
            *["    " + line for line in detail.splitlines()[-6:]],
            "Si el error habla del puerto 5432, hay otro PostgreSQL usándolo:",
            "cerralo o cambiá DATABASE_URL (ver README).",
        ])

    # --- 3) Esperar a que acepte conexiones
    say("Esperando a que PostgreSQL esté listo...")

    if not _wait_for_port(host, port, DATABASE_START_SECONDS):

        _fail([
            f"El contenedor arrancó pero PostgreSQL no respondió en {DATABASE_START_SECONDS} s.",
            "Mirá los registros con:  docker logs tomo-db",
        ])

    say("Base de datos lista.")