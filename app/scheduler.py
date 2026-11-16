"""Tareas programadas (APScheduler, dentro del proceso web)."""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.agents import buscador, vendedor
from app.config import get_settings
from app.db import session_scope
from app.notifications.telegram import Telegram

log = logging.getLogger(__name__)


def tarea_busqueda() -> None:
    with session_scope() as s:
        res = buscador.ciclo(s)
        log.info("Ciclo de búsqueda: %s", res)


def tarea_seguimientos() -> None:
    with session_scope() as s:
        pend = vendedor.seguimientos_pendientes(s)
        if pend:
            nombres = "\n".join(f"• {p.nombre} ({p.contacto or 'sin contacto'})" for p in pend)
            Telegram().mensaje(f"📋 <b>Seguimientos para hoy</b>\n{nombres}")


def iniciar() -> BackgroundScheduler:
    cfg = get_settings()
    sch = BackgroundScheduler(timezone="America/Mexico_City")
    sch.add_job(tarea_busqueda, "interval", minutes=cfg.intervalo_busqueda_min,
                id="busqueda", max_instances=1, coalesce=True)
    sch.add_job(tarea_seguimientos, "cron", hour=9, minute=7, id="seguimientos")
    sch.start()
    return sch
