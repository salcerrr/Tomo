"""Esquema, migraciones e importación de los archivos JSON viejos."""

import json

from tomo import db, legacy_import, store


def test_las_migraciones_estan_aplicadas_y_se_pueden_repetir():
    with db.connection() as conn:
        aplicadas = [
            row["version"]
            for row in conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]

    assert aplicadas[:2] == [1, 2]

    # Repetirlas no hace nada.
    assert db.migrate() == []


def test_borrar_un_usuario_borra_todo_lo_suyo(register):
    ana = register()
    uid = ana.user["id"]

    ana.post("/api/library/1", json={"title": "Serie"})
    ana.put("/api/library/1", json={"owned": [1, 2]})
    ana.post("/api/argentina/manual", json={
        "mal_id": 3, "title": "Manual", "publisher": "Ivrea", "volumes_ar": 1
    })
    store.save_mal_export(uid, "ana", 1, b"\x1f\x8b")

    store.delete_user(uid)

    with db.connection() as conn:
        for tabla in ("library_series", "library_volumes",
                      "argentina_manual", "mal_exports"):
            cuenta = conn.execute(
                f"SELECT count(*) AS n FROM {tabla} WHERE user_id = %s", (uid,)
            ).fetchone()["n"]

            assert cuenta == 0, tabla


def test_los_tomos_se_guardan_sin_repetidos_y_en_orden():
    store.save_library_entry(1, {
        "mal_id": 5, "title": "X", "owned": [3, 1, 3, 2], "special": [2, 9],
    })

    entrada = store.load_library(1)[5]

    assert entrada["owned"] == [1, 2, 3]
    assert entrada["special"] == [2]


# ---------- importación de data/*.json

def escribir(carpeta, nombre, contenido):
    (carpeta / nombre).write_text(json.dumps(contenido), encoding="utf-8")


def test_importar_json_viejo(tmp_path):
    escribir(tmp_path, "library.json", {"entries": {
        "100": {"title": "Dandadan", "owned": [1, 2], "special": [2], "total": 20},
        "malo": {"title": "ignorado"},
    }})
    escribir(tmp_path, "argentina_manual.json", {"entries": {
        "9": {"mal_id": 9, "hidden": True},
    }})
    escribir(tmp_path, "ivrea_catalog.json", {
        "updated_at": 123.0,
        "entries": {"dandadan": {"slug": "dandadan", "title": "Dandadan"}},
        "links": {"100": "dandadan"},
    })

    conteo = legacy_import.run_import(tmp_path)

    assert conteo["library"] == 1
    assert conteo["manual"] == 1
    assert conteo["ivrea"] == 1

    assert store.load_library(store.LEGACY_USER_ID)[100]["owned"] == [1, 2]
    assert store.load_manual(store.LEGACY_USER_ID)[9]["hidden"] is True


def test_importar_sin_archivos_no_falla(tmp_path):
    conteo = legacy_import.run_import(tmp_path)

    assert conteo["library"] == 0
    assert conteo["mal_list"] == 0


def test_la_primera_cuenta_se_queda_con_los_datos_de_antes(tmp_path, new_client):
    escribir(tmp_path, "library.json", {"entries": {
        "100": {"title": "Dandadan", "owned": [1, 2, 3]},
    }})
    legacy_import.run_import(tmp_path)

    assert store.legacy_unclaimed() is True
    assert new_client().get("/api/auth/me").get_json()["legacy_unclaimed"] is True

    primera = new_client()
    primera.post("/api/auth/register", json={
        "email": "dueña@test.com", "password": "clave-segura-1"
    })

    segunda = new_client()
    segunda.post("/api/auth/register", json={
        "email": "otra@test.com", "password": "clave-segura-1"
    })

    assert [i["mal_id"] for i in primera.get("/api/library").get_json()["items"]] == [100]
    assert segunda.get("/api/library").get_json()["items"] == []
    assert store.legacy_unclaimed() is False


def test_sin_datos_viejos_no_se_avisa_de_nada(new_client):
    assert store.legacy_unclaimed() is False
    assert new_client().get("/api/auth/me").get_json()["legacy_unclaimed"] is False
