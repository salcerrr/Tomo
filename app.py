"""
Punto de entrada de Tomo.

    python app.py

El código vive en el paquete tomo/ (rutas en tomo/server.py).
"""

if __name__ == "__main__":

    # Si la base local está apagada (por ejemplo Docker Desktop cerrado),
    # la levanta antes de importar el resto.
    from tomo.devdb import ensure_local_database

    ensure_local_database()

from tomo.server import app, main  # noqa: E402,F401  (app lo usan las pruebas)

if __name__ == "__main__":
    main()