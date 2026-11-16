"""Notificaciones por Telegram (Bot API oficial). Solo envía mensajes a TU chat."""
from __future__ import annotations

import html
import logging

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{metodo}"


class TelegramError(Exception):
    def __init__(self, codigo: int, descripcion: str) -> None:
        super().__init__(f"Telegram {codigo}: {descripcion}")
        self.codigo = codigo


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

    # ------------------------------------------------ recepción (bot)
    def actualizaciones(self, offset: int | None, espera: int = 25) -> list[dict]:
        """Long polling con getUpdates. Lanza TelegramError si la API responde con error."""
        r = self.http.post(
            API.format(token=self.cfg.telegram_bot_token, metodo="getUpdates"),
            json={"offset": offset, "timeout": espera, "allowed_updates": ["message"]},
            timeout=espera + 15)
        j = r.json()
        if not j.get("ok"):
            raise TelegramError(r.status_code, j.get("description", ""))
        return j.get("result", [])

    def descargar_archivo(self, file_id: str) -> tuple[bytes, str]:
        """Descarga un archivo enviado al bot. Devuelve (bytes, ruta_en_telegram)."""
        r = self.http.post(API.format(token=self.cfg.telegram_bot_token, metodo="getFile"),
                           json={"file_id": file_id})
        j = r.json()
        if not j.get("ok"):
            raise TelegramError(r.status_code, j.get("description", ""))
        ruta = j["result"]["file_path"]
        datos = self.http.get(
            f"https://api.telegram.org/file/bot{self.cfg.telegram_bot_token}/{ruta}").content
        return datos, ruta

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
