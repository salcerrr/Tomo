"""Actualización del catálogo compartido: permisos, límites, candado y programador."""

import time

from tomo import db, scheduler, store


def sin_trabajo_pesado(catalog, pasos=None, demora=0.0):
    """Reemplaza las descargas por funciones instantáneas (no hay internet)."""

    pasos = pasos if pasos is not None else []

    catalog.refresh_calendar = lambda force=False: pasos.append("calendario") or True
    catalog._refresh_stacks = lambda force=False: pasos.append("stacks")
    catalog._refresh_ivrea = lambda force=False: (pasos.append("ivrea"), time.sleep(demora))

    return pasos


def esperar_fin(catalog, segundos=10):
    limite = time.time() + segundos

    while catalog.is_running() and time.time() < limite:
        time.sleep(0.05)

    assert not catalog.is_running(), "la actualización no terminó"


# ---------- quién puede actualizar

def test_usuario_comun_puede_pedir_una_actualizacion_normal(user, catalog):
    pasos = sin_trabajo_pesado(catalog)

    respuesta = user.post("/api/argentina/refresh").get_json()
    esperar_fin(catalog)

    assert respuesta["started"] is True
    assert pasos == ["calendario", "stacks", "ivrea"]


def test_limite_de_una_actualizacion_por_hora_para_usuarios_comunes(user, admin, catalog):
    sin_trabajo_pesado(catalog)

    user.post("/api/argentina/refresh")
    esperar_fin(catalog)

    de_nuevo = user.post("/api/argentina/refresh").get_json()

    assert de_nuevo["started"] is False

    # El administrador no tiene ese límite.
    del_admin = admin.post("/api/argentina/refresh").get_json()
    esperar_fin(catalog)

    assert del_admin["started"] is True


def test_forzar_es_solo_del_admin(user, admin, catalog, monkeypatch):
    pedidos = []

    monkeypatch.setattr(
        catalog, "start_refresh",
        lambda force=False: pedidos.append(force) or True,
    )

    user.post("/api/argentina/refresh?force=1")
    admin.post("/api/argentina/refresh?force=1")

    assert pedidos == [False, True]


# ---------- candado entre procesos

def test_candado_de_la_base_impide_dos_a_la_vez():
    with db.advisory_lock(123456) as primero:
        assert primero is True

        with db.advisory_lock(123456) as segundo:
            assert segundo is False

    # Al soltarse, se puede volver a tomar.
    with db.advisory_lock(123456) as tercero:
        assert tercero is True


def test_no_arranca_si_otro_proceso_esta_actualizando(catalog):
    sin_trabajo_pesado(catalog)

    # Simula a otro proceso: dejó su estado "en curso" y da señales de vida.
    store.save_job_state({"running": True, "heartbeat": time.time()})

    assert catalog.is_running() is True
    assert catalog.start_refresh() is False
    assert catalog.status()["job_running"] is True


def test_un_proceso_caido_no_bloquea_para_siempre(catalog):
    sin_trabajo_pesado(catalog)

    # Quedó "en curso" pero hace mucho que no da señales de vida.
    store.save_job_state({
        "running": True,
        "heartbeat": time.time() - catalog_stale() - 5,
    })

    assert catalog.is_running() is False
    assert catalog.start_refresh() is True

    esperar_fin(catalog)


def catalog_stale():
    from tomo.sources.argentina_catalog import JOB_STALE_SECONDS

    return JOB_STALE_SECONDS


def test_el_candado_gana_aunque_el_estado_no_lo_muestre(catalog):
    sin_trabajo_pesado(catalog)

    with db.advisory_lock(727301) as tomado:  # el mismo candado que usa el catálogo
        assert tomado is True

        assert catalog.run_refresh() is False

    assert catalog.is_running() is False


def test_el_avance_queda_guardado_en_la_base(catalog):
    sin_trabajo_pesado(catalog)

    catalog.start_refresh()
    esperar_fin(catalog)

    guardado = store.load_job_state()

    assert guardado["running"] is False
    assert guardado["finished_at"] > 0


def test_otro_proceso_actualizo_se_recarga_el_catalogo(catalog):
    assert catalog.status()["ivrea_count"] == 0

    # Otro proceso escribe una ficha nueva en la base.
    store.save_ivrea(
        {"dandadan": {"slug": "dandadan", "title": "Dandadan", "parse_ok": True}},
        {},
        time.time(),
    )

    assert catalog.reload_if_changed(force=True) is True
    assert catalog.status()["ivrea_count"] == 1


def test_error_en_una_etapa_no_deja_el_estado_colgado(catalog):
    sin_trabajo_pesado(catalog)

    def falla(force=False):
        raise RuntimeError("se cayó Ivrea")

    catalog._refresh_ivrea = falla

    catalog.start_refresh()
    esperar_fin(catalog)

    estado = catalog.status()

    assert estado["job_running"] is False
    assert "se cayó Ivrea" in (estado["job_last_error"] or "")


# ---------- programador

def test_programador_decide_si_toca(catalog):
    ahora = time.time()

    store.save_job_state({"running": False, "finished_at": ahora - 3600})
    assert scheduler.is_due(24, ahora) is False

    store.save_job_state({"running": False, "finished_at": ahora - 30 * 3600})
    assert scheduler.is_due(24, ahora) is True

    assert scheduler.is_due(0, ahora) is False


def test_programador_nunca_actualizado_toca():
    assert scheduler.is_due(24) is True


def test_programador_horas_por_defecto_y_invalidas(monkeypatch):
    monkeypatch.delenv("TOMO_AUTO_REFRESH_HOURS", raising=False)
    assert scheduler.interval_hours() == scheduler.DEFAULT_HOURS

    monkeypatch.setenv("TOMO_AUTO_REFRESH_HOURS", "basura")
    assert scheduler.interval_hours() == scheduler.DEFAULT_HOURS

    monkeypatch.setenv("TOMO_AUTO_REFRESH_HOURS", "0")
    assert scheduler.start() is None
