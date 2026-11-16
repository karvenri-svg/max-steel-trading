"""Bot de Telegram para capturar publicaciones de Facebook desde el teléfono.

Flujo (todo en tu chat privado con el bot):
  1. Compartes el enlace de Facebook al bot (puedes incluir precio y título):
        https://www.facebook.com/marketplace/item/123  $350  Ozaru GT
  2. Opcional: mandas capturas de pantalla de la publicación.
  3. Si falta, mandas el precio (solo el número) y luego /evaluar.
     Si el enlace ya trae precio con /fb, se evalúa al instante.

Seguridad:
  * Solo responde al chat configurado en TELEGRAM_CHAT_ID; ignora a cualquier otra persona.
  * No abre ni descarga las páginas de Facebook: solo usa lo que tú le envías.
  * Usa long polling (getUpdates) de la Bot API oficial: no necesita servidor público.
"""
from __future__ import annotations

import logging
import re
import threading
import uuid
from pathlib import Path

import httpx
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents import analista, buscador, evaluador
from app.ajustes import _get as leer_ajuste
from app.ajustes import puntuacion_minima, registrar, set_ajuste
from app.config import get_settings
from app.db import session_scope
from app.ia import bloque_imagen, get_claude
from app.models import CapturaTelegram, Oportunidad
from app.notifications.telegram import Telegram, TelegramError, formato_alerta
from app.sources.base import Publicacion

log = logging.getLogger(__name__)

MAX_FOTOS = 4
MAX_BYTES_FOTO = 8 * 1024 * 1024
RE_URL = re.compile(
    r"(?:https?://|(?<![\w.])(?:www\.|m\.|web\.)?(?=(?:facebook\.com|fb\.com|fb\.me)/))\S+",
    re.IGNORECASE)
RE_PRECIO = [
    re.compile(r"\$\s*([\d][\d.,]*)"),
    re.compile(r"\b([\d][\d.,]*)\s*(?:pesos|mxn|mx|usd|dlls|dólares|dolares)\b", re.IGNORECASE),
    re.compile(r"\bprecio[:\s]+([\d][\d.,]*)", re.IGNORECASE),
]
RE_SOLO_NUMERO = re.compile(r"^\s*\$?\s*([\d][\d.,]*)\s*(?:pesos|mxn)?\s*$", re.IGNORECASE)

AYUDA = (
    "🦾 <b>Captura de publicaciones de Facebook</b>\n\n"
    "1️⃣ Comparte aquí el enlace de la publicación (Marketplace o grupo).\n"
    "2️⃣ Si quieres, manda capturas de pantalla de la publicación.\n"
    "3️⃣ Manda el precio (solo el número) y luego /evaluar.\n\n"
    "Atajo: <code>/fb enlace 350 Ozaru GT</code> evalúa al instante.\n\n"
    "Comandos: /evaluar · /estado · /cancelar · /resumen · /ayuda\n"
    "<i>Nunca compro ni contacto a nadie: solo evalúo lo que me mandas.</i>"
)


# ------------------------------------------------------------- utilidades
def numero(texto: str) -> float | None:
    """'1,250' → 1250 · '1.250' → 1250 · '350.50' → 350.5 · '1.250,50' → 1250.5"""
    t = texto.strip().rstrip(".,")
    if not t:
        return None
    if "," in t and "." in t:
        decimal = "," if t.rfind(",") > t.rfind(".") else "."
        miles = "." if decimal == "," else ","
        t = t.replace(miles, "").replace(decimal, ".")
    elif "," in t:
        partes = t.split(",")
        t = t.replace(",", "") if all(len(p) == 3 for p in partes[1:]) else t.replace(",", ".")
    elif t.count(".") > 1 or (t.count(".") == 1 and len(t.split(".")[1]) == 3):
        t = t.replace(".", "")
    try:
        v = float(t)
    except ValueError:
        return None
    return v if v > 0 else None


def extraer_precio(texto: str) -> float | None:
    sin_urls = RE_URL.sub(" ", texto)
    for patron in RE_PRECIO:
        m = patron.search(sin_urls)
        if m and (v := numero(m.group(1))):
            return v
    return None


def normalizar_enlace(url: str) -> tuple[str, str, str]:
    """Devuelve (url_limpia, id_externo, plataforma) sin abrir la página."""
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    p = urlsplit(url.rstrip(").,;"))
    host = p.netloc.lower()
    es_fb = any(d in host for d in ("facebook.com", "fb.com", "fb.me"))
    if es_fb and (m := re.search(r"/marketplace/item/(\d+)", p.path)):
        return f"https://www.facebook.com/marketplace/item/{m.group(1)}/", f"fbmp-{m.group(1)}", "facebook"
    limpia = urlunsplit((p.scheme, p.netloc, p.path, "", ""))  # sin parámetros de rastreo
    if es_fb:
        if (m := re.search(r"/groups/([^/]+)/(?:posts|permalink)/(\d+)", p.path)):
            return limpia, f"fbgr-{m.group(1)}-{m.group(2)}", "facebook_grupo"
        plataforma = "facebook_grupo" if "/groups/" in p.path else "facebook"
        return limpia, limpia, plataforma
    return limpia, limpia, "otra"


def texto_sin_enlaces_ni_precio(texto: str) -> str:
    t = RE_URL.sub(" ", texto)
    for patron in RE_PRECIO:
        t = patron.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip(" -–·|")


class DatosCaptura(BaseModel):
    titulo: str | None = Field(description="Título de la publicación tal como aparece")
    precio: float | None = Field(description="Precio pedido, solo el número")
    moneda: str | None = Field(description="Código ISO de la moneda si se ve (MXN, USD…)")
    ubicacion: str | None
    vendedor: str | None
    descripcion: str | None = Field(description="Texto de la descripción visible, sin inventar")


class _SinTelegram:
    """Para crear la alerta del panel sin reenviarla a Telegram (ya respondemos en el chat)."""
    activo = False


# ------------------------------------------------------------- bot
class BotTelegram:
    def __init__(self, telegram: Telegram | None = None) -> None:
        self.tg = telegram or Telegram()
        self.cfg = get_settings()

    # ---------- entrada
    def procesar(self, s: Session, update: dict) -> None:
        msg = update.get("message") or {}
        chat_id = str((msg.get("chat") or {}).get("id", ""))
        if not chat_id:
            return
        if chat_id != str(self.cfg.telegram_chat_id):
            log.warning("Mensaje ignorado de un chat no autorizado (%s)", chat_id)
            return
        texto = (msg.get("text") or msg.get("caption") or "").strip()
        foto_id = self._id_foto(msg)

        if texto.startswith("/"):
            comando, _, resto = texto.partition(" ")
            comando = comando.split("@")[0].lower()
            manejador = {
                "/start": self._ayuda, "/ayuda": self._ayuda, "/help": self._ayuda,
                "/fb": self._cmd_fb, "/agregar": self._cmd_fb, "/evaluar": self._cmd_evaluar,
                "/precio": self._cmd_precio, "/cancelar": self._cmd_cancelar,
                "/estado": self._cmd_estado, "/resumen": self._cmd_resumen,
            }.get(comando)
            if manejador is None:
                self.responder("No conozco ese comando. Usa /ayuda.")
                return
            manejador(s, chat_id, resto.strip(), foto_id)
            return

        cap = self._activa(s, chat_id)
        enlaces = RE_URL.findall(texto)
        if enlaces:
            self._nueva_captura(s, chat_id, texto, foto_id, evaluar_si_completa=False)
        elif foto_id:
            cap = cap or self._crear(s, chat_id)
            self._agregar_foto(cap, foto_id)
            if texto:
                self._agregar_texto(cap, texto)
            self.responder(self._estado(cap))
        elif cap and (m := RE_SOLO_NUMERO.match(texto)) and (v := numero(m.group(1))):
            cap.precio = v
            self.responder(self._estado(cap))
        elif cap and texto:
            self._agregar_texto(cap, texto)
            self.responder(self._estado(cap))
        else:
            self.responder("Compárteme el enlace de una publicación de Facebook para empezar. /ayuda")

    # ---------- comandos
    def _ayuda(self, s, chat_id, resto, foto_id):
        self.responder(AYUDA)

    def _cmd_fb(self, s, chat_id, resto, foto_id):
        if not RE_URL.search(resto):
            self.responder("Incluye el enlace: <code>/fb https://www.facebook.com/marketplace/item/… 350 título</code>")
            return
        self._nueva_captura(s, chat_id, resto, foto_id, evaluar_si_completa=True)

    def _cmd_precio(self, s, chat_id, resto, foto_id):
        cap = self._activa(s, chat_id)
        v = numero(resto.replace("$", ""))
        if not cap:
            self.responder("No hay ninguna publicación en preparación. Mándame primero el enlace.")
        elif v is None:
            self.responder("Escribe el precio así: <code>/precio 350</code>")
        else:
            cap.precio = v
            self.responder(self._estado(cap))

    def _cmd_evaluar(self, s, chat_id, resto, foto_id):
        cap = self._activa(s, chat_id)
        if not cap:
            self.responder("No hay ninguna publicación en preparación. Mándame el enlace.")
            return
        self._evaluar(s, cap)

    def _cmd_cancelar(self, s, chat_id, resto, foto_id):
        cap = self._activa(s, chat_id)
        if cap:
            cap.activa = False
            self.responder("Captura cancelada.")
        else:
            self.responder("No había nada en preparación.")

    def _cmd_estado(self, s, chat_id, resto, foto_id):
        cap = self._activa(s, chat_id)
        self.responder(self._estado(cap) if cap else "No hay ninguna publicación en preparación.")

    def _cmd_resumen(self, s, chat_id, resto, foto_id):
        m = analista.metricas(s)
        d = lambda v: f"${v:,.0f}"  # noqa: E731
        self.responder(
            "📊 <b>Resumen</b>\n"
            f"Inventario disponible: {m['inventario_disponible']} (invertido {d(m['inversion_en_stock'])})\n"
            f"Valor estimado: {d(m['valor_estimado_inventario'])}\n"
            f"Ventas: {m['ventas_realizadas']} · Ganancia: {d(m['ganancias_realizadas'])} "
            f"({m['margen_utilidad_pct']}% sobre costo)\n"
            f"Oportunidades pendientes: {m['oportunidades_pendientes']}")

    # ---------- capturas
    def _activa(self, s: Session, chat_id: str) -> CapturaTelegram | None:
        return s.scalar(select(CapturaTelegram).where(
            CapturaTelegram.chat_id == chat_id, CapturaTelegram.activa)
            .order_by(CapturaTelegram.id.desc()))

    def _crear(self, s: Session, chat_id: str) -> CapturaTelegram:
        cap = CapturaTelegram(chat_id=chat_id)
        s.add(cap)
        s.flush()
        return cap

    def _nueva_captura(self, s, chat_id, texto, foto_id, evaluar_si_completa: bool):
        anterior = self._activa(s, chat_id)
        aviso = ""
        if anterior and anterior.url:
            anterior.activa = False
            aviso = "ℹ️ Descarté la captura anterior (no se había evaluado).\n\n"
        cap = anterior if anterior and not anterior.url else self._crear(s, chat_id)
        cap.url = RE_URL.search(texto).group(0)
        if (precio := extraer_precio(texto)) is not None:
            cap.precio = precio
        resto = texto_sin_enlaces_ni_precio(texto)
        if evaluar_si_completa and not extraer_precio(texto):
            # En /fb el número suelto después del enlace es el precio: "/fb enlace 350 Ozaru"
            if m := re.match(r"^\$?\s*([\d][\d.,]*)\s*(.*)$", resto):
                if (v := numero(m.group(1))) is not None:
                    cap.precio, resto = v, m.group(2).strip()
        if resto:
            self._agregar_texto(cap, resto)
        if foto_id:
            self._agregar_foto(cap, foto_id)
        if evaluar_si_completa and cap.precio:
            if aviso:
                self.responder(aviso.strip())
            self._evaluar(s, cap)
        else:
            self.responder(aviso + self._estado(cap))

    def _agregar_texto(self, cap: CapturaTelegram, texto: str) -> None:
        if not cap.titulo:
            cap.titulo = texto[:300]
        else:
            cap.descripcion = ((cap.descripcion + "\n") if cap.descripcion else "") + texto

    def _agregar_foto(self, cap: CapturaTelegram, file_id: str) -> None:
        if len(cap.lista_fotos) >= MAX_FOTOS:
            self.responder(f"Ya tengo {MAX_FOTOS} fotos; ignoro las demás.")
            return
        try:
            datos, ruta = self.tg.descargar_archivo(file_id)
        except Exception as e:  # noqa: BLE001
            log.warning("No pude descargar la foto: %s", e)
            self.responder("No pude descargar esa foto; inténtalo de nuevo.")
            return
        ext = Path(ruta).suffix.lower() or ".jpg"
        if ext not in (".jpg", ".jpeg", ".png", ".webp") or len(datos) > MAX_BYTES_FOTO:
            self.responder("Formato o tamaño de imagen no admitido (JPG/PNG/WEBP, máx. 8 MB).")
            return
        nombre = f"tg-{uuid.uuid4().hex}{ext}"
        self.cfg.upload_dir.mkdir(parents=True, exist_ok=True)
        (self.cfg.upload_dir / nombre).write_bytes(datos)
        cap.fotos = ",".join([*cap.lista_fotos, nombre])

    @staticmethod
    def _id_foto(msg: dict) -> str | None:
        if msg.get("photo"):
            return msg["photo"][-1]["file_id"]  # la de mayor resolución
        doc = msg.get("document") or {}
        if str(doc.get("mime_type", "")).startswith("image/"):
            return doc["file_id"]
        return None

    def _estado(self, cap: CapturaTelegram) -> str:
        faltan = []
        if not cap.url:
            faltan.append("el <b>enlace</b>")
        if not cap.precio:
            faltan.append("el <b>precio</b> (manda solo el número)"
                          + (" — o lo leo de tus capturas al /evaluar" if cap.lista_fotos and get_claude().disponible else ""))
        linea = lambda k, v: f"{k}: {v}\n" if v else ""  # noqa: E731
        texto = ("📝 <b>Publicación en preparación</b>\n"
                 + linea("Enlace", cap.url) + linea("Título", cap.titulo)
                 + linea("Precio", f"${cap.precio:,.0f} {self.cfg.moneda}" if cap.precio else None)
                 + f"Fotos: {len(cap.lista_fotos)}\n\n")
        if faltan:
            return texto + "Me falta " + " y ".join(faltan) + "."
        return texto + "Todo listo: manda /evaluar (o más fotos antes)."

    # ---------- evaluación
    def _completar_con_ia(self, cap: CapturaTelegram) -> None:
        """Lee precio, título, ubicación, etc. de las capturas de pantalla (si hay IA)."""
        claude = get_claude()
        if not claude.disponible or not cap.lista_fotos:
            return
        imagenes = [b for b in (bloque_imagen(f) for f in cap.lista_fotos) if b]
        if not imagenes:
            return
        datos = claude.json(
            sistema="Extraes datos de capturas de pantalla de publicaciones de venta. "
                    "Copia solo lo que se ve; usa null si un dato no aparece. No inventes.",
            contenido=[*imagenes, {"type": "text", "text": (
                f"Texto que envió el usuario: {cap.titulo or ''} {cap.descripcion or ''}\n"
                f"Moneda local por defecto: {self.cfg.moneda}. Extrae los datos de la publicación.")}],
            salida=DatosCaptura, max_tokens=4000, effort="low")
        if not datos:
            return
        if not cap.precio and datos.precio:
            precio = datos.precio
            if datos.moneda and datos.moneda.upper() != self.cfg.moneda:
                try:
                    precio = self.cfg.a_moneda_local(datos.precio, datos.moneda)
                except ValueError:
                    pass
            cap.precio = precio
        cap.titulo = cap.titulo or datos.titulo
        cap.ubicacion = cap.ubicacion or datos.ubicacion
        cap.vendedor = cap.vendedor or datos.vendedor
        if datos.descripcion:
            cap.descripcion = ((cap.descripcion + "\n") if cap.descripcion else "") + datos.descripcion

    def _evaluar(self, s: Session, cap: CapturaTelegram) -> None:
        if not cap.url:
            self.responder("Me falta el enlace de la publicación para poder guardarla.")
            return
        if not cap.precio:
            self._completar_con_ia(cap)
        if not cap.precio:
            self.responder("Me falta el precio: mándalo como número (por ejemplo <code>350</code>).")
            return
        url, id_externo, plataforma = normalizar_enlace(cap.url)
        existente = s.scalar(select(Oportunidad).where(
            Oportunidad.plataforma == plataforma, Oportunidad.id_externo == id_externo))
        if existente:
            cap.activa, cap.oportunidad_id = False, existente.id
            self.responder(f"Esa publicación ya estaba registrada (#{existente.id}, "
                           f"{existente.puntuacion}/100). La puedes ver en el panel.")
            return
        fotos = cap.lista_fotos
        descripcion = cap.descripcion or ""
        if len(fotos) > 1:
            descripcion += f"\n[{len(fotos)} capturas recibidas por Telegram]"
        op = buscador.guardar_publicacion(s, Publicacion(
            plataforma=plataforma, id_externo=id_externo, url=url,
            titulo=cap.titulo or "Publicación de Facebook", precio=cap.precio,
            moneda=self.cfg.moneda, descripcion=descripcion.strip() or None,
            ubicacion=cap.ubicacion, vendedor=cap.vendedor,
            foto_url=fotos[0] if fotos else None), "telegram")
        evaluador.evaluar(s, op, self.cfg.moneda)
        alerta = buscador.alertar(s, op, telegram=_SinTelegram())
        cap.activa, cap.oportunidad_id = False, op.id
        registrar(s, "bot_telegram", "captura_evaluada", f"#{op.id} {op.titulo[:80]} → {op.puntuacion}")
        nota = ("✅ Supera tu umbral: quedó como alerta en el panel." if alerta else
                f"ℹ️ No llega a tu umbral ({puntuacion_minima(s)}) o excede el presupuesto.")
        self.responder(formato_alerta(op, self.cfg.moneda).replace(
            "<i>Nada se compra automáticamente. Decide en el panel.</i>",
            f"{nota}\n<i>Nada se compra automáticamente. Oportunidad #{op.id} en el panel.</i>"))

    # ---------- salida
    def responder(self, texto: str) -> None:
        ok, err = self.tg.mensaje(texto)
        if not ok:
            log.warning("No pude responder en Telegram: %s", err)

    # ---------- bucle de recepción
    def bucle(self, parar: threading.Event, espera: int = 25) -> None:
        if not self.tg.activo:
            log.info("Bot de Telegram desactivado (faltan TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
            return
        log.info("Bot de Telegram escuchando mensajes")
        while not parar.is_set():
            try:
                self.una_vuelta(espera)
            except TelegramError as e:
                if e.codigo == 409:
                    log.error("Telegram 409: otro proceso está leyendo este bot o hay un webhook "
                              "configurado. Deja un solo proceso (servidor O bot) y borra el webhook.")
                    parar.wait(30)
                else:
                    log.warning("%s", e)
                    parar.wait(10)
            except httpx.HTTPError as e:
                log.warning("Sin conexión con Telegram (%s); reintento en 15 s", e)
                parar.wait(15)
            except Exception:  # noqa: BLE001 — el bot no debe morir por un error inesperado
                log.exception("Error en el bot de Telegram")
                parar.wait(5)

    def una_vuelta(self, espera: int = 25) -> int:
        with session_scope() as s:
            offset = leer_ajuste(s, "telegram_offset")
        updates = self.tg.actualizaciones(int(offset) if offset else None, espera)
        for u in updates:
            with session_scope() as s:
                try:
                    self.procesar(s, u)
                except Exception:  # noqa: BLE001 — un mensaje raro no bloquea a los siguientes
                    log.exception("Error procesando el mensaje %s", u.get("update_id"))
                    s.rollback()
                    self.responder("⚠️ Hubo un error procesando ese mensaje. Revisa el registro.")
                set_ajuste(s, "telegram_offset", u["update_id"] + 1)
        return len(updates)


def iniciar_en_hilo() -> tuple[threading.Thread, threading.Event] | None:
    if not get_settings().telegram_activo:
        return None
    parar = threading.Event()
    hilo = threading.Thread(target=BotTelegram().bucle, args=(parar,), name="bot-telegram", daemon=True)
    hilo.start()
    return hilo, parar
