"""AGENTE 4 — VENDEDOR.

Prepara publicaciones, precios, respuestas, seguimientos, promociones y
paquetes. Reglas de seguridad aplicadas en código:
  * Las publicaciones en grupos de Facebook solo se generan para grupos
    de tema Max Steel marcados como autorizados por ti.
  * Nada se publica ni se envía automáticamente: todo queda como borrador
    que tú apruebas y copias/pegas.
  * Solo se preparan respuestas para prospectos con consentimiento
    (te escribieron o aceptaron ser contactados). Sin mensajes masivos.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from itertools import combinations

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ajustes import registrar
from app.config import get_settings
from app.ia import bloque_imagen, get_claude
from app.models import (
    Canal, EstadoProducto, EstadoProspecto, Interaccion, Producto, Prospecto, Publicacion,
)
from app.pricing import estimar_valor


class ErrorVendedor(ValueError):
    pass


class TextoPublicacion(BaseModel):
    titulo: str = Field(description="Máx. 80 caracteres, atractivo y honesto")
    descripcion: str = Field(description="Descripción comercial en español, con estado y accesorios")


class Respuesta(BaseModel):
    respuesta: str
    requiere_tu_decision: bool = Field(description="True si pide descuento, apartado, cambio, etc.")
    nota_interna: str


SISTEMA_VENTAS = """Eres el agente VENDEDOR de un coleccionista que revende figuras Max Steel y \
Dragon Ball GT. Escribes en español neutro de México, cercano y honesto.
Reglas: nunca exageres el estado, menciona defectos y accesorios faltantes, nunca presentes un \
bootleg como original, no inventes datos que no se te dan, no prometas envíos ni fechas que no \
estén indicadas, y no cierres tratos: el dueño aprueba todo."""

ESTADO_LEGIBLE = {
    "sellado": "nuevo en caja sellada", "caja_abierta": "caja abierta, completo",
    "excelente": "excelente estado", "bueno": "buen estado", "regular": "con detalles de uso",
    "malo": "para reparar o piezas", "desconocido": "ver fotos",
}


# ------------------------------------------------------------ Canales
def validar_canal(canal: Canal) -> None:
    if not canal.autorizado:
        raise ErrorVendedor(
            f"El canal '{canal.nombre}' no está marcado como autorizado. Confirma que eres "
            "miembro y que sus reglas permiten ventas antes de usarlo.")
    if canal.tipo == "grupo_facebook" and canal.tema != "max_steel":
        raise ErrorVendedor("Las publicaciones en grupos de Facebook solo se crean en grupos de Max Steel.")


def canales_sugeridos(s: Session, productos: list[Producto]) -> list[Canal]:
    lineas = {p.linea for p in productos}
    canales = s.scalars(select(Canal).where(Canal.autorizado)).all()
    out = []
    for c in canales:
        if c.tipo == "grupo_facebook":
            if c.tema == "max_steel" and lineas == {"Max Steel"}:
                out.append(c)
        else:
            out.append(c)
    return out


# ------------------------------------------------------------ Precios
def recomendar_precio(s: Session, p: Producto) -> dict:
    val = estimar_valor(s, p.modelo)
    dias = (date.today() - p.fecha_compra).days if p.fecha_compra else 0
    base = val.valor or p.precio_recomendado or (p.precio_minimo or 0) * 1.3
    factor = 1.0
    if dias > 120:
        factor = 0.88
    elif dias > 60:
        factor = 0.94
    precio = max(round(base * factor, -1), p.precio_minimo or 0)
    return {"precio": precio, "minimo": p.precio_minimo, "valor_mercado": val.valor,
            "fuente": val.fuente, "dias_en_inventario": dias,
            "motivo": (f"Valor de mercado ({val.fuente}) {val.valor:,.0f}" if val.valor
                       else "Sin comparables: basado en tu precio recomendado")
                      + (f"; descuento por {dias} días en inventario" if factor < 1 else "")}


# ------------------------------------------------------------ Publicaciones
def _texto_reglas(productos: list[Producto], precio: float, moneda: str) -> TextoPublicacion:
    if len(productos) == 1:
        p = productos[0]
        titulo = f"{p.nombre}" + (f" ({p.anio})" if p.anio else "") + f" - {ESTADO_LEGIBLE[p.estado_fisico.value]}"
        desc = (f"🔥 {p.nombre} — {p.linea}" + (f" {p.anio}" if p.anio else "") + "\n"
                f"Estado: {ESTADO_LEGIBLE[p.estado_fisico.value]}\n"
                f"Accesorios: {p.accesorios or 'solo la figura'}\n"
                f"Precio: ${precio:,.0f} {moneda}\n"
                f"Código: {p.codigo}\n\nPregunta por envíos y entregas. Más fotos por mensaje.")
    else:
        nombres = ", ".join(p.nombre for p in productos)
        titulo = f"Paquete {len(productos)} figuras: {nombres}"[:80]
        lineas = "\n".join(f"• {p.nombre} — {ESTADO_LEGIBLE[p.estado_fisico.value]}"
                           f"{' — ' + p.accesorios if p.accesorios else ''} ({p.codigo})" for p in productos)
        desc = (f"📦 Paquete de {len(productos)} figuras\n{lineas}\n\n"
                f"Precio por todo el paquete: ${precio:,.0f} {moneda}\n"
                "Pregunta por envíos y entregas. Más fotos por mensaje.")
    return TextoPublicacion(titulo=titulo[:80], descripcion=desc)


def generar_publicacion(s: Session, producto_ids: list[int], canal_id: int | None = None,
                        precio: float | None = None) -> Publicacion:
    productos = [s.get(Producto, i) for i in producto_ids]
    if not productos or any(p is None for p in productos):
        raise ErrorVendedor("Producto no encontrado")
    if any(p.estado != EstadoProducto.disponible for p in productos):
        raise ErrorVendedor("Solo se pueden publicar productos disponibles")
    canal = s.get(Canal, canal_id) if canal_id else None
    if canal:
        validar_canal(canal)
        if canal.tipo == "grupo_facebook" and any(p.linea != "Max Steel" for p in productos):
            raise ErrorVendedor("Ese grupo es de Max Steel: no publiques otras líneas ahí.")
    moneda = get_settings().moneda
    if precio is None:
        suma = sum(recomendar_precio(s, p)["precio"] for p in productos)
        precio = round(suma * (0.9 if len(productos) > 1 else 1.0), -1)
    minimo = sum(p.precio_minimo or 0 for p in productos)
    if precio < minimo:
        raise ErrorVendedor(f"El precio {precio:,.0f} está por debajo de tu mínimo ({minimo:,.0f})")

    texto = None
    claude = get_claude()
    if claude.disponible:
        contenido: list[dict] = [b for b in (bloque_imagen(p.foto) for p in productos[:4]) if b]
        datos = "\n".join(
            f"- {p.nombre} | línea {p.linea} | modelo {p.modelo or '-'} | año {p.anio or '-'} | "
            f"estado {ESTADO_LEGIBLE[p.estado_fisico.value]} | accesorios {p.accesorios or 'ninguno'} | "
            f"notas {p.notas or '-'}" for p in productos)
        contenido.append({"type": "text", "text": (
            f"Redacta una publicación de venta para {'un paquete' if len(productos) > 1 else 'una figura'}.\n"
            f"Canal: {canal.nombre + ' (' + canal.tipo + ')' if canal else 'general'}\n"
            f"Reglas del canal: {canal.reglas if canal and canal.reglas else 'no indicadas'}\n"
            f"Precio: {precio:,.0f} {moneda}\nProductos:\n{datos}")})
        texto = claude.json(sistema=SISTEMA_VENTAS, contenido=contenido, salida=TextoPublicacion,
                            effort="low")
    if texto is None:
        texto = _texto_reglas(productos, precio, moneda)

    pub = Publicacion(canal_id=canal.id if canal else None,
                      productos_ids=",".join(str(p.id) for p in productos),
                      titulo=texto.titulo[:300], descripcion=texto.descripcion, precio=precio)
    s.add(pub)
    s.flush()
    registrar(s, "vendedor", "borrador_publicacion", f"#{pub.id} {pub.titulo}")
    return pub


# ------------------------------------------------------------ Prospectos
def registrar_pregunta(s: Session, prospecto: Prospecto, pregunta: str) -> Interaccion:
    i = Interaccion(prospecto=prospecto, direccion="entrante", texto=pregunta, aprobado=True)
    s.add(i)
    if prospecto.estado == EstadoProspecto.nuevo:
        prospecto.estado = EstadoProspecto.interesado
    return i


def preparar_respuesta(s: Session, prospecto: Prospecto, pregunta: str) -> Interaccion:
    if not prospecto.consentimiento:
        raise ErrorVendedor("Este prospecto no ha dado consentimiento para ser contactado.")
    registrar_pregunta(s, prospecto, pregunta)
    disponibles = s.scalars(select(Producto).where(Producto.estado == EstadoProducto.disponible)).all()
    inventario = "\n".join(f"- {p.codigo} {p.nombre} ({p.linea}) {ESTADO_LEGIBLE[p.estado_fisico.value]}, "
                           f"precio {p.precio_recomendado:,.0f}, mínimo {p.precio_minimo:,.0f}"
                           for p in disponibles[:60])
    historial = "\n".join(f"[{x.direccion}] {x.texto}" for x in prospecto.interacciones[-10:])
    resp = get_claude().json(
        sistema=SISTEMA_VENTAS + "\nNunca reveles el precio mínimo; úsalo solo para decidir si "
                                 "requiere_tu_decision.",
        contenido=(f"Cliente: {prospecto.nombre}. Intereses: {prospecto.intereses or '-'}\n"
                   f"Historial:\n{historial}\n\nInventario disponible:\n{inventario}\n\n"
                   f"Nueva pregunta del cliente: {pregunta}\n\nPrepara la respuesta."),
        salida=Respuesta, effort="low")
    texto = resp.respuesta if resp else (
        f"¡Hola {prospecto.nombre}! Gracias por escribir. Déjame revisar y te confirmo enseguida "
        "disponibilidad, fotos y precio.")
    nota = resp.nota_interna if resp else "Respuesta genérica (IA no configurada)."
    i = Interaccion(prospecto=prospecto, direccion="saliente", texto=texto, borrador_ia=True,
                    aprobado=False)
    s.add(i)
    registrar(s, "vendedor", "borrador_respuesta", f"prospecto #{prospecto.id}: {nota}")
    return i


def aprobar_interaccion(s: Session, i: Interaccion, texto_final: str | None = None) -> Interaccion:
    """Tú apruebas el borrador; luego lo envías tú mismo por el canal correspondiente."""
    if texto_final:
        i.texto = texto_final
    i.aprobado = True
    i.prospecto.proximo_seguimiento = date.today() + timedelta(days=3)
    registrar(s, "vendedor", "respuesta_aprobada", f"prospecto #{i.prospecto_id}")
    return i


def seguimientos_pendientes(s: Session, hoy: date | None = None) -> list[Prospecto]:
    hoy = hoy or date.today()
    return list(s.scalars(select(Prospecto).where(
        Prospecto.consentimiento,
        Prospecto.estado.in_([EstadoProspecto.interesado, EstadoProspecto.negociando]),
        Prospecto.proximo_seguimiento.is_not(None), Prospecto.proximo_seguimiento <= hoy)))


# ------------------------------------------------------------ Promociones
def recomendar_promociones(s: Session) -> list[dict]:
    hoy = date.today()
    recs = []
    for p in s.scalars(select(Producto).where(Producto.estado == EstadoProducto.disponible)):
        dias = (hoy - p.fecha_compra).days if p.fecha_compra else 0
        if dias >= 60:
            r = recomendar_precio(s, p)
            recs.append({"producto": p, "dias": dias, "precio_promocion": r["precio"],
                         "motivo": f"{dias} días sin venderse"})
    return sorted(recs, key=lambda r: -r["dias"])


def recomendar_paquetes(s: Session, max_paquetes: int = 5) -> list[dict]:
    """Agrupa figuras de la misma línea (2-3) con 10 % de descuento sobre la suma."""
    disponibles = s.scalars(select(Producto).where(Producto.estado == EstadoProducto.disponible)).all()
    por_linea: dict[str, list[Producto]] = {}
    for p in disponibles:
        por_linea.setdefault(p.linea, []).append(p)
    paquetes = []
    for linea, items in por_linea.items():
        items = sorted(items, key=lambda p: p.fecha_compra or date.today())  # los más viejos primero
        for tam in (3, 2):
            for combo in combinations(items[:6], tam):
                suma = sum(p.precio_recomendado or 0 for p in combo)
                minimo = sum(p.precio_minimo or 0 for p in combo)
                precio = max(round(suma * 0.9, -1), minimo)
                paquetes.append({"linea": linea, "productos": list(combo), "precio": precio,
                                 "ahorro": round(suma - precio, 0)})
    vistos, out = set(), []
    for pq in sorted(paquetes, key=lambda x: -x["ahorro"]):
        ids = {p.id for p in pq["productos"]}
        if ids & vistos:
            continue
        vistos |= ids
        out.append(pq)
        if len(out) >= max_paquetes:
            break
    return out
