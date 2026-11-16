import json
from types import SimpleNamespace

from app.agents import evaluador
from app.ia import Claude, _esquema_estricto
from app.models import Oportunidad, PrecioReferencia


def _op(s, titulo, precio=300, desc=""):
    op = Oportunidad(plataforma="t", id_externo=titulo, url="https://x", titulo=titulo,
                     descripcion=desc, precio=precio)
    s.add(op)
    s.flush()
    return op


def test_reglas_identifican_ozaru_bootleg(s):
    ev = evaluador.evaluar_por_reglas(s, _op(s, "Figura Ozaru Dragon Ball GT bootleg"))
    assert ev.modelo == "Goku Ozaru (Dragon Ball GT)"
    assert ev.bootleg_declarado and ev.linea == "Dragon Ball"


def test_reglas_estado_sellado(s):
    ev = evaluador.evaluar_por_reglas(s, _op(s, "Max Steel 2000 nuevo en caja sellado"))
    assert ev.estado_fisico == "sellado" and ev.linea == "Max Steel"


def test_reglas_no_relevante(s):
    op = _op(s, "Licuadora Oster")
    evaluador.evaluar(s, op)
    assert op.puntuacion <= 10


def test_evaluar_calcula_margen_y_puntuacion(s):
    for p in (1000, 1000, 1000):
        s.add(PrecioReferencia(modelo="Max Steel (figura original 2000)", precio=p, fuente="t", vendido=True))
    op = _op(s, "Max Steel 2000 original excelente estado", precio=300)
    evaluador.evaluar(s, op)
    assert op.evaluada_con == "reglas"
    assert op.valor_reventa == 1000 and op.ganancia == 1000 * 0.9 - 100 - 300
    assert op.puntuacion >= 60


def test_evaluar_usa_claude_si_esta_disponible(s, monkeypatch):
    falso = SimpleNamespace(disponible=True, json=lambda **kw: evaluador.EvaluacionIA(
        es_figura_relevante=True, linea="Max Steel", modelo="Dread (villano)", anio_aproximado=2001,
        estado_fisico="bueno", accesorios_visibles=["arma"], riesgo_replica=0.1,
        bootleg_declarado=False, senales_autenticidad=["logo Mattel visible"],
        valor_mercado_estimado=800, resumen="Figura original."))
    monkeypatch.setattr(evaluador, "get_claude", lambda: falso)
    op = _op(s, "figura villano", precio=200)
    evaluador.evaluar(s, op)
    assert op.evaluada_con == "claude" and op.modelo_identificado == "Dread (villano)"
    assert op.valor_reventa == 800  # estimación IA como último recurso
    assert "valor: fuente=ia" in op.razonamiento.lower()


def test_esquema_estricto():
    sch = _esquema_estricto(evaluador.EvaluacionIA.model_json_schema())
    assert sch["additionalProperties"] is False
    assert set(sch["required"]) == set(sch["properties"])


class _Resp(SimpleNamespace):
    pass


def _cliente(resp, capturado):
    def create(**kw):
        capturado.update(kw)
        return resp
    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))


def _claude_con(resp, capturado, monkeypatch):
    c = Claude()
    monkeypatch.setattr(c.cfg, "anthropic_api_key", "sk-test")
    c._client = _cliente(resp, capturado)
    return c


def test_claude_json_ok(monkeypatch):
    datos = {"titulo": "T", "descripcion": "D"}
    resp = _Resp(stop_reason="end_turn", stop_details=None,
                 content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=json.dumps(datos))])
    cap = {}
    from app.agents.vendedor import TextoPublicacion
    r = _claude_con(resp, cap, monkeypatch).json(sistema="s", contenido="hola", salida=TextoPublicacion)
    assert r.titulo == "T"
    assert cap["model"] == "claude-opus-5-5"
    assert cap["output_config"]["format"]["type"] == "json_schema"
    assert cap["fallbacks"] == "default"
    assert "thinking" not in cap  # adaptativo por defecto


def test_claude_json_rechazo(monkeypatch):
    resp = _Resp(stop_reason="refusal", stop_details={"category": None}, content=[])
    from app.agents.vendedor import TextoPublicacion
    assert _claude_con(resp, {}, monkeypatch).json(sistema="s", contenido="x", salida=TextoPublicacion) is None


def test_claude_no_disponible_sin_clave():
    assert Claude().disponible is False
