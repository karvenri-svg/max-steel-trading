"""Puntuación de oportunidades de 0 a 100 (transparente y explicable)."""
from __future__ import annotations

from dataclasses import dataclass, field

PUNTOS_ESTADO = {
    "sellado": 10, "caja_abierta": 9, "excelente": 8, "bueno": 6,
    "regular": 3, "malo": 0, "desconocido": 4,
}


@dataclass
class Puntuacion:
    total: int
    desglose: dict[str, float] = field(default_factory=dict)
    avisos: list[str] = field(default_factory=list)

    def explicar(self) -> str:
        partes = ", ".join(f"{k} {v:.0f}" for k, v in self.desglose.items())
        avisos = (" | " + "; ".join(self.avisos)) if self.avisos else ""
        return f"{self.total}/100 ({partes}){avisos}"


def puntuar(*, precio: float, margen_pct: float | None, ganancia: float | None,
            presupuesto: float, riesgo_replica: float | None, estado_fisico: str | None,
            confianza_valor: float, bootleg_declarado: bool = False) -> Puntuacion:
    d: dict[str, float] = {}
    avisos: list[str] = []

    # Margen porcentual (hasta 40): 100 % de margen o más = 40 puntos.
    d["margen"] = 0 if margen_pct is None else max(0.0, min(40.0, margen_pct * 0.4))
    # Ganancia absoluta (hasta 15): escala con el presupuesto.
    ref = max(presupuesto * 0.5, 1)
    d["ganancia"] = 0 if ganancia is None else max(0.0, min(15.0, ganancia / ref * 15))
    # Ajuste al presupuesto (10).
    d["presupuesto"] = 10 if precio <= presupuesto else 0
    # Autenticidad (hasta 15).
    r = 0.5 if riesgo_replica is None else max(0.0, min(1.0, riesgo_replica))
    d["autenticidad"] = (1 - r) * 15
    # Estado físico (hasta 10).
    d["estado"] = PUNTOS_ESTADO.get(estado_fisico or "desconocido", 4)
    # Confianza en la valoración (hasta 10).
    d["confianza"] = confianza_valor * 10

    total = sum(d.values())

    if precio > presupuesto:
        avisos.append("excede el presupuesto")
        total = min(total, 40)
    if r >= 0.7 and not bootleg_declarado:
        avisos.append("probable réplica no declarada")
        total = min(total, 25)
    if bootleg_declarado:
        avisos.append("bootleg declarado (no venderlo como original)")
    if margen_pct is None:
        avisos.append("sin valor de mercado: agrega precios de referencia")
    elif margen_pct < 0:
        avisos.append("margen negativo")
        total = min(total, 20)

    return Puntuacion(int(round(max(0, min(100, total)))), {k: round(v, 1) for k, v in d.items()}, avisos)
