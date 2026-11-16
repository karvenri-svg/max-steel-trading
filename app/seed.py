"""Datos iniciales: búsquedas y catálogo base de modelos (sin precios inventados)."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Busqueda, CatalogoModelo

BUSQUEDAS = [
    "Max Steel vintage", "Max Steel 2000", "Max Steel Mattel",
    "Dragon Ball Ozaru", "Dragon Ball bootleg", "Dragon Ball GT",
    "muñecos Max Steel", "juguetes Max Steel",
]

# Valor de referencia vacío a propósito: lo llenas tú en el panel (Catálogo)
# o se calcula a partir de precios observados y ventas reales.
CATALOGO = [
    ("Max Steel (figura original 2000)", "Max Steel", 2000,
     "max steel 2000,max steel original,josh mcgrath,mattel 2000",
     "Original Mattel: marca 'Mattel' y año grabados en espalda/pie; articulaciones firmes."),
    ("Max Steel Turbo / misión (2000-2004)", "Max Steel", 2002,
     "max steel turbo,turbo,mision,mission,adventure",
     "Revisar empaque Mattel y número de lote."),
    ("Dread (villano)", "Max Steel", 2001, "dread,villano dread", None),
    ("Psycho (villano)", "Max Steel", 2001, "psycho,psicho", None),
    ("Max Steel 2013 (reboot)", "Max Steel", 2013,
     "max steel 2013,turbo modos,steel,extroyer,toxzon,elementor", None),
    ("Goku Ozaru (Dragon Ball GT)", "Dragon Ball", 1997,
     "ozaru,oozaru,mono gigante,gran mono,golden ozaru",
     "Bootlegs comunes: pintura imprecisa, plástico brillante, sin copyright Bird Studio/Toei."),
    ("Goku Super Saiyajin 4 (GT)", "Dragon Ball", 1997,
     "ssj4,super saiyajin 4,super saiyan 4,goku gt", None),
    ("Baby Vegeta (GT)", "Dragon Ball", 1997, "baby vegeta,bebi,baby", None),
    ("Super 17 (GT)", "Dragon Ball", 1997, "super 17,android 17 gt", None),
    ("Omega Shenron (GT)", "Dragon Ball", 1997, "omega shenron,li shenron,syn shenron", None),
    ("Figura bootleg Dragon Ball", "Dragon Ball", None,
     "bootleg,pirata,generico,genérico,china",
     "Bootleg declarado: valor de colección propio; nunca venderlo como original."),
]


def seed_defaults(s: Session) -> None:
    if not s.scalar(select(Busqueda.id).limit(1)):
        s.add_all(Busqueda(termino=t) for t in BUSQUEDAS)
    if not s.scalar(select(CatalogoModelo.id).limit(1)):
        s.add_all(
            CatalogoModelo(nombre=n, linea=l, anio=a, palabras_clave=k, notas_autenticidad=na)
            for n, l, a, k, na in CATALOGO
        )
