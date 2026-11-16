import os
import tempfile

# Configuración aislada ANTES de importar la app.
_tmp = tempfile.mkdtemp()
os.environ.update({
    "DATABASE_URL": f"sqlite:///{_tmp}/test.db", "UPLOAD_DIR": f"{_tmp}/uploads",
    "ANTHROPIC_API_KEY": "", "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "",
    "EBAY_CLIENT_ID": "", "EBAY_CLIENT_SECRET": "", "MELI_ACCESS_TOKEN": "",
    "MONEDA": "MXN", "TIPOS_CAMBIO": "USD=18", "PRESUPUESTO_MAXIMO": "1500",
    "PUNTUACION_MINIMA_ALERTA": "60", "COMISION_VENTA": "0.10", "COSTO_ENVIO": "100",
    "PANEL_USUARIO": "", "PANEL_CLAVE": "",
})

import pytest  # noqa: E402

from app import db as dbmod  # noqa: E402
from app.ia import reset_claude  # noqa: E402


@pytest.fixture()
def s(tmp_path):
    dbmod.configure(f"sqlite:///{tmp_path}/t.db")
    dbmod.init_db()
    reset_claude()
    sess = dbmod.SessionLocal()
    yield sess
    sess.close()


class TelegramFalso:
    def __init__(self):
        self.enviadas = []

    activo = True

    def alerta(self, texto, foto_url, url):
        self.enviadas.append((texto, foto_url, url))
        return True, None


@pytest.fixture()
def telegram():
    return TelegramFalso()
