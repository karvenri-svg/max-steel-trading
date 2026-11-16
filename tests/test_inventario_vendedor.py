from datetime import date, timedelta

import pytest

from app.agents import analista, contacto, inventario, vendedor
from app.models import Canal, EstadoOportunidad, EstadoProducto, Oportunidad, Prospecto


def _prod(s, nombre="Max Steel 2000", linea="Max Steel", costo=300, **kw):
    return inventario.registrar_compra(s, nombre=nombre, linea=linea, costo=costo, **kw)


def test_codigos_y_precios(s):
    a, b, c = _prod(s), _prod(s), _prod(s, "Ozaru", "Dragon Ball")
    assert (a.codigo, b.codigo, c.codigo) == ("MS-0001", "MS-0002", "DB-0001")
    assert a.precio_minimo > a.costo_adquisicion and a.precio_recomendado >= a.precio_minimo


def test_venta_y_utilidad(s):
    p = _prod(s, costo=300, modelo="M")
    v = inventario.registrar_venta(s, p, 900, canal="mercadolibre")
    assert p.estado == EstadoProducto.vendido and v.comision == 90 and v.utilidad == 510
    with pytest.raises(inventario.ErrorInventario):
        inventario.registrar_venta(s, p, 900)
    with pytest.raises(inventario.ErrorInventario):
        inventario.cambiar_estado(s, p, EstadoProducto.disponible)
    m = analista.metricas(s)
    assert m["ventas_realizadas"] == 1 and m["ganancias_realizadas"] == 510
    assert m["margen_utilidad_pct"] == 170.0


def test_actualizar_rechaza_campos_no_editables(s):
    p = _prod(s)
    inventario.actualizar(s, p, nombre="Nuevo", estado_fisico="sellado")
    assert p.nombre == "Nuevo" and p.estado_fisico.value == "sellado"
    with pytest.raises(inventario.ErrorInventario):
        inventario.actualizar(s, p, estado="vendido")


def test_compra_desde_oportunidad(s):
    op = Oportunidad(plataforma="t", id_externo="1", url="u", titulo="t", precio=200,
                     modelo_identificado="Dread (villano)", linea="Max Steel")
    s.add(op)
    s.flush()
    p = inventario.registrar_compra_desde_oportunidad(s, op, 220)
    assert op.estado == EstadoOportunidad.comprada and p.costo_adquisicion == 220


def test_contacto_solo_borrador(s):
    op = Oportunidad(plataforma="t", id_externo="2", url="u", titulo="Ozaru", precio=200)
    s.add(op)
    s.flush()
    txt = contacto.preparar_mensaje_vendedor(s, op, 150)
    assert "$150" in txt and op.estado == EstadoOportunidad.interesa
    contacto.aprobar_contacto(s, op)
    assert op.estado == EstadoOportunidad.contacto_aprobado


def _canal(s, **kw):
    c = Canal(**{"nombre": "G", "tipo": "grupo_facebook", "tema": "max_steel", "autorizado": True, **kw})
    s.add(c)
    s.flush()
    return c


def test_publicacion_reglas_de_canal(s):
    ms, db = _prod(s), _prod(s, "Ozaru", "Dragon Ball")
    with pytest.raises(vendedor.ErrorVendedor, match="autorizado"):
        vendedor.generar_publicacion(s, [ms.id], _canal(s, autorizado=False).id)
    with pytest.raises(vendedor.ErrorVendedor, match="Max Steel"):
        vendedor.generar_publicacion(s, [ms.id], _canal(s, tema="dragon_ball").id)
    with pytest.raises(vendedor.ErrorVendedor, match="otras líneas"):
        vendedor.generar_publicacion(s, [db.id], _canal(s).id)
    with pytest.raises(vendedor.ErrorVendedor, match="mínimo"):
        vendedor.generar_publicacion(s, [ms.id], _canal(s).id, precio=1)
    pub = vendedor.generar_publicacion(s, [ms.id], _canal(s).id)
    assert pub.estado == "borrador" and "MS-0001" in pub.descripcion


def test_paquete_y_canales_sugeridos(s):
    a, b = _prod(s), _prod(s, "Dread")
    _canal(s)
    _canal(s, nombre="ML", tipo="mercadolibre", tema="general")
    pub = vendedor.generar_publicacion(s, [a.id, b.id])
    assert "Paquete" in pub.titulo
    assert {c.nombre for c in vendedor.canales_sugeridos(s, [a])} == {"G", "ML"}
    ozaru = _prod(s, "Ozaru", "Dragon Ball")
    assert {c.nombre for c in vendedor.canales_sugeridos(s, [ozaru])} == {"ML"}
    paquetes = vendedor.recomendar_paquetes(s)
    assert paquetes and paquetes[0]["linea"] == "Max Steel"


def test_respuesta_requiere_consentimiento_y_aprobacion(s):
    sin = Prospecto(nombre="Ana", consentimiento=False)
    con = Prospecto(nombre="Luis", consentimiento=True)
    s.add_all([sin, con])
    s.flush()
    with pytest.raises(vendedor.ErrorVendedor):
        vendedor.preparar_respuesta(s, sin, "¿precio?")
    i = vendedor.preparar_respuesta(s, con, "¿Sigue disponible?")
    assert i.aprobado is False and i.borrador_ia
    vendedor.aprobar_interaccion(s, i, "Sí, disponible")
    assert i.aprobado and con.proximo_seguimiento == date.today() + timedelta(days=3)
    assert vendedor.seguimientos_pendientes(s, date.today() + timedelta(days=3)) == [con]


def test_promociones_por_antiguedad(s):
    _prod(s, fecha_compra=date.today() - timedelta(days=100))
    _prod(s)
    recs = vendedor.recomendar_promociones(s)
    assert len(recs) == 1 and recs[0]["dias"] == 100
