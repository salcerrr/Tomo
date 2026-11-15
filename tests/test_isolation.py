"""Cada usuario ve y modifica solo lo suyo."""

import gzip
import io


def subir_lista(client, contenido, nombre="lista.xml"):
    return client.post(
        "/api/upload-list",
        data={"file": (io.BytesIO(contenido), nombre)},
        content_type="multipart/form-data",
    )


def items(client):
    return {i["mal_id"]: i for i in client.get("/api/library").get_json()["items"]}


# ---------- biblioteca

def test_biblioteca_agregar_marcar_y_quitar(register):
    ana = register()

    assert ana.post("/api/library/100", json={"title": "Dandadan"}).status_code in (200, 201)

    respuesta = ana.put("/api/library/100", json={
        "owned": [1, 2, 3], "special": [2, 9], "total": 20
    }).get_json()

    assert respuesta["ok"] is True

    serie = items(ana)[100]

    assert serie["owned"] == [1, 2, 3]
    # Una edición especial tiene que ser un tomo que se tiene.
    assert serie["special"] == [2]

    assert ana.get("/api/library").get_json()["stats"] == {
        "series": 1, "volumes": 3, "special": 1
    }

    assert ana.delete("/api/library/100").get_json()["ok"] is True
    assert items(ana) == {}


def test_biblioteca_descarta_numeros_invalidos(register):
    ana = register()

    ana.post("/api/library/100", json={"title": "X"})
    ana.put("/api/library/100", json={"owned": [0, -3, 5, 5, 999999]})

    assert items(ana)[100]["owned"] == [5]


def test_dos_usuarios_misma_serie_datos_distintos(register):
    ana = register()
    beto = register()

    ana.post("/api/library/100", json={"title": "Dandadan"})
    ana.put("/api/library/100", json={"owned": [1, 2]})

    beto.post("/api/library/100", json={"title": "Mi Dandadan"})
    beto.put("/api/library/100", json={"owned": [7]})

    assert items(ana)[100]["owned"] == [1, 2]
    assert items(beto)[100]["owned"] == [7]
    assert items(beto)[100]["title"] == "Mi Dandadan"


def test_no_se_puede_tocar_la_biblioteca_ajena(register):
    ana = register()
    beto = register()

    ana.post("/api/library/100", json={"title": "Serie de Ana"})
    ana.put("/api/library/100", json={"owned": [1]})

    # Beto no la tiene: no puede editarla ni borrarla.
    assert beto.put("/api/library/100", json={"owned": [5]}).status_code == 404
    beto.delete("/api/library/100")

    assert items(ana)[100]["owned"] == [1]


# ---------- lista de MyAnimeList

def test_cada_usuario_tiene_su_lista(register, mal_xml):
    ana = register()
    beto = register()

    assert subir_lista(ana, mal_xml).get_json()["ok"] is True

    assert ana.get("/api/list-info").get_json()["username"] == "lector"
    assert beto.get("/api/list-info").status_code == 400  # todavía no subió


def test_subir_lista_gzip_y_reemplaza_la_anterior(register, mal_xml):
    ana = register()

    subir_lista(ana, mal_xml)

    otra = mal_xml.replace(b"lector", b"otro-nombre")

    assert subir_lista(ana, gzip.compress(otra), "lista.xml.gz").get_json()["ok"]
    assert ana.get("/api/list-info").get_json()["username"] == "otro-nombre"


def test_subida_invalida(register):
    ana = register()

    assert subir_lista(ana, b"esto no es xml").status_code == 400
    assert subir_lista(ana, b"<a/>", "malo.txt").status_code == 400
    assert ana.post("/api/upload-list").status_code == 400


def test_xml_malicioso_y_gzip_bomba_se_rechazan(register):
    ana = register()

    entidades = (
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">'
        b'<!ENTITY b "&a;&a;&a;&a;">]><myanimelist>&b;</myanimelist>'
    )

    assert subir_lista(ana, entidades).status_code == 400

    bomba = gzip.compress(b"<myanimelist>" + b"<x/>" * (20 * 1024 * 1024))
    respuesta = subir_lista(ana, bomba, "bomba.xml.gz")

    assert respuesta.status_code == 400
    assert "grande" in respuesta.get_json()["error"]


# ---------- series cargadas a mano y ocultas

def test_manuales_y_ocultos_son_personales(register):
    ana = register()
    beto = register()

    ana.post("/api/argentina/manual", json={
        "mal_id": 9, "title": "Mi manga", "publisher": "Ivrea", "volumes_ar": 3
    })
    ana.post("/api/argentina/hide/50", json={"hidden": True})

    estado_ana = ana.get("/api/argentina/status").get_json()
    estado_beto = beto.get("/api/argentina/status").get_json()

    assert estado_ana["manual_count"] == 1
    assert estado_ana["hidden_count"] == 1
    assert estado_beto["manual_count"] == 0
    assert estado_beto["hidden_count"] == 0


def test_quitar_manual_y_mostrar_de_nuevo(register):
    ana = register()

    ana.post("/api/argentina/manual", json={
        "mal_id": 9, "title": "Mi manga", "publisher": "Ivrea", "volumes_ar": 3
    })
    ana.delete("/api/argentina/manual/9")

    ana.post("/api/argentina/hide/50", json={"hidden": True})
    ana.post("/api/argentina/hide/50", json={"hidden": False})

    estado = ana.get("/api/argentina/status").get_json()

    assert estado["manual_count"] == 0
    assert estado["hidden_count"] == 0


def test_mostrar_de_nuevo_no_borra_una_carga_manual(register):
    ana = register()

    ana.post("/api/argentina/manual", json={
        "mal_id": 9, "title": "Mi manga", "publisher": "Ivrea", "volumes_ar": 3
    })
    ana.post("/api/argentina/hide/9", json={"hidden": False})

    assert ana.get("/api/argentina/status").get_json()["manual_count"] == 1


# ---------- búsquedas de Descubrir

def test_una_busqueda_ajena_no_existe(register, mal_xml, monkeypatch):
    from tomo import server

    ana = register()
    beto = register()

    server.discovery_jobs["trabajo-de-ana"] = {
        "user_id": ana.user["id"], "status": "running",
        "sections": [], "magazines": [],
    }

    assert beto.get("/api/discover-status/trabajo-de-ana").status_code == 404
    assert ana.get("/api/discover-status/trabajo-de-ana").status_code != 404
