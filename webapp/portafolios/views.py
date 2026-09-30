from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.cache import cache_control
from plotly.offline import get_plotlyjs

from . import consultas, graficas


def _sin_datos(request):
    return render(request, "portafolios/sin_datos.html", status=503)


def cliente(request):
    """Portafolio local (COP) e internacional (USD) de un cliente en su última fecha."""
    if not consultas.pipeline_listo():
        return _sin_datos(request)

    clientes = consultas.clientes()
    ids = [c["id_cliente"] for c in clientes]
    seleccionado = request.GET.get("cliente")
    if seleccionado not in ids:
        seleccionado = ids[0]
    resumen = next(c for c in clientes if c["id_cliente"] == seleccionado)

    local = consultas.portafolio_local(seleccionado)
    usd = consultas.portafolio_usd(seleccionado)
    evolucion_local = consultas.evolucion_local(seleccionado)
    evolucion_usd = consultas.evolucion_usd(seleccionado)
    modelo = consultas.modelo_listo()

    return render(request, "portafolios/cliente.html", {
        "clientes": clientes,
        "resumen": resumen,
        "riesgo": consultas.riesgo_cliente(seleccionado) if modelo else None,
        "recomendaciones": consultas.recomendaciones(seleccionado) if modelo else [],
        "local": local,
        "usd": usd,
        "composicion": [
            {"nombre": "Renta Variable", "pct": resumen["pct_renta_variable"], "color": graficas.COLORES["Renta Variable"]},
            {"nombre": "Renta Fija", "pct": resumen["pct_renta_fija"], "color": graficas.COLORES["Renta Fija"]},
            {"nombre": "FICs", "pct": resumen["pct_fics"], "color": graficas.COLORES["FICs"]},
        ],
        "graficas": {
            "local": graficas.barras_local(local) if local else None,
            "usd": graficas.barras_usd(usd) if usd else None,
            "evolucion_local": graficas.area_evolucion_local(evolucion_local) if evolucion_local else None,
            "evolucion_usd": graficas.barras_evolucion_usd(evolucion_usd) if evolucion_usd else None,
        },
    })


def cartera(request):
    """Vista agregada de todos los clientes."""
    if not consultas.pipeline_listo():
        return _sin_datos(request)

    clientes = consultas.clientes()
    por_banca = consultas.cartera_por("banca")
    por_perfil = consultas.cartera_por("perfil")
    return render(request, "portafolios/cartera.html", {
        "clientes": clientes,
        "total_cop": sum(c["total_cop"] or 0 for c in clientes),
        "total_usd": sum(c["total_usd"] or 0 for c in clientes),
        "n_usd": sum(1 for c in clientes if c["total_usd"]),
        "n_truncados": sum(1 for c in clientes if c["id_truncado"]),
        "por_banca": por_banca,
        "por_perfil": por_perfil,
        "por_macroactivo": consultas.cartera_por_macroactivo(),
        "graficas": {
            "banca": graficas.barras_cartera(por_banca, "grupo"),
            "perfil": graficas.barras_cartera(por_perfil, "grupo"),
        },
    })


def modelo(request):
    """Riesgo vs. perfil declarado, segmentos, recomendaciones y validación con mercado."""
    if not consultas.pipeline_listo() or not consultas.modelo_listo():
        return _sin_datos(request)

    riesgo = consultas.riesgo_clientes()
    meta = consultas.metadatos_modelo()
    recomendaciones = consultas.recomendaciones()
    conteo = {estado: sum(1 for r in riesgo if r["coherencia"] == estado) for estado in graficas.ESTADO}
    return render(request, "portafolios/modelo.html", {
        "riesgo": riesgo,
        "conteo": conteo,
        "segmentos": consultas.segmentos(),
        "recomendaciones": recomendaciones,
        "n_alta": sum(1 for r in recomendaciones if r["prioridad"] == 1),
        "validacion": consultas.validacion_mercado(),
        "meta": meta,
        "internacionales": [r for r in riesgo if r["pct_internacional"] >= 0.1],
        "graficas": {
            "perfil": graficas.dispersion_perfil(riesgo, meta["bandas_volatilidad"]),
            "cambiario": graficas.barras_cambiario(riesgo),
        },
    })


def calidad(request):
    """Bitácora de reglas de calidad de datos aplicadas por el pipeline."""
    if not consultas.pipeline_listo():
        return _sin_datos(request)
    return render(request, "portafolios/calidad.html", {
        "bitacora": consultas.bitacora_calidad(),
        "volumen": consultas.volumen_calidad(),
    })


@cache_control(max_age=86400, public=True)
def plotly_js(request):
    """plotly.js servido desde el paquete de Python: el dashboard funciona sin internet."""
    return HttpResponse(get_plotlyjs(), content_type="application/javascript")
