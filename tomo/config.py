import os

from pathlib import Path

# La raíz del proyecto (la carpeta que tiene app.py), un nivel arriba de tomo/.
BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    """
    Lee un archivo .env simple (CLAVE=valor, una por línea) y completa
    las variables de entorno que todavía no estén definidas.
    """

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for line in lines:

        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, _, value = line.partition("=")

        os.environ.setdefault(
            key.strip(),
            value.strip().strip('"').strip("'")
        )


_load_env_file(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
DEFAULT_LIST_PATH = DATA_DIR / "mangalist.xml.gz"

# Base de datos PostgreSQL. Se configura con la variable de entorno
# DATABASE_URL (o en el archivo .env). El valor por defecto coincide con
# el de docker-compose.yml.
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://tomo:tomo@localhost:5432/tomo"
)

MIGRATIONS_DIR = BASE_DIR / "migrations"

JIKAN_BASE_URL = "https://api.tenrai.org/v1"

# Romance en MyAnimeList
ROMANCE_GENRE_ID = 22