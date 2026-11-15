"""
Tareas que se pueden lanzar a mano o desde un programador (cron, el
Programador de tareas de Windows, un cron de Render, etc.).

    python -m tomo.jobs refresh           # actualiza el catálogo de Ivrea
    python -m tomo.jobs refresh --force   # vuelve a leer todo, aunque esté al día
    python -m tomo.jobs status            # estado y fechas del catálogo

Si ya hay una actualización en curso (en la app web o en otro proceso),
no arranca otra: el candado vive en la base de datos.
Termina con código 0 si todo salió bien y 1 si hubo errores.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from tomo import db


def _when(timestamp: float) -> str:

    if not timestamp:
        return "nunca"

    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


def _print_status(status: dict) -> None:

    print(f"Series de Ivrea:     {status['ivrea_count']} "
          f"({status['ivrea_unread']} sin leer)  "
          f"· actualizado: {_when(status['ivrea_updated_at'])}")
    print(f"Listas de MAL:       {status['stacks_count']}  "
          f"· actualizado: {_when(status['stacks_updated_at'])}")
    print(f"Calendario:          {status['calendar_count']} lanzamientos  "
          f"· actualizado: {_when(status['calendar_updated_at'])}")

    if status.get("job_running"):
        print(f"En curso: {status.get('job_message') or status.get('job_phase')}")

    elif status.get("job_finished_at"):
        print(f"Última actualización terminó: {_when(status['job_finished_at'])}")

    if status.get("job_last_error"):
        print(f"Último error: {status['job_last_error']}")


def cmd_status() -> int:

    from tomo.sources.argentina_catalog import argentina_catalog

    _print_status(argentina_catalog.status())

    return 0


def cmd_refresh(force: bool) -> int:

    from tomo.sources.argentina_catalog import argentina_catalog

    if not argentina_catalog.start_refresh(force=force):

        print("Ya hay una actualización en curso. No hago nada.")

        return 0

    print("Actualizando el catálogo (puede tardar unos 10 minutos)...")

    last_message = ""

    while True:

        job = argentina_catalog.status()

        message = job.get("job_message") or ""

        if message and message != last_message:
            print(f"  {message}")
            last_message = message

        if not argentina_catalog.is_running():
            break

        time.sleep(2)

    status = argentina_catalog.status()

    print()
    _print_status(status)

    if status.get("job_last_error") or status.get("calendar_error"):

        print()
        print("Terminó con errores.")

        return 1

    print()
    print("Listo.")

    return 0


def main(argv: list[str] | None = None) -> int:

    parser = argparse.ArgumentParser(prog="python -m tomo.jobs")
    commands = parser.add_subparsers(dest="command", required=True)

    refresh = commands.add_parser("refresh", help="actualiza el catálogo")
    refresh.add_argument("--force", action="store_true")

    commands.add_parser("status", help="muestra el estado del catálogo")

    args = parser.parse_args(argv)

    try:

        from tomo import store

        store.ready()

        if args.command == "refresh":
            return cmd_refresh(args.force)

        return cmd_status()

    except db.DatabaseError as error:

        print(f"\n{error}\n", file=sys.stderr)

        return 2


if __name__ == "__main__":
    sys.exit(main())