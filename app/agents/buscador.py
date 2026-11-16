"""AGENTE 1 — BUSCADOR.

Recorre las búsquedas activas en las fuentes autorizadas (APIs oficiales),
guarda publicaciones nuevas, las manda al Evaluador y genera alertas.
Nunca compra ni contacta a nadie.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ajustes import presupuesto_maximo, puntuacion_minima, registrar
from app.agents import evaluador
from app.config import get_settings
from app.models import Alerta, Busqueda, EstadoOportunidad, Oportunidad
from app.notifications.telegram import Telegram, formato_alerta
from app.sources.base import ErrorFuente, Fuente, Publicacion
from app.sources.registro import fuentes_activas

log = logging.getLogger(__name__)


@dataclass
class ResultadoCiclo:
    nuevas: int = 0
    evaluadas: int = 0
    alertas: int = 0
    errores: list[str] = field(default_factory=list)


def guardar_publicacion(s: Session, pub: Publicacion, termino: str | None = None) -> Oportunidad | None:
    """Guarda una publicación si no existía. Devuelve None si ya estaba registrada."""
    existe = s.scalar(select(Oportunidad.id).where(
        Oportunidad.plataforma == pub.plataforma, Oportunidad.id_externo == pub.id_externo))
    if existe:
        return None
    cfg = get_settings()
    nota = ""
    precio, envio = pub.precio, pub.envio or 0
    if pub.moneda and pub.moneda != cfg.moneda:
        precio = cfg.a_moneda_local(pub.precio, pub.moneda)
        envio = cfg.a_moneda_local(envio, pub.moneda)
        nota = f"[Precio original: {pub.precio:,.2f} {pub.moneda}]\n"
    op = Oportunidad(
        plataforma=pub.plataforma, id_externo=pub.id_externo, url=pub.url, titulo=pub.titulo,
        descripcion=(nota + (f"Condición: {pub.condicion}\n" if pub.condicion else "")
                     + (pub.descripcion or "")) or None,
        precio=precio, moneda=cfg.moneda, envio=envio, ubicacion=pub.ubicacion,
        foto_url=pub.foto_url, vendedor=pub.vendedor, termino_busqueda=termino,
    )
    s.add(op)
    s.flush()
    return op


def alertar(s: Session, op: Oportunidad, telegram: Telegram | None = None) -> bool:
    """Crea la alerta en el panel y la envía a Telegram si cumple los criterios."""
    if op.puntuacion is None or op.puntuacion < puntuacion_minima(s):
        return False
    if op.precio + (op.envio or 0) > presupuesto_maximo(s):
        return False
    if op.estado != EstadoOportunidad.nueva:
        return False
    s.add(Alerta(oportunidad_id=op.id, canal="panel"))
    tg = telegram or Telegram()
    if tg.activo:
        ok, err = tg.alerta(formato_alerta(op, get_settings().moneda), op.foto_url, op.url)
        s.add(Alerta(oportunidad_id=op.id, canal="telegram", ok=ok, error=err))
    op.estado = EstadoOportunidad.alertada
    return True


def procesar_nueva(s: Session, op: Oportunidad, telegram: Telegram | None = None) -> bool:
    evaluador.evaluar(s, op, get_settings().moneda)
    return alertar(s, op, telegram)


def ciclo(s: Session, fuentes: list[Fuente] | None = None, telegram: Telegram | None = None,
          limite_por_busqueda: int = 50) -> ResultadoCiclo:
    res = ResultadoCiclo()
    fuentes = fuentes_activas() if fuentes is None else fuentes
    if not fuentes:
        res.errores.append("No hay fuentes configuradas (eBay / Mercado Libre).")
        return res
    terminos = s.scalars(select(Busqueda.termino).where(Busqueda.activa)).all()
    for fuente in fuentes:
        for termino in terminos:
            try:
                pubs = fuente.buscar(termino, limite_por_busqueda)
            except (ErrorFuente, Exception) as e:  # una fuente caída no detiene el ciclo
                msg = f"{fuente.nombre}/{termino}: {e}"
                log.warning(msg)
                res.errores.append(msg)
                continue
            for pub in pubs:
                op = guardar_publicacion(s, pub, termino)
                if op is None:
                    continue
                res.nuevas += 1
                if procesar_nueva(s, op, telegram):
                    res.alertas += 1
                res.evaluadas += 1
                s.commit()
    registrar(s, "buscador", "ciclo",
              f"nuevas={res.nuevas} alertas={res.alertas} errores={len(res.errores)}")
    s.commit()
    return res
