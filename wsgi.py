"""
Punto de entrada para producción (servidor gunicorn).

    gunicorn -c gunicorn.conf.py wsgi:app

Para desarrollo en tu computadora se sigue usando `python app.py`.
"""

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from tomo import scheduler  # noqa: E402
from tomo.server import app  # noqa: E402,F401  (gunicorn busca "app" acá)

# Actualización automática del catálogo mientras el servidor está despierto
# (TOMO_AUTO_REFRESH_HOURS=0 la apaga, por ejemplo si se programa afuera).
scheduler.start()