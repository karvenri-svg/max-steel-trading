"""Interfaz común para las fuentes de publicaciones."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass
class Publicacion:
    plataforma: str
    id_externo: str
    url: str
    titulo: str
    precio: float
    moneda: str
    descripcion: str | None = None
    envio: float = 0.0
    ubicacion: str | None = None
    foto_url: str | None = None
    vendedor: str | None = None
    condicion: str | None = None
    fecha: datetime | None = None


class Fuente(Protocol):
    nombre: str

    def disponible(self) -> bool: ...

    def buscar(self, termino: str, limite: int = 50) -> list[Publicacion]: ...


class ErrorFuente(Exception):
    """Error recuperable de una fuente (credenciales, permisos, red)."""
