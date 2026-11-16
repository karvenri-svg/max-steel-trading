import pytest
from fastapi.testclient import TestClient

from app.web import main


@pytest.fixture()
def client(s):
    with TestClient(main.crear_app(programador=False)) as c:
        yield c


def test_paginas_cargan(client):
    for url in ("/", "/oportunidades", "/inventario", "/ventas", "/vendedor", "/prospectos",
                "/configuracion", "/registro", "/api/metricas", "/api/productos", "/api/oportunidades"):
        assert client.get(url).status_code == 200, url


def test_flujo_completo(client):
    r = client.post("/inventario", data={"nombre": "Max Steel 2000", "linea": "Max Steel", "costo": "300",
                                         "estado_fisico": "bueno"},
                    files={"foto": ("f.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    assert r.status_code == 200 and "MS-0001" in r.text
    pid = client.get("/api/productos").json()[0]["id"]
    r = client.post(f"/inventario/{pid}/venta", data={"precio": "800", "canal": "directo"})
    assert "confirmar" in r.text
    r = client.post(f"/inventario/{pid}/venta", data={"precio": "800", "canal": "directo", "confirmo": "si"})
    assert "Venta registrada" in r.text
    assert client.get("/api/metricas").json()["ventas_realizadas"] == 1


def test_oportunidad_manual_facebook(client):
    r = client.post("/oportunidades/manual", data={
        "url": "https://facebook.com/marketplace/item/123", "titulo": "Ozaru Dragon Ball GT",
        "precio": "250", "plataforma": "facebook"})
    assert r.status_code == 200 and "Evaluada" in r.text
    r = client.post("/oportunidades/manual", data={
        "url": "https://facebook.com/marketplace/item/123", "titulo": "x", "precio": "1"})
    assert "ya estaba registrada" in r.text


def test_prospecto_sin_consentimiento_rechazado(client):
    r = client.post("/prospectos", data={"nombre": "X"})
    assert "consentimiento" in r.text


def test_foto_formato_invalido(client):
    r = client.post("/inventario", data={"nombre": "a", "costo": "1"},
                    files={"foto": ("x.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 400


def test_autenticacion(s, monkeypatch):
    monkeypatch.setattr(main.cfg, "panel_usuario", "u")
    monkeypatch.setattr(main.cfg, "panel_clave", "p")
    with TestClient(main.crear_app(programador=False)) as c:
        assert c.get("/").status_code == 401
        assert c.get("/", auth=("u", "p")).status_code == 200
