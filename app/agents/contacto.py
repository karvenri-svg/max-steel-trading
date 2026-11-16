"""Contacto con vendedores: SOLO borradores que tú apruebas y envías a mano."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.ajustes import registrar
from app.models import EstadoOportunidad, Oportunidad


def preparar_mensaje_vendedor(s: Session, op: Oportunidad, oferta: float | None = None) -> str:
    texto = (f"¡Hola! Vi tu publicación \"{op.titulo}\". ¿Sigue disponible? "
             + (f"¿Aceptarías ${oferta:,.0f}? " if oferta else "")
             + "¿Me podrías enviar más fotos (espalda, pies y marcas de fábrica) y confirmar si "
               "está completa? Gracias.")
    op.mensaje_vendedor = texto
    op.estado = EstadoOportunidad.interesa
    registrar(s, "buscador", "borrador_mensaje_vendedor", f"#{op.id}")
    return texto


def aprobar_contacto(s: Session, op: Oportunidad) -> Oportunidad:
    """Marca que TÚ autorizaste contactar. El sistema no envía el mensaje: lo copias tú."""
    if not op.mensaje_vendedor:
        preparar_mensaje_vendedor(s, op)
    op.estado = EstadoOportunidad.contacto_aprobado
    registrar(s, "buscador", "contacto_aprobado", f"#{op.id} {op.url}")
    return op
