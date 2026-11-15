-- Etapa 2: cuentas de usuario.
--
-- Todo lo personal (biblioteca, series cargadas a mano, exportación de
-- MyAnimeList) pasa a pertenecer a un usuario.
--
-- El usuario 1 existe desde el principio y es dueño de todo lo que se
-- cargó antes de que hubiera cuentas. No tiene contraseña: la primera
-- persona que se registra en una base que todavía tiene ese usuario
-- "sin reclamar" lo toma y conserva sus datos (ver auth.register).

CREATE TABLE users (
    id             BIGSERIAL   PRIMARY KEY,
    email          TEXT        NOT NULL,
    name           TEXT        NOT NULL DEFAULT '',
    password_hash  TEXT,                      -- NULL = cuenta sin reclamar
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at  TIMESTAMPTZ
);

-- El email no distingue mayúsculas de minúsculas.
CREATE UNIQUE INDEX users_email_key ON users (lower(email));

INSERT INTO users (id, email, name) VALUES (1, 'sin-reclamar@tomo.local', '');

SELECT setval(pg_get_serial_sequence('users', 'id'), 1);

-- ---------- Biblioteca ----------

ALTER TABLE library_volumes DROP CONSTRAINT library_volumes_mal_id_fkey;
ALTER TABLE library_volumes DROP CONSTRAINT library_volumes_pkey;
ALTER TABLE library_series  DROP CONSTRAINT library_series_pkey;

ALTER TABLE library_series
    ADD COLUMN user_id BIGINT NOT NULL DEFAULT 1
        REFERENCES users (id) ON DELETE CASCADE;

ALTER TABLE library_series ALTER COLUMN user_id DROP DEFAULT;
ALTER TABLE library_series ADD PRIMARY KEY (user_id, mal_id);

ALTER TABLE library_volumes ADD COLUMN user_id BIGINT NOT NULL DEFAULT 1;
ALTER TABLE library_volumes ALTER COLUMN user_id DROP DEFAULT;

ALTER TABLE library_volumes ADD PRIMARY KEY (user_id, mal_id, volume);

ALTER TABLE library_volumes
    ADD FOREIGN KEY (user_id, mal_id)
    REFERENCES library_series (user_id, mal_id) ON DELETE CASCADE;

-- ---------- Series cargadas a mano / ocultas ----------

ALTER TABLE argentina_manual DROP CONSTRAINT argentina_manual_pkey;

ALTER TABLE argentina_manual
    ADD COLUMN user_id BIGINT NOT NULL DEFAULT 1
        REFERENCES users (id) ON DELETE CASCADE;

ALTER TABLE argentina_manual ALTER COLUMN user_id DROP DEFAULT;
ALTER TABLE argentina_manual ADD PRIMARY KEY (user_id, mal_id);

-- ---------- Exportación de MyAnimeList ----------

ALTER TABLE mal_exports
    ADD COLUMN user_id BIGINT NOT NULL DEFAULT 1
        REFERENCES users (id) ON DELETE CASCADE;

ALTER TABLE mal_exports ALTER COLUMN user_id DROP DEFAULT;

-- Una sola exportación por usuario (la última que subió).
CREATE UNIQUE INDEX mal_exports_user_key ON mal_exports (user_id);
