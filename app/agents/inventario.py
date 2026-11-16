"""AGENTE 3 — INVENTARIO.

Registra compras (que TÚ hiciste), actualiza existencias, controla costos
y registra ventas. No ejecuta pagos ni compras.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ajustes import registrar
from app.config import get_settings
from app.models import (
    EstadoFisico, EstadoOportunidad, EstadoProducto, Oportunidad, PrecioReferencia, Producto, Venta,
)
from app.pricing import estimar_valor, precio_minimo_venta

PREFIJOS = {"Max Steel": "MS", "Dragon Ball": "DB"}


class ErrorInventario(ValueError):
    pass


def siguiente_codigo(s: Session, linea: str) -> str:
    pref = PREFIJOS.get(linea, "FG")
    n = s.scalar(select(func.count()).select_from(Producto).where(Producto.codigo.like(f"{pref}-%")))
    while True:
        n += 1
        cod = f"{pref}-{n:04d}"
        if not s.scalar(select(Producto.id).where(Producto.codigo == cod)):
            return cod


def _estado_fisico(valor: str | None) -> EstadoFisico:
    try:
        return EstadoFisico(valor or "desconocido")
    except ValueError:
        return EstadoFisico.desconocido


def precios_sugeridos(s: Session, costo: float, modelo: str | None) -> tuple[float, float]:
    minimo = precio_minimo_venta(costo)
    val = estimar_valor(s, modelo)
    recomendado = max(val.valor, minimo) if val.valor else round(minimo * 1.35, 0)
    return minimo, round(recomendado, 0)


def registrar_compra(s: Session, *, nombre: str, costo: float, linea: str = "Max Steel",
                     modelo: str | None = None, anio: int | None = None,
                     estado_fisico: str | None = None, accesorios: str | None = None,
                     foto: str | None = None, fecha_compra: date | None = None,
                     precio_minimo: float | None = None, precio_recomendado: float | None = None,
                     notas: str | None = None, oportunidad: Oportunidad | None = None) -> Producto:
    if costo < 0:
        raise ErrorInventario("El costo no puede ser negativo")
    pmin, prec = precios_sugeridos(s, costo, modelo)
    p = Producto(
        codigo=siguiente_codigo(s, linea), nombre=nombre.strip(), linea=linea, modelo=modelo,
        anio=anio, foto=foto, estado_fisico=_estado_fisico(estado_fisico), accesorios=accesorios,
        costo_adquisicion=costo, precio_minimo=precio_minimo or pmin,
        precio_recomendado=precio_recomendado or prec,
        fecha_compra=fecha_compra or date.today(), notas=notas,
        oportunidad_id=oportunidad.id if oportunidad else None,
    )
    s.add(p)
    if oportunidad:
        oportunidad.estado = EstadoOportunidad.comprada
    s.flush()
    registrar(s, "inventario", "compra", f"{p.codigo} {p.nombre} costo={costo}")
    return p


def registrar_compra_desde_oportunidad(s: Session, op: Oportunidad, costo_real: float,
                                       foto: str | None = None) -> Producto:
    return registrar_compra(
        s, nombre=op.modelo_identificado or op.titulo, costo=costo_real,
        linea=op.linea if op.linea in PREFIJOS else "Max Steel", modelo=op.modelo_identificado,
        estado_fisico=op.estado_fisico, foto=foto or op.foto_url,
        notas=f"Comprado de {op.plataforma}: {op.url}", oportunidad=op)


CAMPOS_EDITABLES = {"nombre", "linea", "modelo", "anio", "foto", "estado_fisico", "accesorios",
                    "costo_adquisicion", "precio_minimo", "precio_recomendado", "fecha_compra", "notas"}


def actualizar(s: Session, p: Producto, **cambios) -> Producto:
    for k, v in cambios.items():
        if k not in CAMPOS_EDITABLES:
            raise ErrorInventario(f"Campo no editable: {k}")
        if k == "estado_fisico":
            v = _estado_fisico(v)
        setattr(p, k, v)
    registrar(s, "inventario", "actualizar", f"{p.codigo}: {', '.join(cambios)}")
    return p


def cambiar_estado(s: Session, p: Producto, estado: EstadoProducto) -> Producto:
    if p.estado == EstadoProducto.vendido and estado != EstadoProducto.vendido:
        raise ErrorInventario("Un producto vendido no puede volver a disponible; anula la venta primero")
    p.estado = estado
    registrar(s, "inventario", "estado", f"{p.codigo} → {estado.value}")
    return p


def registrar_venta(s: Session, p: Producto, precio_venta: float, *, canal: str | None = None,
                    comision: float | None = None, envio: float = 0.0,
                    prospecto_id: int | None = None, fecha: date | None = None) -> Venta:
    """Registra una venta que TÚ cerraste (con tu autorización explícita)."""
    if p.estado == EstadoProducto.vendido:
        raise ErrorInventario(f"{p.codigo} ya está vendido")
    if precio_venta <= 0:
        raise ErrorInventario("Precio de venta inválido")
    if comision is None:
        comision = round(precio_venta * get_settings().comision_venta, 2) if canal in (
            "mercadolibre", "ebay") else 0.0
    v = Venta(producto=p, precio_venta=precio_venta, comision=comision, envio=envio,
              canal=canal, prospecto_id=prospecto_id, fecha=fecha or date.today())
    s.add(v)
    p.estado = EstadoProducto.vendido
    if p.modelo:
        s.add(PrecioReferencia(modelo=p.modelo, precio=precio_venta, fuente="venta_propia", vendido=True))
    s.flush()
    registrar(s, "inventario", "venta", f"{p.codigo} vendido en {precio_venta} (utilidad {v.utilidad:.0f})")
    return v
