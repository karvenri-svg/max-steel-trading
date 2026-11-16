"""eBay — API oficial Browse (https://developer.ebay.com/api-docs/buy/browse/overview.html).

Usa el flujo OAuth "client credentials" (token de aplicación). No requiere
la contraseña de ningún usuario.
"""
from __future__ import annotations

import base64
import time
from datetime import datetime

import httpx

from app.config import get_settings
from app.sources.base import ErrorFuente, Publicacion

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
SCOPE = "https://api.ebay.com/oauth/api_scope"


class EbayFuente:
    nombre = "ebay"

    def __init__(self, http: httpx.Client | None = None) -> None:
        self.cfg = get_settings()
        self.http = http or httpx.Client(timeout=20)
        self._token: str | None = None
        self._expira = 0.0

    def disponible(self) -> bool:
        return bool(self.cfg.ebay_client_id and self.cfg.ebay_client_secret)

    def _token_app(self) -> str:
        if self._token and time.time() < self._expira - 60:
            return self._token
        cred = base64.b64encode(
            f"{self.cfg.ebay_client_id}:{self.cfg.ebay_client_secret}".encode()).decode()
        r = self.http.post(TOKEN_URL, headers={
            "Authorization": f"Basic {cred}",
            "Content-Type": "application/x-www-form-urlencoded",
        }, data={"grant_type": "client_credentials", "scope": SCOPE})
        if r.status_code != 200:
            raise ErrorFuente(f"eBay OAuth {r.status_code}: {r.text[:200]}")
        j = r.json()
        self._token = j["access_token"]
        self._expira = time.time() + int(j.get("expires_in", 7200))
        return self._token

    def buscar(self, termino: str, limite: int = 50) -> list[Publicacion]:
        r = self.http.get(SEARCH_URL, params={
            "q": termino, "limit": min(limite, 200), "sort": "newlyListed",
        }, headers={
            "Authorization": f"Bearer {self._token_app()}",
            "X-EBAY-C-MARKETPLACE-ID": self.cfg.ebay_marketplace,
        })
        if r.status_code != 200:
            raise ErrorFuente(f"eBay búsqueda {r.status_code}: {r.text[:200]}")
        return [self._convertir(it) for it in r.json().get("itemSummaries", [])
                if it.get("price")]

    @staticmethod
    def _convertir(it: dict) -> Publicacion:
        envio = 0.0
        for op in it.get("shippingOptions") or []:
            costo = (op.get("shippingCost") or {}).get("value")
            if costo is not None:
                envio = float(costo)
                break
        loc = it.get("itemLocation") or {}
        fecha = it.get("itemCreationDate")
        return Publicacion(
            plataforma="ebay",
            id_externo=it["itemId"],
            url=it.get("itemWebUrl", ""),
            titulo=it.get("title", ""),
            precio=float(it["price"]["value"]),
            moneda=it["price"].get("currency", "USD"),
            envio=envio,
            ubicacion=", ".join(x for x in (loc.get("city"), loc.get("stateOrProvince"),
                                            loc.get("country")) if x) or None,
            foto_url=(it.get("image") or {}).get("imageUrl"),
            vendedor=(it.get("seller") or {}).get("username"),
            condicion=it.get("condition"),
            fecha=datetime.fromisoformat(fecha.replace("Z", "+00:00")) if fecha else None,
        )
