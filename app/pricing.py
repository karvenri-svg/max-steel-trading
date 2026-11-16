"""Valoración de mercado y cálculo de márgenes (lógica determinista, sin IA)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CatalogoModelo, PrecioReferencia


@dataclass
class Valoracion:
    valor: float | None
    comparables: int
    fuente: str  # "ventas" | "mercado" | "catalogo" | "ia" | "sin_datos"

    @property
    def confianza(self) -> float:
        """0..1 — qué tanto confiar en el valor estimado."""
        if self.valor is None:
            return 0.0
        if self.fuente == "ventas":
            return min(1.0, 0.6 + 0.1 * self.comparables)
        if self.fuente == "mercado":
            return min(0.8, 0.3 + 0.1 * self.comparables)
        if self.fuente == "catalogo":
            return 0.5
        return 0.3  # estimación de IA sin comparables


def estimar_valor(s: Session, modelo: str | None, excluir_precio: float | None = None,
                  dias: int = 180) -> Valoracion:
    """Mediana de ventas reales; si no hay, precios publicados; si no, catálogo."""
    if not modelo:
        return Valoracion(None, 0, "sin_datos")
    desde = datetime.now() - timedelta(days=dias)
    filas = s.scalars(
        select(PrecioReferencia).where(
            PrecioReferencia.modelo == modelo, PrecioReferencia.fecha >= desde)
    ).all()
    vendidos = [f.precio for f in filas if f.vendido]
    if vendidos:
        return Valoracion(round(median(vendidos), 2), len(vendidos), "ventas")
    publicados = [f.precio for f in filas if not f.vendido]
    if excluir_precio is not None and excluir_precio in publicados:
        publicados.remove(excluir_precio)
    if len(publicados) >= 3:
        # Precio de publicación ≠ precio de venta: se descuenta un 10 %.
        return Valoracion(round(median(publicados) * 0.9, 2), len(publicados), "mercado")
    cat = s.scalar(select(CatalogoModelo).where(CatalogoModelo.nombre == modelo))
    if cat and cat.valor_referencia:
        return Valoracion(cat.valor_referencia, 0, "catalogo")
    return Valoracion(None, len(publicados), "sin_datos")


@dataclass
class Margen:
    costo_total: float
    ingreso_neto: float
    ganancia: float
    margen_pct: float  # ganancia / costo_total * 100


def calcular_margen(precio_compra: float, envio_compra: float, valor_reventa: float,
                    comision: float | None = None, envio_venta: float | None = None) -> Margen:
    cfg = get_settings()
    comision = cfg.comision_venta if comision is None else comision
    envio_venta = cfg.costo_envio if envio_venta is None else envio_venta
    costo = precio_compra + (envio_compra or 0)
    ingreso = valor_reventa * (1 - comision) - envio_venta
    ganancia = ingreso - costo
    pct = (ganancia / costo * 100) if costo > 0 else 0.0
    return Margen(round(costo, 2), round(ingreso, 2), round(ganancia, 2), round(pct, 1))


def precio_minimo_venta(costo: float, margen_minimo: float = 0.15) -> float:
    """Precio por debajo del cual pierdes dinero (+ margen mínimo) tras comisión y envío."""
    cfg = get_settings()
    return round((costo * (1 + margen_minimo) + cfg.costo_envio) / (1 - cfg.comision_venta), 0)
