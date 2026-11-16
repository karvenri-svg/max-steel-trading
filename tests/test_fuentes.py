import httpx
import pytest

from app.sources.base import ErrorFuente
from app.sources.ebay import EbayFuente
from app.sources.mercadolibre import MercadoLibreFuente


def test_ebay_busqueda(monkeypatch):
    def handler(req: httpx.Request):
        if "oauth2/token" in req.url.path:
            assert req.headers["Authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 7200})
        assert req.headers["Authorization"] == "Bearer tok"
        assert req.url.params["sort"] == "newlyListed"
        return httpx.Response(200, json={"itemSummaries": [{
            "itemId": "v1|1|0", "title": "Max Steel 2000", "price": {"value": "25.00", "currency": "USD"},
            "itemWebUrl": "https://ebay.com/itm/1", "image": {"imageUrl": "https://i.ebayimg.com/1.jpg"},
            "itemLocation": {"city": "Austin", "country": "US"}, "seller": {"username": "vend"},
            "shippingOptions": [{"shippingCost": {"value": "10.00"}}], "condition": "Used",
            "itemCreationDate": "2026-11-01T10:00:00.000Z"}]})
    f = EbayFuente(httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(f.cfg, "ebay_client_id", "id")
    monkeypatch.setattr(f.cfg, "ebay_client_secret", "sec")
    [p] = f.buscar("max steel")
    assert p.precio == 25 and p.moneda == "USD" and p.envio == 10
    assert p.ubicacion == "Austin, US" and p.foto_url.startswith("https://")


def test_mercadolibre_busqueda():
    def handler(req):
        assert req.headers["Authorization"] == "Bearer t"
        return httpx.Response(200, json={"results": [{
            "id": "MLM1", "title": "Ozaru GT", "price": 350, "currency_id": "MXN",
            "permalink": "https://articulo.mercadolibre.com.mx/MLM1",
            "thumbnail": "http://http2.mlstatic.com/D_1-I.jpg", "seller": {"nickname": "abc"},
            "address": {"state_name": "Jalisco", "city_name": "Guadalajara"}}]})
    f = MercadoLibreFuente(httpx.Client(transport=httpx.MockTransport(handler)))
    f.cfg.meli_access_token = "t"
    try:
        [p] = f.buscar("ozaru")
    finally:
        f.cfg.meli_access_token = ""
    assert p.id_externo == "MLM1" and p.ubicacion == "Guadalajara, Jalisco"
    assert p.foto_url == "https://http2.mlstatic.com/D_1-O.jpg"


def test_mercadolibre_403():
    f = MercadoLibreFuente(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403))))
    with pytest.raises(ErrorFuente):
        f.buscar("x")
