"""Modelos de datos del sistema."""
from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    Boolean, Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def ahora() -> datetime:
    return datetime.now()


class EstadoProducto(str, enum.Enum):
    disponible = "disponible"
    reservado = "reservado"
    vendido = "vendido"


class EstadoFisico(str, enum.Enum):
    sellado = "sellado"          # nuevo en caja cerrada
    caja_abierta = "caja_abierta"
    excelente = "excelente"
    bueno = "bueno"
    regular = "regular"
    malo = "malo"
    desconocido = "desconocido"


class EstadoOportunidad(str, enum.Enum):
    nueva = "nueva"
    alertada = "alertada"
    interesa = "interesa"
    contacto_aprobado = "contacto_aprobado"  # tú autorizaste contactar al vendedor
    comprada = "comprada"                    # la compraste tú, manualmente
    descartada = "descartada"


class EstadoProspecto(str, enum.Enum):
    nuevo = "nuevo"
    interesado = "interesado"
    negociando = "negociando"
    compro = "compro"
    perdido = "perdido"


# ---------------------------------------------------------------- Inventario
class Producto(Base):
    __tablename__ = "productos"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(200))
    linea: Mapped[str] = mapped_column(String(50), default="Max Steel")
    modelo: Mapped[str | None] = mapped_column(String(200))
    anio: Mapped[int | None] = mapped_column(Integer)
    foto: Mapped[str | None] = mapped_column(String(300))
    estado_fisico: Mapped[EstadoFisico] = mapped_column(
        Enum(EstadoFisico), default=EstadoFisico.desconocido)
    accesorios: Mapped[str | None] = mapped_column(Text)
    costo_adquisicion: Mapped[float] = mapped_column(Float, default=0)
    precio_minimo: Mapped[float | None] = mapped_column(Float)
    precio_recomendado: Mapped[float | None] = mapped_column(Float)
    fecha_compra: Mapped[date | None] = mapped_column(Date)
    estado: Mapped[EstadoProducto] = mapped_column(
        Enum(EstadoProducto), default=EstadoProducto.disponible, index=True)
    notas: Mapped[str | None] = mapped_column(Text)
    oportunidad_id: Mapped[int | None] = mapped_column(ForeignKey("oportunidades.id"))
    creado: Mapped[datetime] = mapped_column(DateTime, default=ahora)
    actualizado: Mapped[datetime] = mapped_column(DateTime, default=ahora, onupdate=ahora)

    ventas: Mapped[list[Venta]] = relationship(back_populates="producto")


class Venta(Base):
    __tablename__ = "ventas"

    id: Mapped[int] = mapped_column(primary_key=True)
    producto_id: Mapped[int] = mapped_column(ForeignKey("productos.id"))
    prospecto_id: Mapped[int | None] = mapped_column(ForeignKey("prospectos.id"))
    precio_venta: Mapped[float] = mapped_column(Float)
    comision: Mapped[float] = mapped_column(Float, default=0)
    envio: Mapped[float] = mapped_column(Float, default=0)
    canal: Mapped[str | None] = mapped_column(String(100))
    fecha: Mapped[date] = mapped_column(Date, default=date.today)

    producto: Mapped[Producto] = relationship(back_populates="ventas")

    @property
    def utilidad(self) -> float:
        return self.precio_venta - self.comision - self.envio - (self.producto.costo_adquisicion or 0)


# ------------------------------------------------------------ Oportunidades
class Oportunidad(Base):
    """Una publicación encontrada en una plataforma (posible compra)."""
    __tablename__ = "oportunidades"
    __table_args__ = (UniqueConstraint("plataforma", "id_externo"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    plataforma: Mapped[str] = mapped_column(String(30), index=True)
    id_externo: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(String(600))
    titulo: Mapped[str] = mapped_column(String(300))
    descripcion: Mapped[str | None] = mapped_column(Text)
    precio: Mapped[float] = mapped_column(Float)
    moneda: Mapped[str] = mapped_column(String(5), default="MXN")
    envio: Mapped[float] = mapped_column(Float, default=0)
    ubicacion: Mapped[str | None] = mapped_column(String(200))
    foto_url: Mapped[str | None] = mapped_column(String(600))
    vendedor: Mapped[str | None] = mapped_column(String(200))
    termino_busqueda: Mapped[str | None] = mapped_column(String(100))
    encontrada: Mapped[datetime] = mapped_column(DateTime, default=ahora, index=True)

    # Resultado de la evaluación
    evaluada: Mapped[bool] = mapped_column(Boolean, default=False)
    evaluada_con: Mapped[str | None] = mapped_column(String(20))  # "claude" | "reglas"
    linea: Mapped[str | None] = mapped_column(String(50))
    modelo_identificado: Mapped[str | None] = mapped_column(String(200))
    estado_fisico: Mapped[str | None] = mapped_column(String(30))
    riesgo_replica: Mapped[float | None] = mapped_column(Float)  # 0 = original, 1 = réplica
    valor_reventa: Mapped[float | None] = mapped_column(Float)
    ganancia: Mapped[float | None] = mapped_column(Float)
    margen_pct: Mapped[float | None] = mapped_column(Float)
    puntuacion: Mapped[int | None] = mapped_column(Integer, index=True)
    razonamiento: Mapped[str | None] = mapped_column(Text)

    estado: Mapped[EstadoOportunidad] = mapped_column(
        Enum(EstadoOportunidad), default=EstadoOportunidad.nueva, index=True)
    mensaje_vendedor: Mapped[str | None] = mapped_column(Text)  # borrador, se envía a mano


class Alerta(Base):
    __tablename__ = "alertas"

    id: Mapped[int] = mapped_column(primary_key=True)
    oportunidad_id: Mapped[int] = mapped_column(ForeignKey("oportunidades.id"))
    canal: Mapped[str] = mapped_column(String(20))  # telegram | panel
    enviada: Mapped[datetime] = mapped_column(DateTime, default=ahora)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)
    leida: Mapped[bool] = mapped_column(Boolean, default=False)

    oportunidad: Mapped[Oportunidad] = relationship()


class Busqueda(Base):
    __tablename__ = "busquedas"

    id: Mapped[int] = mapped_column(primary_key=True)
    termino: Mapped[str] = mapped_column(String(100), unique=True)
    activa: Mapped[bool] = mapped_column(Boolean, default=True)


class CatalogoModelo(Base):
    """Modelos conocidos: ayudan a identificar figuras y a estimar su valor."""
    __tablename__ = "catalogo"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(200), unique=True)
    linea: Mapped[str] = mapped_column(String(50))
    anio: Mapped[int | None] = mapped_column(Integer)
    palabras_clave: Mapped[str] = mapped_column(Text)  # separadas por coma
    valor_referencia: Mapped[float | None] = mapped_column(Float)  # lo defines tú
    notas_autenticidad: Mapped[str | None] = mapped_column(Text)


class PrecioReferencia(Base):
    """Precio observado en el mercado (comparable) para un modelo."""
    __tablename__ = "precios_referencia"

    id: Mapped[int] = mapped_column(primary_key=True)
    modelo: Mapped[str] = mapped_column(String(200), index=True)
    precio: Mapped[float] = mapped_column(Float)
    fuente: Mapped[str] = mapped_column(String(50))
    vendido: Mapped[bool] = mapped_column(Boolean, default=False)
    fecha: Mapped[datetime] = mapped_column(DateTime, default=ahora)


# --------------------------------------------------------------- Vendedor
class Canal(Base):
    """Grupo o canal donde publicas. Solo se usan los marcados como autorizados."""
    __tablename__ = "canales"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(200))
    tipo: Mapped[str] = mapped_column(String(30), default="grupo_facebook")
    url: Mapped[str | None] = mapped_column(String(500))
    tema: Mapped[str] = mapped_column(String(30), default="max_steel")
    autorizado: Mapped[bool] = mapped_column(Boolean, default=False)
    reglas: Mapped[str | None] = mapped_column(Text)


class Publicacion(Base):
    __tablename__ = "publicaciones"

    id: Mapped[int] = mapped_column(primary_key=True)
    canal_id: Mapped[int | None] = mapped_column(ForeignKey("canales.id"))
    productos_ids: Mapped[str] = mapped_column(String(200))  # "1,4,7" (paquetes)
    titulo: Mapped[str] = mapped_column(String(300))
    descripcion: Mapped[str] = mapped_column(Text)
    precio: Mapped[float] = mapped_column(Float)
    estado: Mapped[str] = mapped_column(String(20), default="borrador")  # borrador|aprobada|publicada
    creada: Mapped[datetime] = mapped_column(DateTime, default=ahora)

    canal: Mapped[Canal | None] = relationship()


class Prospecto(Base):
    """Cliente potencial. Solo personas que te contactaron o aceptaron ser contactadas."""
    __tablename__ = "prospectos"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(200))
    contacto: Mapped[str | None] = mapped_column(String(200))
    origen: Mapped[str | None] = mapped_column(String(200))
    consentimiento: Mapped[bool] = mapped_column(Boolean, default=False)
    intereses: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[EstadoProspecto] = mapped_column(
        Enum(EstadoProspecto), default=EstadoProspecto.nuevo)
    proximo_seguimiento: Mapped[date | None] = mapped_column(Date)
    notas: Mapped[str | None] = mapped_column(Text)
    creado: Mapped[datetime] = mapped_column(DateTime, default=ahora)

    interacciones: Mapped[list[Interaccion]] = relationship(
        back_populates="prospecto", order_by="Interaccion.fecha")


class Interaccion(Base):
    __tablename__ = "interacciones"

    id: Mapped[int] = mapped_column(primary_key=True)
    prospecto_id: Mapped[int] = mapped_column(ForeignKey("prospectos.id"))
    fecha: Mapped[datetime] = mapped_column(DateTime, default=ahora)
    direccion: Mapped[str] = mapped_column(String(10))  # entrante | saliente
    texto: Mapped[str] = mapped_column(Text)
    borrador_ia: Mapped[bool] = mapped_column(Boolean, default=False)
    aprobado: Mapped[bool] = mapped_column(Boolean, default=False)

    prospecto: Mapped[Prospecto] = relationship(back_populates="interacciones")


# ------------------------------------------------------------- Sistema
class Ajuste(Base):
    __tablename__ = "ajustes"

    clave: Mapped[str] = mapped_column(String(50), primary_key=True)
    valor: Mapped[str] = mapped_column(String(500))


class RegistroAgente(Base):
    """Bitácora de todo lo que hacen los agentes (auditoría)."""
    __tablename__ = "registro_agentes"

    id: Mapped[int] = mapped_column(primary_key=True)
    agente: Mapped[str] = mapped_column(String(30), index=True)
    accion: Mapped[str] = mapped_column(String(100))
    detalle: Mapped[str | None] = mapped_column(Text)
    fecha: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), default=ahora)
