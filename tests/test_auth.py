"""Cuentas: registro, ingreso, límites y protección de rutas."""

import pytest

from tomo import auth, store


RUTAS_PRIVADAS = [
    ("get", "/api/library"),
    ("get", "/api/list-info"),
    ("get", "/api/argentina/status"),
    ("get", "/api/argentina/list"),
    ("post", "/api/argentina/refresh"),
    ("post", "/api/upload-list"),
    ("get", "/api/discover"),
    ("get", "/api/discover-status/abc"),
    ("post", "/api/library/1"),
    ("delete", "/api/library/1"),
]


@pytest.mark.parametrize("metodo, ruta", RUTAS_PRIVADAS)
def test_las_rutas_privadas_piden_sesion(new_client, metodo, ruta):
    respuesta = getattr(new_client(), metodo)(ruta)

    assert respuesta.status_code == 401
    assert respuesta.get_json()["auth"] is True


def test_rutas_publicas(new_client):
    client = new_client()

    assert client.get("/").status_code == 200
    assert client.get("/api/health").get_json()["ok"] is True
    assert client.get("/api/auth/me").get_json()["user"] is None


def test_registro_valida_email_y_contrasena(new_client):
    client = new_client()

    assert client.post("/api/auth/register", json={
        "email": "no-es-un-email", "password": "clave-segura-1"
    }).status_code == 400

    assert client.post("/api/auth/register", json={
        "email": "a@b.com", "password": "corta"
    }).status_code == 400

    assert client.post("/api/auth/register", json={
        "email": "a@b.com", "password": "x" * 200
    }).status_code == 400


def test_registro_abre_sesion_y_no_guarda_la_contrasena(register):
    client = register("Ana@Test.com", name="Ana")

    me = client.get("/api/auth/me").get_json()["user"]

    assert me["email"] == "Ana@Test.com"
    assert me["name"] == "Ana"

    guardado = store.get_user_by_email("ana@test.com")

    assert guardado["password_hash"].startswith(("scrypt:", "pbkdf2:"))
    assert "clave-segura-1" not in guardado["password_hash"]


def test_email_repetido_sin_importar_mayusculas(register, new_client):
    register("ana@test.com")

    respuesta = new_client().post("/api/auth/register", json={
        "email": "ANA@test.com", "password": "otra-clave-22"
    })

    assert respuesta.status_code == 409


def test_login_y_logout(register, new_client):
    register("ana@test.com")

    client = new_client()

    mala = client.post("/api/auth/login", json={
        "email": "ana@test.com", "password": "incorrecta"
    })
    assert mala.status_code == 401

    buena = client.post("/api/auth/login", json={
        "email": "ANA@test.com", "password": "clave-segura-1"
    })
    assert buena.status_code == 200
    assert client.get("/api/library").status_code == 200

    client.post("/api/auth/logout")

    assert client.get("/api/library").status_code == 401


def test_el_error_de_login_no_revela_si_el_email_existe(register, new_client):
    register("ana@test.com")

    existente = new_client().post("/api/auth/login", json={
        "email": "ana@test.com", "password": "mala"
    }).get_json()

    inexistente = new_client().post("/api/auth/login", json={
        "email": "nadie@test.com", "password": "mala"
    }).get_json()

    assert existente == inexistente


def test_bloqueo_por_intentos_fallidos(new_client):
    client = new_client()

    codigos = [
        client.post("/api/auth/login", json={
            "email": "x@y.com", "password": "mala"
        }).status_code
        for _ in range(auth.MAX_FAILURES + 2)
    ]

    assert codigos[:auth.MAX_FAILURES] == [401] * auth.MAX_FAILURES
    assert codigos[-1] == 429


def test_el_bloqueo_de_un_email_no_frena_a_otro(register, new_client):
    register("ana@test.com")

    atacante = new_client()

    for _ in range(auth.MAX_FAILURES):
        atacante.post("/api/auth/login", json={
            "email": "ana@test.com", "password": "mala"
        })

    otro = new_client().post("/api/auth/login", json={
        "email": "otra@test.com", "password": "mala"
    })

    assert otro.status_code == 401  # error normal, no 429


def test_limite_de_cuentas_nuevas_por_ip(new_client):
    client = new_client()

    for n in range(auth.MAX_SIGNUPS_PER_IP):
        assert client.post("/api/auth/register", json={
            "email": f"u{n}@test.com", "password": "clave-segura-1"
        }).status_code == 201

        client.post("/api/auth/logout")

    siguiente = client.post("/api/auth/register", json={
        "email": "una-mas@test.com", "password": "clave-segura-1"
    })

    assert siguiente.status_code == 429


def test_escrituras_con_origin_ajeno_se_rechazan(register):
    client = register()

    ajeno = client.post(
        "/api/library/5", json={"title": "X"},
        headers={"Origin": "http://sitio-malicioso.example"},
    )
    propio = client.post(
        "/api/library/5", json={"title": "X"},
        headers={"Origin": "http://localhost"},
    )

    assert ajeno.status_code == 403
    assert propio.status_code != 403


def test_cookie_y_cabeceras_de_seguridad(register):
    client = register()

    cookie = client.get_cookie("tomo_session")

    assert cookie.http_only is True
    assert cookie.same_site == "Lax"

    respuesta = client.get("/api/library")

    assert respuesta.headers["X-Frame-Options"] == "DENY"
    assert respuesta.headers["X-Content-Type-Options"] == "nosniff"
    assert respuesta.headers["Cache-Control"] == "no-store"


def test_la_sesion_se_invalida_si_se_borra_la_cuenta(register):
    client = register()

    store.delete_user(client.user["id"])

    assert client.get("/api/library").status_code == 401


def test_borrar_cuenta_pide_la_contrasena_y_borra_todo(register):
    client = register("ana@test.com")
    uid = client.user["id"]

    client.post("/api/library/7", json={"title": "Serie"})

    assert client.post("/api/auth/delete-account",
                       json={"password": "mala"}).status_code == 403
    assert store.load_library(uid)

    assert client.post("/api/auth/delete-account",
                       json={"password": "clave-segura-1"}).status_code == 200

    assert store.load_library(uid) == {}
    assert store.get_user(uid) is None
    assert client.get("/api/library").status_code == 401


# ---------- administración

def test_la_primera_cuenta_es_admin_y_la_segunda_no(admin, user):
    assert admin.get("/api/auth/me").get_json()["user"]["is_admin"] is True
    assert user.get("/api/auth/me").get_json()["user"]["is_admin"] is False


def test_admin_por_variable_de_entorno(monkeypatch, register):
    monkeypatch.setenv("TOMO_ADMIN_EMAILS", "jefa@test.com, otra@test.com")

    primera = register("primera@test.com")
    jefa = register("JEFA@test.com")

    assert primera.get("/api/auth/me").get_json()["user"]["is_admin"] is False
    assert jefa.get("/api/auth/me").get_json()["user"]["is_admin"] is True


def test_diagnostico_solo_para_admin(admin, user):
    assert user.get("/api/argentina/debug").status_code == 403
    assert admin.get("/api/argentina/debug").status_code in (200, 404)


def test_proxy_las_ips_distintas_no_comparten_limite(monkeypatch, app, new_client):
    # Con ProxyFix, la IP sale de X-Forwarded-For: dos personas detrás del
    # mismo proxy no se bloquean entre sí.
    from werkzeug.middleware.proxy_fix import ProxyFix

    monkeypatch.setattr(app, "wsgi_app", ProxyFix(app.wsgi_app, x_for=1))

    client = new_client()

    for _ in range(auth.MAX_FAILURES):
        client.post("/api/auth/login", headers={"X-Forwarded-For": "1.1.1.1"},
                    json={"email": "a@a.com", "password": "mala"})

    otra_ip = client.post("/api/auth/login",
                          headers={"X-Forwarded-For": "2.2.2.2"},
                          json={"email": "b@b.com", "password": "mala"})

    assert otra_ip.status_code == 401
