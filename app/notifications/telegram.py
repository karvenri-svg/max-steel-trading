"""Notificaciones por Telegram (Bot API oficial). Solo envía mensajes a TU chat."""
from __future__ import annotations

import html
import logging

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{metodo}"


class Telegram:
    def __init__(self, http: httpx.Client | None = None) -> None:
        self.cfg = get_settings()
        self.http = http or httpx.Client(timeout=20)

    @property
    def activo(self) -> bool:
        return self.cfg.telegram_activo

    def _llamar(self, metodo: str, datos: dict) -> tuple[bool, str | None]:
        if not self.activo:
            return False, "Telegram no configurado"
        try:
            r = self.http.post(API.format(token=self.cfg.telegram_bot_token, metodo=metodo),
                               json={"chat_id": self.cfg.telegram_chat_id, **datos})
            j = r.json()
            return (True, None) if j.get("ok") else (False, j.get("description", r.text[:200]))
        except (httpx.HTTPError, ValueError) as e:
            return False, str(e)

    def mensaje(self, texto: str) -> tuple[bool, str | None]:
        return self._llamar("sendMessage", {"text": texto, "parse_mode": "HTML",
                                            "disable_web_page_preview": True})

    def alerta(self, texto: str, foto_url: str | None, url: str | None) -> tuple[bool, str | None]:
        teclado = {"inline_keyboard": [[{"text": "Ver publicación", "url": url}]]} if url else None
        datos = {"parse_mode": "HTML"}
        if teclado:
            datos["reply_markup"] = teclado
        if foto_url and foto_url.startswith("https://"):
            ok, err = self._llamar("sendPhoto", {**datos, "photo": foto_url, "caption": texto[:1024]})
            if ok:
                return ok, err
            log.info("sendPhoto falló (%s); se envía solo texto", err)
        return self._llamar("sendMessage", {**datos, "text": texto,
                                            "disable_web_page_preview": False})


def formato_alerta(op, moneda: str) -> str:
    e = html.escape
    def dinero(v):
        return "—" if v is None else f"${v:,.0f} {moneda}"
    return (
        f"🎯 <b>Oportunidad {op.puntuacion}/100</b>\n"
        f"<b>{e(op.modelo_identificado or op.titulo)}</b>\n"
        f"{e(op.titulo)}\n\n"
        f"💵 Precio: <b>{dinero(op.precio)}</b>"
        + (f" + envío {dinero(op.envio)}" if op.envio else "") + "\n"
        f"📈 Reventa estimada: {dinero(op.valor_reventa)}\n"
        f"💰 Ganancia potencial: <b>{dinero(op.ganancia)}</b>"
        + (f" ({op.margen_pct:.0f}%)" if op.margen_pct is not None else "") + "\n"
        f"🧩 Estado: {e(op.estado_fisico or 'desconocido')}"
        + (f" · riesgo réplica {op.riesgo_replica:.0%}" if op.riesgo_replica is not None else "") + "\n"
        f"📍 {e(op.ubicacion or 'sin ubicación')} · {e(op.plataforma)}\n"
        f"🔗 {e(op.url)}\n\n"
        f"<i>Nada se compra automáticamente. Decide en el panel.</i>"
    )
