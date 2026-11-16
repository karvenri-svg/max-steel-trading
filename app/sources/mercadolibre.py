"""Mercado Libre — API oficial (https://developers.mercadolibre.com).

La búsqueda requiere un access token OAuth de tu aplicación (Mercado Libre
restringe el acceso anónimo). El token se configura en MELI_ACCESS_TOKEN.
"""
from __future__ import annotations

import httpx

from app.config import get_settings
from app.sources.base import ErrorFuente, Publicacion

API = "https://api.mercadolibre.com"


class MercadoLibreFuente:
    nombre = "mercadolibre"

    def __init__(self, http: httpx.Client | None = None) -> None:
        self.cfg = get_settings()
        self.http = http or httpx.Client(timeout=20)

    def disponible(self) -> bool:
        return bool(self.cfg.meli_access_token)

    def buscar(self, termino: str, limite: int = 50) -> list[Publicacion]:
        r = self.http.get(
            f"{API}/sites/{self.cfg.meli_site_id}/search",
            params={"q": termino, "limit": min(limite, 50)},
            headers={"Authorization": f"Bearer {self.cfg.meli_access_token}"},
        )
        if r.status_code in (401, 403):
            raise ErrorFuente(
                "Mercado Libre rechazó el token (401/403). Renueva MELI_ACCESS_TOKEN "
                "o revisa los permisos de tu aplicación.")
        if r.status_code != 200:
            raise ErrorFuente(f"Mercado Libre {r.status_code}: {r.text[:200]}")
        return [self._convertir(it) for it in r.json().get("results", [])]

    @staticmethod
    def _convertir(it: dict) -> Publicacion:
        addr = it.get("address") or it.get("seller_address") or {}
        estado = addr.get("state_name") or (addr.get("state") or {}).get("name")
        ciudad = addr.get("city_name") or (addr.get("city") or {}).get("name")
        foto = it.get("thumbnail")
        if foto:
            foto = foto.replace("http://", "https://").replace("-I.jpg", "-O.jpg")
        return Publicacion(
            plataforma="mercadolibre",
            id_externo=it["id"],
            url=it.get("permalink", ""),
            titulo=it.get("title", ""),
            precio=float(it.get("price") or 0),
            moneda=it.get("currency_id", "MXN"),
            envio=0.0,  # el costo real de envío se conoce al comprar
            ubicacion=", ".join(x for x in (ciudad, estado) if x) or None,
            foto_url=foto,
            vendedor=(it.get("seller") or {}).get("nickname"),
            condicion=it.get("condition"),
        )
