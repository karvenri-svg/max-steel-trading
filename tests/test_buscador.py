from app.agents import buscador
from app.models import Alerta, EstadoOportunidad, Oportunidad, PrecioReferencia
from app.notifications.telegram import formato_alerta
from app.sources.base import Publicacion


class FuenteFalsa:
    nombre = "falsa"

    def __init__(self, pubs):
        self.pubs = pubs

    def disponible(self):
        return True

    def buscar(self, termino, limite=50):
        return self.pubs


class FuenteRota(FuenteFalsa):
    def buscar(self, termino, limite=50):
        raise RuntimeError("caída")


def _pub(i, titulo, precio, moneda="MXN"):
    return Publicacion(plataforma="falsa", id_externo=str(i), url=f"https://x/{i}", titulo=titulo,
                       precio=precio, moneda=moneda, foto_url="https://img/1.jpg", ubicacion="CDMX")


def test_ciclo_guarda_evalua_alerta_y_no_duplica(s, telegram):
    for p in (1000, 1000, 1000):
        s.add(PrecioReferencia(modelo="Max Steel (figura original 2000)", precio=p, fuente="t", vendido=True))
    s.commit()
    pubs = [_pub(1, "Max Steel 2000 original excelente", 250),     # buena
            _pub(2, "Max Steel 2000 original excelente", 1400),    # margen negativo
            _pub(3, "Max Steel 2000 original", 3000)]              # sobre presupuesto
    r = buscador.ciclo(s, [FuenteFalsa(pubs)], telegram)
    # 8 búsquedas por defecto, pero solo 3 publicaciones únicas
    assert r.nuevas == 3 and r.alertas == 1 and len(telegram.enviadas) == 1
    buena = s.query(Oportunidad).filter_by(id_externo="1").one()
    assert buena.estado == EstadoOportunidad.alertada
    assert s.query(Alerta).filter_by(canal="telegram").count() == 1
    r2 = buscador.ciclo(s, [FuenteFalsa(pubs)], telegram)
    assert r2.nuevas == 0


def test_conversion_de_moneda(s):
    op = buscador.guardar_publicacion(s, _pub(9, "Max Steel", 20, "USD"))
    assert op.precio == 360 and op.moneda == "MXN" and "20.00 USD" in op.descripcion


def test_fuente_caida_no_detiene(s, telegram):
    r = buscador.ciclo(s, [FuenteRota([]), FuenteFalsa([_pub(5, "Ozaru GT", 100)])], telegram)
    assert r.nuevas == 1 and r.errores


def test_sin_fuentes(s):
    assert buscador.ciclo(s, []).errores


def test_formato_alerta_tiene_campos():
    op = Oportunidad(plataforma="ebay", id_externo="1", url="https://x/1", titulo="Max <Steel>",
                     precio=300, envio=0, valor_reventa=1000, ganancia=500, margen_pct=166,
                     ubicacion="CDMX", puntuacion=88, modelo_identificado="Max Steel 2000",
                     estado_fisico="bueno", riesgo_replica=0.1)
    t = formato_alerta(op, "MXN")
    for parte in ("88/100", "$300", "$1,000", "$500", "CDMX", "https://x/1", "&lt;Steel&gt;"):
        assert parte in t
