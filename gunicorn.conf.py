"""
Configuración de gunicorn para producción.

Se usa UN solo proceso con varios hilos, a propósito: la app guarda en
memoria las búsquedas de Descubrir en curso, las listas de MAL ya leídas y
los contadores de intentos de login. Con varios procesos cada uno tendría
su propia copia y los pedidos de una misma persona podrían caer en otro.
Los hilos alcanzan de sobra para el uso esperado.
"""

import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"

workers = 1
threads = int(os.environ.get("WEB_THREADS", "8"))

# Subir una lista o leer fichas puede tardar; el límite evita pedidos colgados.
timeout = 120
graceful_timeout = 30
keepalive = 5

# En contenedores, /tmp puede ser lento; /dev/shm es memoria.
worker_tmp_dir = "/dev/shm" if os.path.isdir("/dev/shm") else None

accesslog = "-"
errorlog = "-"