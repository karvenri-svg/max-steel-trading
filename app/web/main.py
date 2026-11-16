"""Backend web + panel de control (FastAPI + plantillas Jinja2)."""
from __future__ import annotations

import logging
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import ajustes
from app.agents import analista, buscador, contacto, evaluador, inventario, vendedor
from app.config import get_settings
from app.db import get_db, init_db
from app.models import (
    Alerta, Busqueda, Canal, CatalogoModelo, EstadoOportunidad, EstadoProducto, EstadoProspecto,
    Interaccion, Oportunidad, PrecioReferencia, Producto, Prospecto, Publicacion, RegistroAgente,
    Venta,
)
from app.notifications.telegram import Telegram
from app.sources.base import Publicacion as PubFuente
from app.sources.registro import estado_fuentes

log = logging.getLogger(__name__)
WEB_DIR = Path(__file__).parent
cfg = get_settings()

# ------------------------------------------------------------------ Seguridad
seguridad = HTTPBasic(auto_error=False)


def autenticar(cred: HTTPBasicCredentials | None = Depends(seguridad)) -> None:
    if not (cfg.panel_usuario and cfg.panel_clave):
        return  # sin usuario configurado: solo para uso local
    ok = cred and secrets.compare_digest(cred.username, cfg.panel_usuario) and \
        secrets.compare_digest(cred.password, cfg.panel_clave)
    if not ok:
        raise HTTPException(401, "Credenciales requeridas", headers={"WWW-Authenticate": "Basic"})


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg.upload_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    sch = bot = None
    if app.state.programador:
        from app import scheduler
        from app.notifications.bot import iniciar_en_hilo
        sch = scheduler.iniciar()
        bot = iniciar_en_hilo()  # escucha tus mensajes de Telegram (si está configurado)
    yield
    if sch:
        sch.shutdown(wait=False)
    if bot:
        bot[1].set()


def crear_app(programador: bool = True) -> FastAPI:
    app = FastAPI(title="Max Steel Trading", lifespan=lifespan, dependencies=[Depends(autenticar)])
    app.state.programador = programador
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    cfg.upload_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=cfg.upload_dir), name="uploads")
    _rutas(app)
    return app


templates = Jinja2Templates(directory=WEB_DIR / "templates")


def foto_src(f: str | None) -> str | None:
    if not f:
        return None
    return f if f.startswith(("http://", "https://")) else f"/uploads/{Path(f).name}"


def dinero(v) -> str:
    return "—" if v is None else f"${v:,.0f}"


templates.env.globals.update(foto_src=foto_src, moneda=cfg.moneda)
templates.env.filters["dinero"] = dinero


def ir(url: str, msg: str | None = None, error: bool = False) -> RedirectResponse:
    if msg:
        from urllib.parse import quote
        url += ("&" if "?" in url else "?") + ("error=" if error else "msg=") + quote(msg)
    return RedirectResponse(url, status_code=303)


async def guardar_foto(archivo: UploadFile | None) -> str | None:
    if not archivo or not archivo.filename:
        return None
    ext = Path(archivo.filename).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        raise HTTPException(400, "Formato de imagen no permitido")
    datos = await archivo.read()
    if len(datos) > 8 * 1024 * 1024:
        raise HTTPException(400, "Imagen demasiado grande (máx. 8 MB)")
    nombre = f"{uuid.uuid4().hex}{ext}"
    (cfg.upload_dir / nombre).write_bytes(datos)
    return nombre


def _num(v: str | None) -> float | None:
    v = (v or "").strip().replace(",", "").replace("$", "")
    return float(v) if v else None


def _int(v: str | None) -> int | None:
    v = (v or "").strip()
    return int(v) if v else None


def _fecha(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


def _rutas(app: FastAPI) -> None:  # noqa: C901 — registro de rutas del panel
    def render(req: Request, plantilla: str, **ctx) -> HTMLResponse:
        return templates.TemplateResponse(req, plantilla, {
            "msg": req.query_params.get("msg"), "error": req.query_params.get("error"),
            "ia_activa": cfg.ia_activa, **ctx})

    # ============================================================ Dashboard
    @app.get("/", response_class=HTMLResponse)
    def dashboard(req: Request, db: Session = Depends(get_db)):
        top = db.scalars(select(Oportunidad).where(
            Oportunidad.estado.in_([EstadoOportunidad.nueva, EstadoOportunidad.alertada,
                                    EstadoOportunidad.interesa]),
            Oportunidad.puntuacion >= ajustes.puntuacion_minima(db))
            .order_by(Oportunidad.puntuacion.desc()).limit(6)).all()
        alertas = db.scalars(select(Alerta).where(Alerta.canal == "panel")
                             .order_by(Alerta.enviada.desc()).limit(8)).all()
        return render(req, "dashboard.html", m=analista.metricas(db), top=top, alertas=alertas,
                      recs=analista.recomendaciones(db),
                      seguimientos=vendedor.seguimientos_pendientes(db),
                      presupuesto=ajustes.presupuesto_maximo(db))

    @app.post("/analista/informe", response_class=HTMLResponse)
    def informe(req: Request, db: Session = Depends(get_db)):
        inf = analista.informe(db)
        db.commit()
        if inf is None:
            return ir("/", "El informe con IA requiere ANTHROPIC_API_KEY (o la llamada falló).", True)
        return render(req, "informe.html", inf=inf)

    # ============================================================ Oportunidades
    @app.get("/oportunidades", response_class=HTMLResponse)
    def oportunidades(req: Request, estado: str = "abiertas", minimo: int = 0,
                      db: Session = Depends(get_db)):
        q = select(Oportunidad)
        if estado == "abiertas":
            q = q.where(Oportunidad.estado.notin_([EstadoOportunidad.descartada, EstadoOportunidad.comprada]))
        elif estado != "todas":
            q = q.where(Oportunidad.estado == EstadoOportunidad(estado))
        if minimo:
            q = q.where(Oportunidad.puntuacion >= minimo)
        ops = db.scalars(q.order_by(Oportunidad.puntuacion.desc().nulls_last(),
                                    Oportunidad.encontrada.desc()).limit(200)).all()
        return render(req, "oportunidades.html", ops=ops, estado=estado, minimo=minimo,
                      estados=[e.value for e in EstadoOportunidad],
                      presupuesto=ajustes.presupuesto_maximo(db))

    @app.post("/oportunidades/buscar")
    def buscar_ahora(db: Session = Depends(get_db)):
        res = buscador.ciclo(db)
        msg = f"Búsqueda: {res.nuevas} nuevas, {res.alertas} alertas."
        if res.errores:
            msg += " Avisos: " + " | ".join(res.errores[:3])
        return ir("/oportunidades", msg, error=bool(res.errores) and not res.nuevas)

    @app.post("/oportunidades/manual")
    async def agregar_manual(url: str = Form(...), titulo: str = Form(...), precio: str = Form(...),
                             plataforma: str = Form("facebook"), descripcion: str = Form(""),
                             ubicacion: str = Form(""), vendedor_nombre: str = Form(""),
                             foto_url: str = Form(""), foto: UploadFile | None = File(None),
                             db: Session = Depends(get_db)):
        archivo = await guardar_foto(foto)
        pub = PubFuente(plataforma=plataforma, id_externo=url.strip(), url=url.strip(),
                        titulo=titulo, precio=_num(precio) or 0, moneda=cfg.moneda,
                        descripcion=descripcion or None, ubicacion=ubicacion or None,
                        vendedor=vendedor_nombre or None, foto_url=archivo or foto_url or None)
        op = buscador.guardar_publicacion(db, pub, "manual")
        if op is None:
            return ir("/oportunidades", "Esa publicación ya estaba registrada.", True)
        alertada = buscador.procesar_nueva(db, op)
        db.commit()
        return ir(f"/oportunidades/{op.id}",
                  f"Evaluada: {op.puntuacion}/100" + (" — alerta enviada" if alertada else ""))

    @app.get("/oportunidades/{op_id}", response_class=HTMLResponse)
    def oportunidad(req: Request, op_id: int, db: Session = Depends(get_db)):
        op = db.get(Oportunidad, op_id) or _404()
        return render(req, "oportunidad.html", op=op)

    @app.post("/oportunidades/{op_id}/accion")
    def accion_oportunidad(op_id: int, accion: str = Form(...), oferta: str = Form(""),
                           db: Session = Depends(get_db)):
        op = db.get(Oportunidad, op_id) or _404()
        if accion == "descartar":
            op.estado = EstadoOportunidad.descartada
        elif accion == "interesa":
            op.estado = EstadoOportunidad.interesa
        elif accion == "mensaje":
            contacto.preparar_mensaje_vendedor(db, op, _num(oferta))
        elif accion == "aprobar_contacto":
            contacto.aprobar_contacto(db, op)
        elif accion == "reevaluar":
            evaluador.evaluar(db, op, cfg.moneda)
        else:
            raise HTTPException(400, "Acción desconocida")
        ajustes.registrar(db, "usuario", f"oportunidad_{accion}", f"#{op.id}")
        db.commit()
        return ir(f"/oportunidades/{op.id}", "Hecho.")

    @app.post("/oportunidades/{op_id}/comprada")
    async def marcar_comprada(op_id: int, costo: str = Form(...), foto: UploadFile | None = File(None),
                              db: Session = Depends(get_db)):
        op = db.get(Oportunidad, op_id) or _404()
        p = inventario.registrar_compra_desde_oportunidad(db, op, _num(costo) or op.precio,
                                                          await guardar_foto(foto))
        db.commit()
        return ir(f"/inventario/{p.id}", f"Registrado en inventario como {p.codigo}.")

    # ============================================================ Inventario
    @app.get("/inventario", response_class=HTMLResponse)
    def lista_inventario(req: Request, estado: str = "", q: str = "", db: Session = Depends(get_db)):
        consulta = select(Producto)
        if estado:
            consulta = consulta.where(Producto.estado == EstadoProducto(estado))
        if q:
            consulta = consulta.where(Producto.nombre.ilike(f"%{q}%") | Producto.codigo.ilike(f"%{q}%"))
        productos = db.scalars(consulta.order_by(Producto.creado.desc())).all()
        return render(req, "inventario.html", productos=productos, estado=estado, q=q,
                      catalogo=db.scalars(select(CatalogoModelo).order_by(CatalogoModelo.nombre)).all())

    @app.post("/inventario")
    async def crear_producto(nombre: str = Form(...), linea: str = Form("Max Steel"),
                             modelo: str = Form(""), anio: str = Form(""),
                             estado_fisico: str = Form("desconocido"), accesorios: str = Form(""),
                             costo: str = Form(...), precio_minimo: str = Form(""),
                             precio_recomendado: str = Form(""), fecha_compra: str = Form(""),
                             notas: str = Form(""), foto: UploadFile | None = File(None),
                             db: Session = Depends(get_db)):
        try:
            p = inventario.registrar_compra(
                db, nombre=nombre, costo=_num(costo) or 0, linea=linea, modelo=modelo or None,
                anio=_int(anio), estado_fisico=estado_fisico, accesorios=accesorios or None,
                foto=await guardar_foto(foto), fecha_compra=_fecha(fecha_compra),
                precio_minimo=_num(precio_minimo), precio_recomendado=_num(precio_recomendado),
                notas=notas or None)
        except inventario.ErrorInventario as e:
            return ir("/inventario", str(e), True)
        db.commit()
        return ir(f"/inventario/{p.id}", f"Producto {p.codigo} creado.")

    @app.get("/inventario/{pid}", response_class=HTMLResponse)
    def ver_producto(req: Request, pid: int, db: Session = Depends(get_db)):
        p = db.get(Producto, pid) or _404()
        canales = db.scalars(select(Canal).where(Canal.autorizado)).all()
        prospectos = db.scalars(select(Prospecto).where(Prospecto.consentimiento)).all()
        return render(req, "producto.html", p=p, rec=vendedor.recomendar_precio(db, p),
                      canales=canales, sugeridos=vendedor.canales_sugeridos(db, [p]),
                      prospectos=prospectos,
                      catalogo=db.scalars(select(CatalogoModelo).order_by(CatalogoModelo.nombre)).all())

    @app.post("/inventario/{pid}/editar")
    async def editar_producto(pid: int, nombre: str = Form(...), linea: str = Form(...),
                              modelo: str = Form(""), anio: str = Form(""),
                              estado_fisico: str = Form(...), accesorios: str = Form(""),
                              costo: str = Form(...), precio_minimo: str = Form(""),
                              precio_recomendado: str = Form(""), fecha_compra: str = Form(""),
                              notas: str = Form(""), foto: UploadFile | None = File(None),
                              db: Session = Depends(get_db)):
        p = db.get(Producto, pid) or _404()
        cambios = dict(nombre=nombre, linea=linea, modelo=modelo or None, anio=_int(anio),
                       estado_fisico=estado_fisico, accesorios=accesorios or None,
                       costo_adquisicion=_num(costo) or 0, precio_minimo=_num(precio_minimo),
                       precio_recomendado=_num(precio_recomendado),
                       fecha_compra=_fecha(fecha_compra), notas=notas or None)
        nueva = await guardar_foto(foto)
        if nueva:
            cambios["foto"] = nueva
        inventario.actualizar(db, p, **cambios)
        db.commit()
        return ir(f"/inventario/{p.id}", "Producto actualizado.")

    @app.post("/inventario/{pid}/estado")
    def estado_producto(pid: int, estado: str = Form(...), db: Session = Depends(get_db)):
        p = db.get(Producto, pid) or _404()
        try:
            inventario.cambiar_estado(db, p, EstadoProducto(estado))
        except inventario.ErrorInventario as e:
            return ir(f"/inventario/{pid}", str(e), True)
        db.commit()
        return ir(f"/inventario/{pid}", f"Estado: {estado}.")

    @app.post("/inventario/{pid}/venta")
    def vender(pid: int, precio: str = Form(...), canal: str = Form(""), comision: str = Form(""),
               envio: str = Form("0"), prospecto_id: str = Form(""), confirmo: str = Form(""),
               db: Session = Depends(get_db)):
        p = db.get(Producto, pid) or _404()
        if confirmo != "si":
            return ir(f"/inventario/{pid}", "Marca la casilla para confirmar que tú cerraste la venta.", True)
        try:
            v = inventario.registrar_venta(db, p, _num(precio) or 0, canal=canal or None,
                                           comision=_num(comision), envio=_num(envio) or 0,
                                           prospecto_id=_int(prospecto_id))
        except inventario.ErrorInventario as e:
            return ir(f"/inventario/{pid}", str(e), True)
        if v.prospecto_id:
            pr = db.get(Prospecto, v.prospecto_id)
            if pr:
                pr.estado = EstadoProspecto.compro
        db.commit()
        return ir(f"/inventario/{pid}", f"Venta registrada. Utilidad: {dinero(v.utilidad)}")

    @app.get("/ventas", response_class=HTMLResponse)
    def ventas(req: Request, db: Session = Depends(get_db)):
        vs = db.scalars(select(Venta).order_by(Venta.fecha.desc())).all()
        return render(req, "ventas.html", ventas=vs, m=analista.metricas(db),
                      modelos=analista.rendimiento_por_modelo(db))

    # ============================================================ Vendedor
    @app.get("/vendedor", response_class=HTMLResponse)
    def panel_vendedor(req: Request, db: Session = Depends(get_db)):
        return render(req, "vendedor.html",
                      canales=db.scalars(select(Canal).order_by(Canal.nombre)).all(),
                      publicaciones=db.scalars(select(Publicacion).order_by(Publicacion.creada.desc()).limit(30)).all(),
                      disponibles=db.scalars(select(Producto).where(Producto.estado == EstadoProducto.disponible)).all(),
                      promociones=vendedor.recomendar_promociones(db),
                      paquetes=vendedor.recomendar_paquetes(db))

    @app.post("/vendedor/canales")
    def crear_canal(nombre: str = Form(...), tipo: str = Form("grupo_facebook"), url: str = Form(""),
                    tema: str = Form("max_steel"), reglas: str = Form(""), autorizado: str = Form(""),
                    db: Session = Depends(get_db)):
        db.add(Canal(nombre=nombre, tipo=tipo, url=url or None, tema=tema, reglas=reglas or None,
                     autorizado=autorizado == "si"))
        db.commit()
        return ir("/vendedor", "Canal guardado.")

    @app.post("/vendedor/canales/{cid}/autorizar")
    def autorizar_canal(cid: int, autorizado: str = Form(""), db: Session = Depends(get_db)):
        c = db.get(Canal, cid) or _404()
        c.autorizado = autorizado == "si"
        db.commit()
        return ir("/vendedor", "Canal actualizado.")

    @app.post("/vendedor/publicaciones")
    def crear_publicacion(productos: list[int] = Form(...), canal_id: str = Form(""),
                          precio: str = Form(""), db: Session = Depends(get_db)):
        try:
            pub = vendedor.generar_publicacion(db, productos, _int(canal_id), _num(precio))
        except vendedor.ErrorVendedor as e:
            return ir("/vendedor", str(e), True)
        db.commit()
        return ir(f"/vendedor#pub{pub.id}", "Borrador creado. Revísalo, apruébalo y publícalo tú.")

    @app.post("/vendedor/publicaciones/{pid}/estado")
    def estado_publicacion(pid: int, estado: str = Form(...), db: Session = Depends(get_db)):
        pub = db.get(Publicacion, pid) or _404()
        if estado not in ("borrador", "aprobada", "publicada", "descartada"):
            raise HTTPException(400)
        pub.estado = estado
        db.commit()
        return ir("/vendedor", f"Publicación {estado}.")

    @app.get("/prospectos", response_class=HTMLResponse)
    def prospectos(req: Request, db: Session = Depends(get_db)):
        return render(req, "prospectos.html",
                      prospectos=db.scalars(select(Prospecto).order_by(Prospecto.creado.desc())).all(),
                      seguimientos=vendedor.seguimientos_pendientes(db),
                      estados=[e.value for e in EstadoProspecto])

    @app.post("/prospectos")
    def crear_prospecto(nombre: str = Form(...), contacto_: str = Form("", alias="contacto"),
                        origen: str = Form(""), intereses: str = Form(""),
                        consentimiento: str = Form(""), db: Session = Depends(get_db)):
        if consentimiento != "si":
            return ir("/prospectos", "Solo se registran personas que te contactaron o aceptaron ser "
                                     "contactadas. Marca la casilla de consentimiento.", True)
        p = Prospecto(nombre=nombre, contacto=contacto_ or None, origen=origen or None,
                      intereses=intereses or None, consentimiento=True)
        db.add(p)
        db.commit()
        return ir(f"/prospectos/{p.id}", "Prospecto registrado.")

    @app.get("/prospectos/{pid}", response_class=HTMLResponse)
    def ver_prospecto(req: Request, pid: int, db: Session = Depends(get_db)):
        p = db.get(Prospecto, pid) or _404()
        return render(req, "prospecto.html", p=p, estados=[e.value for e in EstadoProspecto])

    @app.post("/prospectos/{pid}/pregunta")
    def pregunta(pid: int, texto: str = Form(...), db: Session = Depends(get_db)):
        p = db.get(Prospecto, pid) or _404()
        try:
            vendedor.preparar_respuesta(db, p, texto)
        except vendedor.ErrorVendedor as e:
            return ir(f"/prospectos/{pid}", str(e), True)
        db.commit()
        return ir(f"/prospectos/{pid}", "Borrador de respuesta listo. Revísalo y apruébalo.")

    @app.post("/prospectos/{pid}/interacciones/{iid}/aprobar")
    def aprobar(pid: int, iid: int, texto: str = Form(...), db: Session = Depends(get_db)):
        i = db.get(Interaccion, iid) or _404()
        vendedor.aprobar_interaccion(db, i, texto)
        db.commit()
        return ir(f"/prospectos/{pid}", "Aprobado. Cópialo y envíalo tú por el canal del cliente.")

    @app.post("/prospectos/{pid}/editar")
    def editar_prospecto(pid: int, estado: str = Form(...), proximo_seguimiento: str = Form(""),
                         notas: str = Form(""), db: Session = Depends(get_db)):
        p = db.get(Prospecto, pid) or _404()
        p.estado = EstadoProspecto(estado)
        p.proximo_seguimiento = _fecha(proximo_seguimiento)
        p.notas = notas or None
        db.commit()
        return ir(f"/prospectos/{pid}", "Prospecto actualizado.")

    # ============================================================ Configuración
    @app.get("/configuracion", response_class=HTMLResponse)
    def configuracion(req: Request, db: Session = Depends(get_db)):
        return render(req, "configuracion.html",
                      presupuesto=ajustes.presupuesto_maximo(db),
                      minima=ajustes.puntuacion_minima(db),
                      busquedas=db.scalars(select(Busqueda).order_by(Busqueda.termino)).all(),
                      catalogo=db.scalars(select(CatalogoModelo).order_by(CatalogoModelo.linea, CatalogoModelo.nombre)).all(),
                      precios=db.scalars(select(PrecioReferencia).order_by(PrecioReferencia.fecha.desc()).limit(30)).all(),
                      fuentes=estado_fuentes(), cfg=cfg)

    @app.post("/configuracion/negocio")
    def guardar_negocio(presupuesto: str = Form(...), minima: str = Form(...),
                        db: Session = Depends(get_db)):
        ajustes.set_ajuste(db, "presupuesto_maximo", _num(presupuesto))
        ajustes.set_ajuste(db, "puntuacion_minima_alerta", _int(minima))
        db.commit()
        return ir("/configuracion", "Ajustes guardados.")

    @app.post("/configuracion/busquedas")
    def agregar_busqueda(termino: str = Form(...), db: Session = Depends(get_db)):
        if not db.scalar(select(Busqueda).where(Busqueda.termino == termino.strip())):
            db.add(Busqueda(termino=termino.strip()))
            db.commit()
        return ir("/configuracion", "Búsqueda agregada.")

    @app.post("/configuracion/busquedas/{bid}/alternar")
    def alternar_busqueda(bid: int, db: Session = Depends(get_db)):
        b = db.get(Busqueda, bid) or _404()
        b.activa = not b.activa
        db.commit()
        return ir("/configuracion")

    @app.post("/configuracion/catalogo")
    def guardar_catalogo(nombre: str = Form(...), linea: str = Form("Max Steel"), anio: str = Form(""),
                         palabras_clave: str = Form(...), valor_referencia: str = Form(""),
                         notas_autenticidad: str = Form(""), db: Session = Depends(get_db)):
        c = db.scalar(select(CatalogoModelo).where(CatalogoModelo.nombre == nombre)) or CatalogoModelo(nombre=nombre)
        c.linea, c.anio, c.palabras_clave = linea, _int(anio), palabras_clave
        c.valor_referencia, c.notas_autenticidad = _num(valor_referencia), notas_autenticidad or None
        db.add(c)
        db.commit()
        return ir("/configuracion", "Catálogo actualizado.")

    @app.post("/configuracion/precios")
    def agregar_precio(modelo: str = Form(...), precio: str = Form(...), fuente: str = Form("manual"),
                       vendido: str = Form(""), db: Session = Depends(get_db)):
        db.add(PrecioReferencia(modelo=modelo, precio=_num(precio) or 0, fuente=fuente,
                                vendido=vendido == "si"))
        db.commit()
        return ir("/configuracion", "Precio de referencia agregado.")

    @app.post("/configuracion/telegram/prueba")
    def probar_telegram():
        ok, err = Telegram().mensaje("✅ Max Steel Trading: notificaciones funcionando.")
        return ir("/configuracion", "Mensaje de prueba enviado." if ok else f"Telegram: {err}", not ok)

    @app.get("/registro", response_class=HTMLResponse)
    def registro(req: Request, db: Session = Depends(get_db)):
        filas = db.scalars(select(RegistroAgente).order_by(RegistroAgente.id.desc()).limit(300)).all()
        return render(req, "registro.html", filas=filas)

    # ============================================================ API JSON
    @app.get("/api/metricas")
    def api_metricas(db: Session = Depends(get_db)):
        return analista.metricas(db)

    @app.get("/api/oportunidades")
    def api_oportunidades(minimo: int = 0, db: Session = Depends(get_db)):
        ops = db.scalars(select(Oportunidad).where(Oportunidad.puntuacion >= minimo)
                         .order_by(Oportunidad.puntuacion.desc()).limit(100)).all()
        return [{"id": o.id, "plataforma": o.plataforma, "titulo": o.titulo, "modelo": o.modelo_identificado,
                 "precio": o.precio, "valor_reventa": o.valor_reventa, "ganancia": o.ganancia,
                 "puntuacion": o.puntuacion, "ubicacion": o.ubicacion, "url": o.url,
                 "foto": o.foto_url, "estado": o.estado.value} for o in ops]

    @app.get("/api/productos")
    def api_productos(db: Session = Depends(get_db)):
        return [{"id": p.id, "codigo": p.codigo, "nombre": p.nombre, "modelo": p.modelo, "anio": p.anio,
                 "estado_fisico": p.estado_fisico.value, "accesorios": p.accesorios,
                 "costo": p.costo_adquisicion, "precio_minimo": p.precio_minimo,
                 "precio_recomendado": p.precio_recomendado,
                 "fecha_compra": p.fecha_compra.isoformat() if p.fecha_compra else None,
                 "estado": p.estado.value} for p in db.scalars(select(Producto))]


def _404():
    raise HTTPException(404, "No encontrado")


app = crear_app()
