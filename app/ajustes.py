"""Ajustes editables desde el panel (sobrescriben los valores del .env)."""
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Ajuste, RegistroAgente


def _get(s: Session, clave: str) -> str | None:
    a = s.get(Ajuste, clave)
    return a.valor if a else None


def set_ajuste(s: Session, clave: str, valor) -> None:
    a = s.get(Ajuste, clave)
    if a:
        a.valor = str(valor)
    else:
        s.add(Ajuste(clave=clave, valor=str(valor)))


def presupuesto_maximo(s: Session) -> float:
    v = _get(s, "presupuesto_maximo")
    return float(v) if v else get_settings().presupuesto_maximo


def puntuacion_minima(s: Session) -> int:
    v = _get(s, "puntuacion_minima_alerta")
    return int(v) if v else get_settings().puntuacion_minima_alerta


def registrar(s: Session, agente: str, accion: str, detalle: str | None = None) -> None:
    s.add(RegistroAgente(agente=agente, accion=accion, detalle=(detalle or "")[:4000]))
