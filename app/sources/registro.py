"""Registro de fuentes activas.

Facebook Marketplace y los grupos de Facebook NO tienen una API pública
para buscar publicaciones (Meta retiró la Groups API en 2024 y Marketplace
nunca la tuvo). Por eso NO se hace scraping ni se usan cuentas: las
publicaciones de Facebook se capturan manualmente desde el panel
(Oportunidades → "Agregar publicación de Facebook") y el sistema las
evalúa igual que las demás.
"""
from app.sources.base import Fuente
from app.sources.ebay import EbayFuente
from app.sources.mercadolibre import MercadoLibreFuente


def fuentes_activas() -> list[Fuente]:
    return [f for f in (EbayFuente(), MercadoLibreFuente()) if f.disponible()]


def estado_fuentes() -> dict[str, str]:
    return {
        "eBay (API Browse)": "activa" if EbayFuente().disponible() else "falta EBAY_CLIENT_ID/SECRET",
        "Mercado Libre (API)": "activa" if MercadoLibreFuente().disponible() else "falta MELI_ACCESS_TOKEN",
        "Facebook Marketplace / Grupos": "captura manual desde el panel (sin API pública)",
    }
