"""Cliente de Claude: el "cerebro" compartido por los agentes.

Todas las llamadas devuelven JSON validado contra un modelo Pydantic
(salidas estructuradas). Si no hay ANTHROPIC_API_KEY, `disponible` es False
y los agentes usan sus reglas locales.
"""
from __future__ import annotations

import base64
import json
import logging
import mimetypes
from pathlib import Path
from typing import TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from app.config import get_settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


def _esquema_estricto(schema: dict) -> dict:
    """Convierte el JSON Schema de Pydantic al formato de salidas estructuradas:
    todos los objetos con additionalProperties=false y todos los campos requeridos."""
    def fix(node):
        if isinstance(node, dict):
            node.pop("title", None)
            node.pop("default", None)
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            for v in node.values():
                fix(v)
        elif isinstance(node, list):
            for v in node:
                fix(v)
        return node
    return fix(json.loads(json.dumps(schema)))


def bloque_imagen(origen: str | None) -> dict | None:
    """Crea un bloque de imagen desde una URL pública o un archivo local."""
    if not origen:
        return None
    if origen.startswith(("http://", "https://")):
        return {"type": "image", "source": {"type": "url", "url": origen}}
    p = Path(origen)
    if not p.is_absolute():
        p = get_settings().upload_dir / p.name
    if not p.is_file():
        return None
    media = mimetypes.guess_type(p.name)[0] or "image/jpeg"
    if media not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
        return None
    data = base64.standard_b64encode(p.read_bytes()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}


class Claude:
    def __init__(self) -> None:
        self.cfg = get_settings()
        self._client: anthropic.Anthropic | None = None

    @property
    def disponible(self) -> bool:
        return self.cfg.ia_activa

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=self.cfg.anthropic_api_key)
        return self._client

    def json(self, *, sistema: str, contenido: list[dict] | str, salida: type[T],
             max_tokens: int = 16000, effort: str | None = None) -> T | None:
        """Una llamada a Claude con salida estructurada. Devuelve None si falla."""
        if not self.disponible:
            return None
        if isinstance(contenido, str):
            contenido = [{"type": "text", "text": contenido}]
        kwargs: dict = dict(
            model=self.cfg.claude_model,
            max_tokens=max_tokens,
            system=sistema,
            messages=[{"role": "user", "content": contenido}],
            output_config={
                "effort": effort or self.cfg.claude_effort,
                "format": {"type": "json_schema",
                           "schema": _esquema_estricto(salida.model_json_schema())},
            },
        )
        if self.cfg.claude_fallbacks:
            # Si el modelo rechaza la petición, la API la reintenta en otro modelo.
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        try:
            resp = self.client.beta.messages.create(**kwargs)
        except anthropic.AuthenticationError:
            log.error("ANTHROPIC_API_KEY inválida")
            return None
        except anthropic.RateLimitError:
            log.warning("Límite de peticiones de Anthropic alcanzado; se reintentará luego")
            return None
        except anthropic.APIStatusError as e:
            log.error("Error de la API de Anthropic %s: %s", e.status_code, e.message)
            return None
        except anthropic.APIConnectionError:
            log.error("Sin conexión con la API de Anthropic")
            return None

        if resp.stop_reason == "refusal":
            log.warning("Claude rechazó la petición: %s", resp.stop_details)
            return None
        if resp.stop_reason == "max_tokens":
            log.warning("Respuesta truncada por max_tokens")
            return None
        texto = next((b.text for b in resp.content if b.type == "text"), None)
        if not texto:
            return None
        try:
            return salida.model_validate_json(texto)
        except ValidationError as e:
            log.error("Respuesta de Claude no válida: %s", e)
            return None


_claude: Claude | None = None


def get_claude() -> Claude:
    global _claude
    if _claude is None:
        _claude = Claude()
    return _claude


def reset_claude() -> None:
    global _claude
    _claude = None
