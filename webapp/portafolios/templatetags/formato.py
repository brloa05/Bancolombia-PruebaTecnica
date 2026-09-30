"""Formato numérico colombiano: punto para miles y coma para decimales."""

from decimal import Decimal

from django import template

register = template.Library()


def _es(numero: float, decimales: int) -> str:
    texto = f"{numero:,.{decimales}f}"
    return texto.replace(",", "_").replace(".", ",").replace("_", ".")


@register.filter
def numero(valor, decimales=0):
    if valor is None or valor == "":
        return "—"
    return _es(float(valor), int(decimales))


@register.filter
def cop(valor):
    """$ 1.829.947.172"""
    return "—" if valor is None else "$ " + _es(float(valor), 0)


@register.filter
def usd(valor):
    """US$ 73.183,49"""
    return "—" if valor is None else "US$ " + _es(float(valor), 2)


@register.filter
def millones(valor):
    """$ 1.829,9 M"""
    return "—" if valor is None else "$ " + _es(float(valor) / 1e6, 1) + " M"


@register.filter
def pct(valor, decimales=1):
    """0.4394 → 43,9 %"""
    if valor is None:
        return "—"
    return _es(float(valor) * 100, int(decimales)) + " %"


@register.filter
def celda(valor):
    """Formato genérico para tablas de resultados SQL."""
    if valor is None:
        return "NULL"
    if isinstance(valor, bool):
        return "true" if valor else "false"
    if isinstance(valor, int):
        return str(valor)  # conteos, códigos y números de fila: sin separador de miles
    if isinstance(valor, (float, Decimal)):
        decimales = 0 if float(valor).is_integer() else 2
        return _es(float(valor), decimales)
    if isinstance(valor, (list, tuple)):
        return "{" + ", ".join(map(str, valor)) + "}"
    return str(valor)


@register.filter
def es_numero(valor):
    return isinstance(valor, (int, float, Decimal)) and not isinstance(valor, bool)
