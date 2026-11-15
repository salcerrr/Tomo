-- Esquema inicial de Tomo (etapa 1: un solo usuario).
--
-- Los datos se dividen en dos grupos:
--   * Compartidos: salen de fuentes públicas (Ivrea, MyAnimeList, Tenrai)
--     y son iguales para todos.
--   * Personales: pertenecen a quien usa la app. En la etapa 2 estas
--     tablas ganan una columna user_id.

-- ---------- Personales ----------

-- Biblioteca física: una fila por serie y una por cada tomo que se tiene.
CREATE TABLE library_series (
    mal_id      INTEGER PRIMARY KEY,
    title       TEXT        NOT NULL DEFAULT '',
    image       TEXT,
    total       INTEGER     CHECK (total IS NULL OR total > 0),
    notes       TEXT        NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Una edición especial es siempre un tomo que se tiene: por eso vive en
-- la misma fila (is_special) y no en una lista aparte.
CREATE TABLE library_volumes (
    mal_id      INTEGER NOT NULL REFERENCES library_series (mal_id) ON DELETE CASCADE,
    volume      INTEGER NOT NULL CHECK (volume > 0),
    is_special  BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (mal_id, volume)
);

-- Exportación de MyAnimeList (XML comprimido con gzip). Se guarda solo
-- la última subida.
CREATE TABLE mal_exports (
    id           BIGSERIAL   PRIMARY KEY,
    username     TEXT        NOT NULL DEFAULT '',
    total        INTEGER     NOT NULL DEFAULT 0,
    content_gz   BYTEA       NOT NULL,
    uploaded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Correcciones y series cargadas a mano (tienen prioridad sobre las
-- fuentes automáticas).
CREATE TABLE argentina_manual (
    mal_id  INTEGER PRIMARY KEY,
    data    JSONB   NOT NULL
);

-- ---------- Compartidos ----------

-- Listas "Manga published in Argentina" de MyAnimeList.
CREATE TABLE argentina_stacks (
    mal_id  INTEGER PRIMARY KEY,
    data    JSONB   NOT NULL
);

-- Catálogo de Ivrea Argentina (una fila por ficha) y su emparejamiento
-- con los IDs de MyAnimeList.
CREATE TABLE ivrea_titles (
    slug  TEXT  PRIMARY KEY,
    data  JSONB NOT NULL
);

CREATE TABLE ivrea_links (
    mal_id  INTEGER PRIMARY KEY,
    slug    TEXT    NOT NULL
);

-- Calendario de salidas. "position" conserva el orden original.
CREATE TABLE ivrea_calendar (
    position  INTEGER PRIMARY KEY,
    data      JSONB   NOT NULL
);

CREATE TABLE covers (
    mal_id  INTEGER PRIMARY KEY,
    url     TEXT    NOT NULL DEFAULT ''
);

-- Fechas de última actualización de cada fuente y otras banderas.
CREATE TABLE app_meta (
    key    TEXT  PRIMARY KEY,
    value  JSONB NOT NULL
);

-- Caché de respuestas de la API de Tenrai (búsquedas, foro, revistas).
CREATE TABLE api_cache (
    namespace  TEXT             NOT NULL,
    key        TEXT             NOT NULL,
    saved_at   DOUBLE PRECISION NOT NULL,
    data       JSONB            NOT NULL,
    PRIMARY KEY (namespace, key)
);

CREATE INDEX api_cache_saved_at_idx ON api_cache (namespace, saved_at DESC);
