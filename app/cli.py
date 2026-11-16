"""Línea de comandos.

    python -m app.cli init        # crea la base de datos
    python -m app.cli servidor    # panel web + tareas programadas
    python -m app.cli buscar      # un ciclo de búsqueda ahora
    python -m app.cli metricas    # imprime las métricas
    python -m app.cli demo        # carga datos de ejemplo (para probar el panel)
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import date, timedelta

from app.db import init_db, session_scope


def cmd_servidor(a):
    import uvicorn
    uvicorn.run("app.web.main:app", host=a.host, port=a.port, reload=False)


def cmd_buscar(_):
    from app.agents import buscador
    init_db()
    with session_scope() as s:
        r = buscador.ciclo(s)
    print(f"Nuevas: {r.nuevas}  Evaluadas: {r.evaluadas}  Alertas: {r.alertas}")
    for e in r.errores:
        print("  aviso:", e)


def cmd_metricas(_):
    from app.agents import analista
    init_db()
    with session_scope() as s:
        print(json.dumps(analista.metricas(s), indent=2, ensure_ascii=False))


def cmd_demo(_):
    """Datos FICTICIOS para explorar el panel. Borra data/maxsteel.db para empezar limpio."""
    from app.agents import buscador, inventario
    from app.models import Canal, PrecioReferencia
    from app.sources.base import Publicacion
    init_db()
    with session_scope() as s:
        for precio in (900, 1100, 1000, 950):
            s.add(PrecioReferencia(modelo="Max Steel (figura original 2000)", precio=precio,
                                   fuente="demo", vendido=True))
        s.add(PrecioReferencia(modelo="Goku Ozaru (Dragon Ball GT)", precio=1400, fuente="demo", vendido=True))
        s.add(Canal(nombre="[DEMO] Coleccionistas Max Steel", tipo="grupo_facebook", tema="max_steel",
                    autorizado=True, reglas="Ventas permitidas los fines de semana. Precio visible."))
        hace = date.today() - timedelta(days=75)
        a = inventario.registrar_compra(s, nombre="Max Steel original 2000", linea="Max Steel",
                                        modelo="Max Steel (figura original 2000)", anio=2000,
                                        estado_fisico="bueno", accesorios="casco, tabla", costo=380,
                                        fecha_compra=hace)
        inventario.registrar_compra(s, nombre="Dread villano", linea="Max Steel", modelo="Dread (villano)",
                                    anio=2001, estado_fisico="excelente", costo=250, fecha_compra=hace)
        b = inventario.registrar_compra(s, nombre="Goku Ozaru GT", linea="Dragon Ball",
                                        modelo="Goku Ozaru (Dragon Ball GT)", estado_fisico="regular",
                                        costo=500)
        inventario.registrar_venta(s, b, 1300, canal="directo")
        for i, (t, p) in enumerate([("Max Steel Mattel 2000 original con accesorios buen estado", 420),
                                    ("Lote juguetes Max Steel usado", 300),
                                    ("Ozaru dragon ball gt bootleg grande", 250)]):
            op = buscador.guardar_publicacion(s, Publicacion(
                plataforma="demo", id_externo=f"demo-{i}", url=f"https://example.com/demo-{i}",
                titulo=t, precio=p, moneda="MXN", ubicacion="CDMX"), "demo")
            if op:
                buscador.procesar_nueva(s, op)
        _ = a
    print("Datos de ejemplo cargados. Abre el panel con: python -m app.cli servidor")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="maxsteel")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(f=lambda _: (init_db(), print("Base de datos lista.")))
    sv = sub.add_parser("servidor")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.set_defaults(f=cmd_servidor)
    sub.add_parser("buscar").set_defaults(f=cmd_buscar)
    sub.add_parser("metricas").set_defaults(f=cmd_metricas)
    sub.add_parser("demo").set_defaults(f=cmd_demo)
    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
