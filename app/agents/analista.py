"""AGENTE 5 — ANALISTA. Métricas del negocio y recomendaciones de compra/venta."""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ajustes import puntuacion_minima, registrar
from app.ia import get_claude
from app.models import EstadoOportunidad, EstadoProducto, Oportunidad, Producto, Venta
from app.pricing import estimar_valor


def metricas(s: Session) -> dict:
    productos = s.scalars(select(Producto)).all()
    ventas = s.scalars(select(Venta)).all()
    disponibles = [p for p in productos if p.estado != EstadoProducto.vendido]

    inversion_total = sum(p.costo_adquisicion for p in productos)
    inversion_en_stock = sum(p.costo_adquisicion for p in disponibles)
    valor_inventario = 0.0
    for p in disponibles:
        v = estimar_valor(s, p.modelo).valor
        valor_inventario += v or p.precio_recomendado or p.costo_adquisicion
    ingresos = sum(v.precio_venta for v in ventas)
    ganancia = sum(v.utilidad for v in ventas)
    costo_vendido = sum(v.producto.costo_adquisicion for v in ventas)
    pendientes = s.scalar(select(func.count()).select_from(Oportunidad).where(
        Oportunidad.estado.in_([EstadoOportunidad.nueva, EstadoOportunidad.alertada,
                                EstadoOportunidad.interesa, EstadoOportunidad.contacto_aprobado]),
        Oportunidad.puntuacion >= puntuacion_minima(s)))
    return {
        "total_compradas": len(productos),
        "inventario_disponible": sum(1 for p in productos if p.estado == EstadoProducto.disponible),
        "reservadas": sum(1 for p in productos if p.estado == EstadoProducto.reservado),
        "inversion_total": round(inversion_total, 2),
        "inversion_en_stock": round(inversion_en_stock, 2),
        "valor_estimado_inventario": round(valor_inventario, 2),
        "ganancia_latente": round(valor_inventario - inversion_en_stock, 2),
        "ingresos": round(ingresos, 2),
        "ganancias_realizadas": round(ganancia, 2),
        "ofertas_encontradas": s.scalar(select(func.count()).select_from(Oportunidad)),
        "oportunidades_pendientes": pendientes or 0,
        "ventas_realizadas": len(ventas),
        "margen_utilidad_pct": round(ganancia / costo_vendido * 100, 1) if costo_vendido else 0.0,
        "margen_sobre_venta_pct": round(ganancia / ingresos * 100, 1) if ingresos else 0.0,
    }


def rendimiento_por_modelo(s: Session) -> list[dict]:
    datos: dict[str, dict] = defaultdict(lambda: {"ventas": 0, "utilidad": 0.0, "dias": []})
    for v in s.scalars(select(Venta)):
        clave = v.producto.modelo or v.producto.nombre
        d = datos[clave]
        d["ventas"] += 1
        d["utilidad"] += v.utilidad
        if v.producto.fecha_compra:
            d["dias"].append((v.fecha - v.producto.fecha_compra).days)
    out = []
    for modelo, d in datos.items():
        out.append({"modelo": modelo, "ventas": d["ventas"],
                    "utilidad_promedio": round(d["utilidad"] / d["ventas"], 0),
                    "dias_promedio_venta": round(sum(d["dias"]) / len(d["dias"]), 0) if d["dias"] else None})
    return sorted(out, key=lambda x: -x["utilidad_promedio"])


def recomendaciones(s: Session) -> dict:
    """Reglas simples y transparentes; Claude puede ampliarlas con `informe()`."""
    comprar = [f"Prioriza '{r['modelo']}': utilidad promedio ${r['utilidad_promedio']:,.0f}"
               + (f", se vende en ~{r['dias_promedio_venta']:.0f} días" if r["dias_promedio_venta"] is not None else "")
               for r in rendimiento_por_modelo(s)[:3] if r["utilidad_promedio"] > 0]
    vender = []
    hoy = date.today()
    for p in s.scalars(select(Producto).where(Producto.estado == EstadoProducto.disponible)):
        dias = (hoy - p.fecha_compra).days if p.fecha_compra else 0
        val = estimar_valor(s, p.modelo).valor
        if val and p.costo_adquisicion and val >= p.costo_adquisicion * 2:
            vender.append(f"{p.codigo} {p.nombre}: valor de mercado ${val:,.0f} vs costo "
                          f"${p.costo_adquisicion:,.0f} — buen momento para vender")
        elif dias >= 90:
            vender.append(f"{p.codigo} {p.nombre}: {dias} días en inventario — considera promoción o paquete")
    if not comprar:
        comprar = ["Aún no hay ventas suficientes para comparar modelos; registra ventas para afinar."]
    return {"comprar": comprar, "vender": vender[:10]}


class Informe(BaseModel):
    resumen: str
    que_comprar: list[str]
    que_vender: list[str]
    riesgos: list[str]


def informe(s: Session) -> Informe | None:
    claude = get_claude()
    if not claude.disponible:
        return None
    m = metricas(s)
    r = rendimiento_por_modelo(s)
    top = s.scalars(select(Oportunidad).where(Oportunidad.puntuacion.is_not(None))
                    .order_by(Oportunidad.puntuacion.desc()).limit(15)).all()
    ops = "\n".join(f"- {o.modelo_identificado} {o.precio:,.0f} → {o.valor_reventa} (score {o.puntuacion})"
                    for o in top)
    res = claude.json(
        sistema="Eres el ANALISTA de un negocio de reventa de figuras Max Steel y Dragon Ball GT. "
                "Das recomendaciones concretas, basadas solo en los datos dados, en español.",
        contenido=f"Métricas: {m}\nRendimiento por modelo: {r}\nMejores oportunidades abiertas:\n{ops}",
        salida=Informe, effort="medium")
    if res:
        registrar(s, "analista", "informe", res.resumen[:500])
    return res
