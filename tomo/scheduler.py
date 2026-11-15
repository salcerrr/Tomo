"""
Actualización automática del catálogo mientras la app está abierta.

Cada tanto se fija cuándo terminó la última actualización (de quien
sea: esta app u otro proceso) y, si pasaron más de TOMO_AUTO_REFRESH_HOURS
horas, lanza una. Con TOMO_AUTO_REFRESH_HOURS=0 queda desactivada, que es
lo que corresponde cuando la actualización se programa afuera
(`python -m tomo.jobs refresh` desde un cron).

Aunque varios procesos tengan este programador prendido, el candado de la
base garantiza que solo corra una actualización a la vez.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

from tomo import store

log = logging.getLogger("tomo.scheduler")

DEFAULT_HOURS = 24.0

# Espera inicial (para no competir con el arranque) y cada cuánto se revisa.
FIRST_CHECK_SECONDS = 60

# Cada 30 minutos: con una base que se duerme sola (Neon) conviene no
# despertarla seguido solo para mirar si toca actualizar.
CHECK_INTERVAL_SECONDS = 1800


def interval_hours() -> float:

    raw = os.environ.get("TOMO_AUTO_REFRESH_HOURS", str(DEFAULT_HOURS))

    try:
        return max(float(raw), 0.0)

    except ValueError:
        return DEFAULT_HOURS


def last_refresh_at() -> float:
    """
    Cuándo se actualizó el catálogo por última vez. Si nunca se guardó el
    estado de una actualización, se toma la fecha de los datos mismos.
    """

    job = store.load_job_state()

    finished = float(job.get("finished_at") or 0)

    if finished:
        return finished

    return max(store.load_catalog_versions())


def is_due(hours: float, now: Optional[float] = None) -> bool:

    if hours <= 0:
        return False

    now = time.time() if now is None else now

    return now - last_refresh_at() >= hours * 3600


def _loop(hours: float) -> None:

    from tomo.sources.argentina_catalog import argentina_catalog

    time.sleep(FIRST_CHECK_SECONDS)

    while True:

        try:

            if is_due(hours) and argentina_catalog.start_refresh():
                log.info("Actualización automática del catálogo iniciada")

        except Exception:
            log.exception("Falló la revisión de la actualización automática")

        time.sleep(CHECK_INTERVAL_SECONDS)


def start() -> Optional[threading.Thread]:
    """Prende el programador. Devuelve None si está desactivado."""

    hours = interval_hours()

    if hours <= 0:
        return None

    thread = threading.Thread(
        target=_loop, args=(hours,), daemon=True, name="tomo-scheduler"
    )

    thread.start()

    return thread