from types import SimpleNamespace

import httpx
import pytest

from app.agents import evaluador
from app.config import get_settings
from app.models import Alerta, CapturaTelegram, Oportunidad, PrecioReferencia
from app.notifications import bot as botmod
from app.notifications.bot import BotTelegram, extraer_precio, normalizar_enlace, numero
from app.notifications.telegram import Telegram, TelegramError

CHAT = "111"
FB = "https://www.facebook.com/marketplace/item/987654321/?ref=share&mibextid=abc"


class TGFalso:
    activo = True

    def __init__(self, updates=None):
        self.mensajes, self.updates, self.descargas = [], list(updates or []), 0

    def mensaje(self, texto):
        self.mensajes.append(texto)
        return True, None

    def descargar_archivo(self, file_id):
        self.descargas += 1
        return b"\xff\xd8\xff fake jpg", f"photos/{file_id}.jpg"

    def actualizaciones(self, offset, espera=25):
        self.offset_pedido = offset
        u, self.updates = self.updates, []
        return u


@pytest.fixture()
def cfg(monkeypatch, tmp_path):
    c = get_settings()
    monkeypatch.setattr(c, "telegram_chat_id", CHAT)
    monkeypatch.setattr(c, "upload_dir", tmp_path / "up")
    return c


@pytest.fixture()
def bot(s, cfg):
    return BotTelegram(TGFalso())


def msg(texto=None, chat=CHAT, foto=None, update_id=1):
    m = {"chat": {"id": int(chat)}}
    if texto is not None:
        m["caption" if foto else "text"] = texto
    if foto:
        m["photo"] = [{"file_id": foto + "-small"}, {"file_id": foto}]
    return {"update_id": update_id, "message": m}


def _con_valor(s, modelo="Goku Ozaru (Dragon Ball GT)", precio=1200):
    for _ in range(3):
        s.add(PrecioReferencia(modelo=modelo, precio=precio, fuente="t", vendido=True))
    s.commit()


# ------------------------------------------------------------ utilidades
@pytest.mark.parametrize("txt,esperado", [
    ("350", 350), ("1,250", 1250), ("1.250", 1250), ("350.50", 350.5),
    ("1.250,50", 1250.5), ("1,250.50", 1250.5), ("0", None), ("abc", None),
])
def test_numero(txt, esperado):
    assert numero(txt) == esperado


def test_extraer_precio_ignora_numeros_del_enlace():
    assert extraer_precio(f"{FB} Ozaru GT $1,200") == 1200
    assert extraer_precio("Ozaru 450 pesos") == 450
    assert extraer_precio(FB) is None


def test_normalizar_enlaces():
    assert normalizar_enlace(FB) == (
        "https://www.facebook.com/marketplace/item/987654321/", "fbmp-987654321", "facebook")
    url, ide, plat = normalizar_enlace("https://m.facebook.com/groups/maxsteelmx/posts/555/?x=1")
    assert ide == "fbgr-maxsteelmx-555" and plat == "facebook_grupo" and "?" not in url
    assert normalizar_enlace("facebook.com/share/p/AbC/")[0] == "https://facebook.com/share/p/AbC/"


# ------------------------------------------------------------ seguridad
def test_ignora_chats_no_autorizados(s, bot):
    bot.procesar(s, msg(f"/fb {FB} 300 Ozaru", chat="999"))
    assert bot.tg.mensajes == [] and s.query(CapturaTelegram).count() == 0


# ------------------------------------------------------------ flujos
def test_atajo_fb_evalua_al_instante(s, bot):
    _con_valor(s)
    bot.procesar(s, msg(f"/fb {FB} 300 Ozaru Dragon Ball GT"))
    op = s.query(Oportunidad).one()
    assert op.plataforma == "facebook" and op.id_externo == "fbmp-987654321"
    assert op.precio == 300 and op.titulo == "Ozaru Dragon Ball GT"
    assert op.modelo_identificado == "Goku Ozaru (Dragon Ball GT)"
    assert "/100" in bot.tg.mensajes[-1] and "Supera tu umbral" in bot.tg.mensajes[-1]
    assert s.query(Alerta).filter_by(canal="panel").count() == 1
    assert s.query(Alerta).filter_by(canal="telegram").count() == 0  # no se duplica en Telegram
    assert s.query(CapturaTelegram).one().activa is False


def test_flujo_paso_a_paso_con_foto(s, bot):
    bot.procesar(s, msg(FB))
    assert "precio" in bot.tg.mensajes[-1]
    bot.procesar(s, msg("Max Steel 2000 original", foto="F1"))
    bot.procesar(s, msg("450"))
    assert "Todo listo" in bot.tg.mensajes[-1]
    assert s.query(Oportunidad).count() == 0  # aún no se evalúa
    bot.procesar(s, msg("/evaluar"))
    op = s.query(Oportunidad).one()
    assert op.precio == 450 and op.titulo == "Max Steel 2000 original"
    assert op.foto_url.startswith("tg-") and (get_settings().upload_dir / op.foto_url).is_file()
    assert bot.tg.descargas == 1


def test_foto_primero_y_enlace_despues(s, bot):
    bot.procesar(s, msg(foto="F1"))
    bot.procesar(s, msg(f"{FB} $280"))
    caps = s.query(CapturaTelegram).filter_by(activa=True).all()
    assert len(caps) == 1 and caps[0].lista_fotos and caps[0].precio == 280


def test_evaluar_sin_precio_pide_precio(s, bot):
    bot.procesar(s, msg(FB))
    bot.procesar(s, msg("/evaluar"))
    assert "Me falta el precio" in bot.tg.mensajes[-1]
    assert s.query(Oportunidad).count() == 0


def test_precio_desde_captura_con_ia(s, bot, monkeypatch):
    falso = SimpleNamespace(disponible=True, json=lambda **kw: botmod.DatosCaptura(
        titulo="Ozaru gigante GT", precio=20, moneda="USD", ubicacion="Monterrey", vendedor="Juan",
        descripcion="Completo"))
    monkeypatch.setattr(botmod, "get_claude", lambda: falso)
    monkeypatch.setattr(evaluador, "get_claude", lambda: SimpleNamespace(disponible=False))
    bot.procesar(s, msg(FB))
    bot.procesar(s, msg(foto="F1"))
    assert "lo leo de tus capturas" in bot.tg.mensajes[-1]
    bot.procesar(s, msg("/evaluar"))
    op = s.query(Oportunidad).one()
    assert op.precio == 360  # 20 USD × 18 (conftest)
    assert op.titulo == "Ozaru gigante GT" and op.ubicacion == "Monterrey"


def test_duplicado(s, bot):
    bot.procesar(s, msg(f"/fb {FB} 300 Ozaru"))
    bot.procesar(s, msg(f"/fb {FB.split('?')[0]} 250 Ozaru"))
    assert s.query(Oportunidad).count() == 1
    assert "ya estaba registrada" in bot.tg.mensajes[-1]


def test_nuevo_enlace_descarta_captura_anterior(s, bot):
    bot.procesar(s, msg(FB))
    bot.procesar(s, msg("https://www.facebook.com/marketplace/item/222/"))
    assert "Descarté la captura anterior" in bot.tg.mensajes[-1]
    assert s.query(CapturaTelegram).filter_by(activa=True).count() == 1


def test_cancelar_estado_resumen_y_desconocido(s, bot):
    bot.procesar(s, msg(FB))
    bot.procesar(s, msg("/estado"))
    assert "en preparación" in bot.tg.mensajes[-1]
    bot.procesar(s, msg("/cancelar"))
    assert s.query(CapturaTelegram).filter_by(activa=True).count() == 0
    bot.procesar(s, msg("/resumen"))
    assert "Resumen" in bot.tg.mensajes[-1]
    bot.procesar(s, msg("/xyz"))
    assert "/ayuda" in bot.tg.mensajes[-1]
    bot.procesar(s, msg("hola"))
    assert "enlace" in bot.tg.mensajes[-1]


def test_fb_sin_enlace(s, bot):
    bot.procesar(s, msg("/fb 300 Ozaru"))
    assert "Incluye el enlace" in bot.tg.mensajes[-1]


# ------------------------------------------------------------ bucle
def test_una_vuelta_guarda_offset_y_sobrevive_errores(s, cfg, monkeypatch):
    tg = TGFalso([msg("/ayuda", update_id=10), msg("/resumen", update_id=11)])
    b = BotTelegram(tg)
    original = b.procesar

    def falla_en_el_primero(sess, u):
        if u["update_id"] == 10:
            raise RuntimeError("boom")
        original(sess, u)
    monkeypatch.setattr(b, "procesar", falla_en_el_primero)
    assert b.una_vuelta() == 2
    assert any("error" in m for m in tg.mensajes) and any("Resumen" in m for m in tg.mensajes)
    tg.updates = []
    b.una_vuelta()
    assert tg.offset_pedido == 12


def test_cliente_get_updates_y_descarga(monkeypatch, cfg):
    monkeypatch.setattr(cfg, "telegram_bot_token", "TOK")

    def handler(req: httpx.Request):
        if req.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [{"update_id": 5}]})
        if req.url.path.endswith("/getFile"):
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "photos/a.jpg"}})
        if req.url.path == "/file/botTOK/photos/a.jpg":
            return httpx.Response(200, content=b"IMG")
        return httpx.Response(409, json={"ok": False, "description": "Conflict"})
    tg = Telegram(httpx.Client(transport=httpx.MockTransport(handler)))
    assert tg.actualizaciones(None, 0) == [{"update_id": 5}]
    assert tg.descargar_archivo("x") == (b"IMG", "photos/a.jpg")

    tg409 = Telegram(httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(409, json={"ok": False, "description": "Conflict"}))))
    with pytest.raises(TelegramError) as e:
        tg409.actualizaciones(None, 0)
    assert e.value.codigo == 409
