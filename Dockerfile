# Imagen de producción de Tomo (sirve para cualquier hosting con Docker).
#
#     docker build -t tomo .
#     docker run -p 8000:8000 -e DATABASE_URL=... -e SECRET_KEY=... tomo
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY migrations ./migrations
COPY static ./static
COPY templates ./templates
COPY tomo ./tomo
COPY wsgi.py gunicorn.conf.py ./

# Sin permisos de administrador dentro del contenedor.
RUN useradd --create-home --uid 1000 tomo \
    && mkdir -p /app/data \
    && chown -R tomo:tomo /app/data
USER tomo

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT', '8000'))"

CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]