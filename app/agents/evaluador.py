"""AGENTE 2 — EVALUADOR.

Identifica el modelo, evalúa estado físico, autenticidad probable y
rentabilidad. Usa Claude (texto + fotografía) si está configurado; si no,
reglas locales. La puntuación final siempre la calcula `scoring.puntuar`
para que sea consistente y explicable.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ajustes import presupuesto_maximo, registrar
from app.ia import bloque_imagen, get_claude
from app.models import CatalogoModelo, Oportunidad, PrecioReferencia
from app.pricing import Valoracion, calcular_margen, estimar_valor
from app.scoring import puntuar

EstadoFisicoIA = Literal["sellado", "caja_abierta", "excelente", "bueno", "regular", "malo", "desconocido"]


class EvaluacionIA(BaseModel):
    es_figura_relevante: bool = Field(description="True si es una figura/juguete Max Steel o Dragon Ball")
    linea: Literal["Max Steel", "Dragon Ball", "Otro"]
    modelo: str = Field(description="Nombre EXACTO del catálogo si coincide; si no, nombre descriptivo")
    anio_aproximado: int | None
    estado_fisico: EstadoFisicoIA
    accesorios_visibles: list[str]
    riesgo_replica: float = Field(description="0.0 = original seguro, 1.0 = réplica segura (no declarada)")
    bootleg_declarado: bool = Field(description="True si el vendedor dice claramente que es bootleg/genérico")
    senales_autenticidad: list[str]
    valor_mercado_estimado: float | None = Field(
        description="Valor de reventa típico en la moneda indicada; null si no tienes base")
    resumen: str = Field(description="2-3 frases en español para el comprador")


SISTEMA = """Eres el agente EVALUADOR de un negocio de compra-reventa de figuras coleccionables \
(Max Steel de Mattel, especialmente 2000-2013, y Dragon Ball GT incluyendo Ozaru y bootlegs).
Analizas una publicación (texto y foto) y devuelves una evaluación objetiva.
- Identifica el modelo usando el catálogo dado; usa el nombre exacto del catálogo cuando coincida.
- Estado físico: basa tu juicio en la foto y la descripción; si no se aprecia, "desconocido".
- Autenticidad: busca señales (logotipo/copyright Mattel o Bird Studio/Shueisha/Toei, calidad de \
pintura, empaque, proporciones). Un bootleg DECLARADO por el vendedor no es engaño: márcalo en \
bootleg_declarado y deja riesgo_replica bajo. riesgo_replica mide el riesgo de réplica NO declarada.
- valor_mercado_estimado: solo si tienes una base razonable; si hay precios comparables, apóyate en ellos. \
Sé conservador; nunca inventes cifras precisas sin base (usa null).
- No eres quien decide la compra: solo informas."""

_PAL_ESTADO = [
    ("sellado", ["sellado", "misb", "mib", "nuevo en caja", "nib", "sealed", "new in box", "moc"]),
    ("caja_abierta", ["caja abierta", "open box", "con caja"]),
    ("malo", ["roto", "rota", "dañado", "danado", "incompleto", "para piezas", "broken", "parts"]),
    ("regular", ["detalles", "desgaste", "despintado", "flojo", "loose joints", "worn"]),
    ("excelente", ["excelente", "como nuevo", "impecable", "mint", "excellent"]),
    ("bueno", ["buen estado", "usado", "used", "good"]),
]
_PAL_REPLICA = ["replica", "réplica", "copia", "pirata", "bootleg", "generico", "genérico",
                "knock off", "knockoff", "fake", "custom"]


def normalizar(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _contiene(texto: str, palabra: str) -> bool:
    return re.search(rf"\b{re.escape(normalizar(palabra))}\b", texto) is not None


def identificar_por_reglas(s: Session, texto: str) -> CatalogoModelo | None:
    """Devuelve el modelo con más coincidencias. Las entradas genéricas (cuyas palabras
    clave son solo términos de réplica, p. ej. 'bootleg') se usan únicamente si
    ningún modelo específico coincide."""
    t = normalizar(texto)
    replica = {normalizar(x) for x in _PAL_REPLICA}
    mejor, mejor_clave = None, (0, 0)
    for cat in s.scalars(select(CatalogoModelo)):
        claves = [x.strip() for x in cat.palabras_clave.split(",") if x.strip()]
        p = sum(len(k) for k in claves if _contiene(t, k))
        if not p:
            continue
        especifico = 0 if all(normalizar(k) in replica or normalizar(k) == "china" for k in claves) else 1
        if (especifico, p) > mejor_clave:
            mejor, mejor_clave = cat, (especifico, p)
    return mejor


def evaluar_por_reglas(s: Session, op: Oportunidad) -> EvaluacionIA:
    texto = f"{op.titulo} {op.descripcion or ''}"
    t = normalizar(texto)
    cat = identificar_por_reglas(s, texto)
    estado = next((e for e, pals in _PAL_ESTADO if any(_contiene(t, p) for p in pals)), "desconocido")
    replica = any(_contiene(t, p) for p in _PAL_REPLICA)
    linea = cat.linea if cat else ("Dragon Ball" if "dragon ball" in t else
                                    "Max Steel" if "max steel" in t else "Otro")
    return EvaluacionIA(
        es_figura_relevante=linea != "Otro",
        linea=linea,
        modelo=cat.nombre if cat else op.titulo[:120],
        anio_aproximado=cat.anio if cat else None,
        estado_fisico=estado,
        accesorios_visibles=[],
        riesgo_replica=0.2 if replica else 0.35,
        bootleg_declarado=replica,
        senales_autenticidad=["evaluación por palabras clave (sin IA)"],
        valor_mercado_estimado=None,
        resumen="Evaluación automática por reglas. Configura ANTHROPIC_API_KEY para análisis de fotos.",
    )


def evaluar_con_claude(s: Session, op: Oportunidad, moneda: str) -> EvaluacionIA | None:
    claude = get_claude()
    if not claude.disponible:
        return None
    catalogo = [{"nombre": c.nombre, "linea": c.linea, "anio": c.anio,
                 "autenticidad": c.notas_autenticidad}
                for c in s.scalars(select(CatalogoModelo))]
    comparables = [{"modelo": p.modelo, "precio": p.precio, "vendido": p.vendido}
                   for p in s.scalars(select(PrecioReferencia)
                                      .order_by(PrecioReferencia.fecha.desc()).limit(40))]
    contenido: list[dict] = []
    img = bloque_imagen(op.foto_url)
    if img:
        contenido.append(img)
    contenido.append({"type": "text", "text": (
        f"Moneda de referencia: {moneda}\n"
        f"CATÁLOGO:\n{json.dumps(catalogo, ensure_ascii=False)}\n\n"
        f"PRECIOS COMPARABLES RECIENTES:\n{json.dumps(comparables, ensure_ascii=False)}\n\n"
        f"PUBLICACIÓN ({op.plataforma}):\nTítulo: {op.titulo}\n"
        f"Precio: {op.precio} {op.moneda} (+ envío {op.envio})\n"
        f"Ubicación: {op.ubicacion}\nDescripción: {(op.descripcion or '')[:3000]}\n"
        f"{'(sin foto disponible)' if not img else ''}"
    )})
    return claude.json(sistema=SISTEMA, contenido=contenido, salida=EvaluacionIA)


def evaluar(s: Session, op: Oportunidad, moneda: str = "MXN") -> Oportunidad:
    ev = evaluar_con_claude(s, op, moneda)
    origen = "claude"
    if ev is None:
        ev, origen = evaluar_por_reglas(s, op), "reglas"

    # Valor: comparables/ventas/catálogo primero; la estimación de IA solo como último recurso.
    val = estimar_valor(s, ev.modelo, excluir_precio=op.precio)
    if val.valor is None and ev.valor_mercado_estimado:
        val = Valoracion(ev.valor_mercado_estimado, 0, "ia")

    margen = calcular_margen(op.precio, op.envio, val.valor) if val.valor else None
    p = puntuar(
        precio=op.precio + (op.envio or 0),
        margen_pct=margen.margen_pct if margen else None,
        ganancia=margen.ganancia if margen else None,
        presupuesto=presupuesto_maximo(s),
        riesgo_replica=ev.riesgo_replica,
        estado_fisico=ev.estado_fisico,
        confianza_valor=val.confianza,
        bootleg_declarado=ev.bootleg_declarado,
    )
    if not ev.es_figura_relevante:
        p.total = min(p.total, 10)
        p.avisos.append("no parece una figura del nicho")

    op.evaluada, op.evaluada_con = True, origen
    op.linea, op.modelo_identificado = ev.linea, ev.modelo
    op.estado_fisico, op.riesgo_replica = ev.estado_fisico, ev.riesgo_replica
    op.valor_reventa = val.valor
    op.ganancia = margen.ganancia if margen else None
    op.margen_pct = margen.margen_pct if margen else None
    op.puntuacion = p.total
    op.razonamiento = (
        f"{ev.resumen}\n\nPuntuación: {p.explicar()}\nValor: fuente={val.fuente}, "
        f"comparables={val.comparables}\nSeñales: {'; '.join(ev.senales_autenticidad)}"
        + (f"\nAccesorios: {', '.join(ev.accesorios_visibles)}" if ev.accesorios_visibles else "")
    )

    # Precio publicado como dato de mercado (para comparar en el futuro).
    if ev.es_figura_relevante and ev.linea != "Otro":
        s.add(PrecioReferencia(modelo=ev.modelo, precio=op.precio, fuente=op.plataforma, vendido=False))
    registrar(s, "evaluador", "evaluar", f"#{op.id} {op.titulo[:80]} → {p.total} ({origen})")
    return op
