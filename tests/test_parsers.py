"""Funciones puras: no necesitan servidor ni internet."""

import gzip
from datetime import date

import pytest

from tomo.sources import manga_list
from tomo.sources.argentina_catalog import (
    _normalize_publisher,
    _normalize_status,
    _parse_note,
    extract_slugs,
    format_date_es,
    normalize_title,
    parse_ivrea_calendar,
    parse_ivrea_title,
    title_keys,
)


# ---------- títulos y normalización

def test_normalize_title_quita_acentos_y_simbolos():
    assert normalize_title("Ōoku: The Inner Chambers!") == "ookutheinnerchambers"
    assert normalize_title(None) == ""
    assert normalize_title("   ") == ""


def test_title_keys_ignora_lo_que_va_entre_parentesis():
    keys = title_keys("Fukushima (1-F)", None, "")

    assert "fukushima" in keys
    assert "fukushima1f" in keys
    assert "" not in keys


@pytest.mark.parametrize("raw, esperado", [
    ("Publishing", "En curso"),
    ("En curso", "En curso"),
    ("Completa", "Completa"),
    ("Completed (original), Publishing (Kanzenban)", "En curso"),
    ("Finished", "Completa"),
    ("Discontinued", "Discontinuada"),
    ("On Hiatus", "En pausa"),
    ("To be released", "Próximamente"),
    ("", None),
    (None, None),
])
def test_normalize_status_siempre_en_espanol(raw, esperado):
    assert _normalize_status(raw) == esperado


@pytest.mark.parametrize("raw, esperado", [
    ("ivrea argentina", "Ivrea"),
    ("Panini Comics", "Panini"),
    ("Ovni Press (Defunct) / Ivrea", "Ivrea"),
    ("Editorial Desconocida", "Editorial Desconocida"),
    ("  ", None),
    (None, None),
])
def test_normalize_publisher(raw, esperado):
    assert _normalize_publisher(raw) == esperado


def test_parse_note_lee_los_campos_de_la_nota():
    nota = "Publisher: Ivrea\nStatus: Publishing\nVolumes: 12 (en curso)\nTitle: Dandadan"

    assert _parse_note(nota) == {
        "publisher": "Ivrea",
        "status": "Publishing",
        "volumes_ar": 12,
        "title_local": "Dandadan",
    }
    assert _parse_note("")["volumes_ar"] is None


# ---------- fichas de Ivrea

FICHA_EN_CURSO = """
<html><head><title>Dandadan – Editorial Ivrea Argentina</title></head><body>
<img class="vc_single_image-img" src="https://www.ivrea.com.ar/covers/dandadan.jpg">
<ul>
<li>• AUTOR: Yukinobu Tatsu</li>
<li>• ESTADO: EN CURSO (23 TOMOS)</li>
</ul>
</body></html>
"""

FICHA_COMPLETA = """
<html><head><title>Ichi the Witch – Ivrea</title></head><body>
<li>• ESTADO: COMPLETA (3 TOMOS)</li>
<li>• SERIE DE: 17 TOMOS</li>
</body></html>
"""

FICHA_TOMO_UNICO = """
<html><head><title>Un libro – Ivrea</title></head><body>
<ul><li>• TOMO ÚNICO</li></ul>
</body></html>
"""

FICHA_ILEGIBLE = "<html><head><title>Algo – Ivrea</title></head><body>sin datos</body></html>"


def test_ficha_en_curso():
    ficha = parse_ivrea_title("dandadan", FICHA_EN_CURSO)

    assert ficha["title"] == "Dandadan"
    assert ficha["author"] == "Yukinobu Tatsu"
    assert ficha["status_ar"] == "En curso"
    assert ficha["volumes_ar"] == 23
    assert ficha["image"] == "https://www.ivrea.com.ar/covers/dandadan.jpg"
    assert ficha["parse_ok"] is True


def test_ficha_completa_usa_los_tomos_del_estado_no_los_de_serie_de():
    ficha = parse_ivrea_title("ichi", FICHA_COMPLETA)

    assert ficha["status_ar"] == "Completa"
    assert ficha["volumes_ar"] == 3


def test_ficha_tomo_unico():
    ficha = parse_ivrea_title("libro", FICHA_TOMO_UNICO)

    assert ficha["volumes_ar"] == 1
    assert ficha["status_ar"] == "Completa"


def test_ficha_ilegible_no_inventa_datos():
    ficha = parse_ivrea_title("algo", FICHA_ILEGIBLE)

    assert ficha["parse_ok"] is False
    assert ficha["volumes_ar"] is None
    assert ficha["text_head"]  # queda texto para diagnosticar


def test_extract_slugs_sin_repetidos():
    html = (
        '<a href="/titulo/dandadan/">A</a>'
        '<a href="https://www.ivrea.com.ar/titulo/Dandadan/">B</a>'
        '<a href="/titulo/one-piece/">C</a>'
        '<a href="/otra/cosa/">D</a>'
    )

    assert extract_slugs(html) == ["dandadan", "one-piece"]


# ---------- calendario

CALENDARIO = """
<html><body>
<h2>30 DE OCTUBRE</h2>
<div><a href="/titulo/dandadan/">DANDADAN #24</a></div>
<div><a href="/titulo/ichi-the-witch/">ICHI THE WITCH (EDICIÓN ESPECIAL LIMITADA) #1</a></div>
<div>¡ÚLTIMO TOMO!</div>
<h2>DICIEMBRE</h2>
<div><a href="/titulo/kagurabachi/">KAGURABACHI #12</a></div>
<div>NUEVA SERIE - MUY PRONTO ANUNCIO</div>
<h2>REEDICIONES POR TOMO AGOTADO</h2>
<h2>15 DE NOVIEMBRE</h2>
<div>REEDICIONES POR TOMO AGOTADO</div>
<div><a href="/titulo/berserk/">BERSERK #1</a></div>
</body></html>
"""


def _por_titulo(eventos, titulo):
    return [e for e in eventos if e["title"] == titulo]


def test_calendario_lee_tomos_especiales_y_reediciones():
    eventos = parse_ivrea_calendar(CALENDARIO, today=date(2026, 10, 1))

    dandadan = _por_titulo(eventos, "DANDADAN")[0]
    assert dandadan["volume"] == 24
    assert dandadan["kind"] == "tomo"
    assert dandadan["date"] == "2026-10-30"
    assert dandadan["precision"] == "day"
    assert dandadan["slug"] == "dandadan"

    ichi = _por_titulo(eventos, "ICHI THE WITCH")[0]
    assert ichi["kind"] == "especial"
    assert ichi["edition"]
    assert ichi["last"] is True

    kagurabachi = _por_titulo(eventos, "KAGURABACHI")[0]
    assert kagurabachi["precision"] == "month"
    assert kagurabachi["date"] == "2026-12-01"

    berserk = _por_titulo(eventos, "BERSERK")[0]
    assert berserk["kind"] == "reedicion"


def test_calendario_sin_repetidos_y_sin_basura():
    doble = CALENDARIO + CALENDARIO

    eventos = parse_ivrea_calendar(doble, today=date(2026, 10, 1))

    assert len(eventos) == len(parse_ivrea_calendar(CALENDARIO, today=date(2026, 10, 1)))
    assert not _por_titulo(eventos, "NUEVA SERIE - MUY PRONTO ANUNCIO")


def test_calendario_vacio():
    assert parse_ivrea_calendar("", today=date(2026, 1, 1)) == []


def test_formato_de_fechas():
    assert format_date_es("2026-10-30") == "30 de octubre"
    assert format_date_es("2026-12-01", "month") == "diciembre"
    assert format_date_es("basura") == ""


# ---------- exportación de MyAnimeList

XML_OK = (
    b"<myanimelist><myinfo><user_name>lector</user_name></myinfo>"
    b"<manga><manga_mangadb_id>1</manga_mangadb_id>"
    b"<manga_title>Uno</manga_title><my_status>Reading</my_status></manga>"
    b"</myanimelist>"
)


def test_exportacion_xml_y_gzip():
    plano = manga_list.load_mal_list_bytes(XML_OK)
    comprimido = manga_list.load_mal_list_bytes(gzip.compress(XML_OK))

    assert plano["username"] == "lector"
    assert plano["total"] == comprimido["total"] == 1


def test_exportacion_rota():
    with pytest.raises(manga_list.MangaListError):
        manga_list.load_mal_list_bytes(b"esto no es xml")


def test_exportacion_con_entidades_maliciosas_se_rechaza():
    bomba = (
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">'
        b'<!ENTITY b "&a;&a;&a;&a;">]><myanimelist>&b;</myanimelist>'
    )

    with pytest.raises(manga_list.MangaListError):
        manga_list.load_mal_list_bytes(bomba)


def test_exportacion_gzip_demasiado_grande(monkeypatch):
    monkeypatch.setattr(manga_list, "MAX_XML_BYTES", 1000)

    grande = gzip.compress(b"<myanimelist>" + b"<x/>" * 2000 + b"</myanimelist>")

    with pytest.raises(manga_list.MangaListError, match="demasiado grande"):
        manga_list.load_mal_list_bytes(grande)
