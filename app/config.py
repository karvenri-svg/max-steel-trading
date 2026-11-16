"""Configuración central leída desde variables de entorno / archivo .env."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'maxsteel.db'}"
    upload_dir: Path = BASE_DIR / "data" / "uploads"

    anthropic_api_key: str = ""
    claude_model: str = "claude-opus-5-5"
    claude_effort: str = "medium"
    claude_fallbacks: bool = True

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    ebay_client_id: str = ""
    ebay_client_secret: str = ""
    ebay_marketplace: str = "EBAY_US"

    meli_site_id: str = "MLM"
    meli_client_id: str = ""
    meli_client_secret: str = ""
    meli_access_token: str = ""

    moneda: str = "MXN"
    # Conversión a tu moneda: "USD=18.5,EUR=20"
    tipos_cambio: str = "USD=18.0"
    presupuesto_maximo: float = 1500
    puntuacion_minima_alerta: int = 70
    comision_venta: float = 0.13
    costo_envio: float = 120
    intervalo_busqueda_min: int = 30

    panel_usuario: str = ""
    panel_clave: str = ""

    def a_moneda_local(self, monto: float, moneda: str) -> float:
        if not moneda or moneda == self.moneda:
            return monto
        tasas = dict(
            (k.strip().upper(), float(v)) for k, v in
            (par.split("=") for par in self.tipos_cambio.split(",") if "=" in par))
        if moneda.upper() not in tasas:
            raise ValueError(f"Sin tipo de cambio para {moneda}; agrégalo en TIPOS_CAMBIO")
        return round(monto * tasas[moneda.upper()], 2)

    @property
    def ia_activa(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def telegram_activo(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)


@lru_cache
def get_settings() -> Settings:
    return Settings()
