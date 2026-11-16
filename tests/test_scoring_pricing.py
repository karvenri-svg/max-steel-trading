from app.models import CatalogoModelo, PrecioReferencia
from app.pricing import calcular_margen, estimar_valor, precio_minimo_venta
from app.scoring import puntuar

BASE = dict(presupuesto=1500, riesgo_replica=0.1, estado_fisico="excelente", confianza_valor=0.8)


def test_buena_oferta_puntua_alto():
    p = puntuar(precio=300, margen_pct=150, ganancia=600, **BASE)
    assert p.total >= 80


def test_sobre_presupuesto_se_limita():
    p = puntuar(precio=2000, margen_pct=150, ganancia=1500, **BASE)
    assert p.total <= 40 and "excede el presupuesto" in p.avisos


def test_replica_no_declarada_se_limita():
    p = puntuar(precio=300, margen_pct=150, ganancia=600, presupuesto=1500, riesgo_replica=0.9,
                estado_fisico="excelente", confianza_valor=0.8)
    assert p.total <= 25


def test_bootleg_declarado_no_se_castiga_como_engano():
    p = puntuar(precio=300, margen_pct=150, ganancia=600, presupuesto=1500, riesgo_replica=0.1,
                estado_fisico="bueno", confianza_valor=0.8, bootleg_declarado=True)
    assert p.total > 60


def test_margen_negativo():
    assert puntuar(precio=900, margen_pct=-20, ganancia=-200, **BASE).total <= 20


def test_rango_0_100():
    p = puntuar(precio=1, margen_pct=99999, ganancia=99999, **BASE)
    assert 0 <= p.total <= 100


def test_calcular_margen():
    m = calcular_margen(400, 0, 1000, comision=0.10, envio_venta=100)
    assert m.ingreso_neto == 800 and m.ganancia == 400 and m.margen_pct == 100


def test_precio_minimo_cubre_costos():
    pm = precio_minimo_venta(400)  # comision 10 %, envío 100 (conftest)
    assert pm * 0.9 - 100 >= 400


def test_valor_prefiere_ventas_reales(s):
    for p in (900, 1000, 1100):
        s.add(PrecioReferencia(modelo="X", precio=p, fuente="t", vendido=True))
    s.add(PrecioReferencia(modelo="X", precio=5000, fuente="t", vendido=False))
    s.flush()
    v = estimar_valor(s, "X")
    assert v.valor == 1000 and v.fuente == "ventas"


def test_valor_desde_publicados_con_descuento(s):
    for p in (1000, 1000, 1000):
        s.add(PrecioReferencia(modelo="Y", precio=p, fuente="t"))
    s.flush()
    assert estimar_valor(s, "Y").valor == 900


def test_valor_desde_catalogo(s):
    s.add(CatalogoModelo(nombre="Z", linea="Max Steel", palabras_clave="z", valor_referencia=700))
    s.flush()
    v = estimar_valor(s, "Z")
    assert v.valor == 700 and v.fuente == "catalogo"


def test_sin_datos(s):
    assert estimar_valor(s, "Nada").valor is None
