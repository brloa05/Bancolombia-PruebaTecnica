"""Figuras Plotly del dashboard.

Criterios de visualización:
- El color sigue a la clase de activo, igual en todas las gráficas (paleta
  categórica validada para daltonismo; ver static/js/graficas.js para el modo oscuro).
- Barras horizontales para comparar posiciones (no tortas: los valores son cercanos).
- Un solo eje por gráfica; los montos en COP se expresan en millones.
- Cada gráfica tiene su tabla equivalente en la página.
"""

import json
from collections import defaultdict

import plotly.graph_objects as go
from plotly.utils import PlotlyJSONEncoder

COLORES = {
    "Renta Variable": "#2a78d6",
    "Renta Fija": "#eb6834",
    "FICs": "#1baf7a",
    "Fondos": "#1baf7a",
    "Estructurados": "#eda100",
    "Liquidez": "#e87ba4",
    "Otros": "#898781",
}
ORDEN_LOCAL = ["Renta Variable", "Renta Fija", "FICs"]
ORDEN_USD = ["Renta Variable", "Renta Fija", "Fondos", "Estructurados", "Liquidez"]

TINTA = "#52514e"
REJILLA = "#e1e0d9"


def _layout(fig: go.Figure, alto: int, **extra) -> str:
    fig.update_layout(
        height=alto,
        margin=dict(l=8, r=52, t=8, b=8),  # espacio para las etiquetas fuera de la barra
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="system-ui, -apple-system, 'Segoe UI', sans-serif", size=12, color=TINTA),
        separators=",.",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, title_text="",
                    traceorder="normal"),
        hoverlabel=dict(font_size=12),
        **extra,
    )
    fig.update_xaxes(gridcolor=REJILLA, zerolinecolor=REJILLA, linecolor=REJILLA)
    fig.update_yaxes(gridcolor=REJILLA, zerolinecolor=REJILLA, linecolor=REJILLA)
    # Se incrusta en <script type="application/json">: se escapa "</" por seguridad
    return json.dumps(fig, cls=PlotlyJSONEncoder).replace("</", "<\\/")


def _alto_barras(n: int, por_barra: int = 44) -> int:
    """Alto proporcional al número de barras (incluye la banda del eje x)."""
    return max(150, por_barra * n + 110)


def _corto(texto: str, n: int = 38) -> str:
    texto = texto or ""
    return texto if len(texto) <= n else texto[: n - 1].rstrip() + "…"


def barras_local(portafolio: list[dict]) -> str:
    """Posiciones del portafolio local, coloreadas por macroactivo."""
    fig = go.Figure()
    filas = sorted(portafolio, key=lambda f: f["saldo_cop"])
    for macro in ORDEN_LOCAL:
        grupo = [f for f in filas if f["macroactivo"] == macro]
        if not grupo:
            continue
        fig.add_bar(
            y=[f["activo"] for f in grupo],
            x=[float(f["saldo_cop"]) / 1e6 for f in grupo],
            orientation="h",
            name=macro,
            marker=dict(color=COLORES[macro], cornerradius=4),
            text=[f"{float(f['participacion']):.1%}".replace(".", ",") for f in grupo],
            textposition="outside",
            cliponaxis=False,
            customdata=[float(f["saldo_cop"]) for f in grupo],
            hovertemplate="<b>%{y}</b><br>$ %{customdata:,.0f} COP<br>%{text} del portafolio<extra>" + macro + "</extra>",
        )
    fig.update_yaxes(categoryorder="array", categoryarray=[f["activo"] for f in filas], showgrid=False)
    fig.update_xaxes(title_text="Millones de COP", ticksuffix=" M")
    return _layout(fig, alto=_alto_barras(len(filas)), bargap=0.35)


def barras_usd(portafolio: list[dict], maximo: int = 12) -> str:
    """Principales instrumentos del portafolio internacional; el resto se agrupa en 'Otros'."""
    principales = portafolio[:maximo]
    resto = portafolio[maximo:]
    filas = sorted(
        ({**f, "etiqueta": _corto(f["simbolo"] + " · " + f["nombre"] if f["simbolo"] else f["nombre"])}
         for f in principales),
        key=lambda f: f["valor_mercado_usd"],
    )
    if resto:
        # 'Otros' va de primero en la lista para quedar en la base del eje, debajo de todo
        filas.insert(0, {
            "etiqueta": f"Otros ({len(resto)} instrumentos)",
            "clase_activo": "Otros",
            "valor_mercado_usd": sum(f["valor_mercado_usd"] for f in resto),
            "participacion": sum(f["participacion"] or 0 for f in resto),
        })

    fig = go.Figure()
    for clase in [*ORDEN_USD, "Otros"]:
        grupo = [f for f in filas if f["clase_activo"] == clase]
        if not grupo:
            continue
        fig.add_bar(
            y=[f["etiqueta"] for f in grupo],
            x=[float(f["valor_mercado_usd"]) for f in grupo],
            orientation="h",
            name=clase,
            marker=dict(color=COLORES[clase], cornerradius=4),
            text=[f"{float(f['participacion'] or 0):.1%}".replace(".", ",") for f in grupo],
            textposition="outside",
            cliponaxis=False,
            hovertemplate="<b>%{y}</b><br>US$ %{x:,.2f}<br>%{text} del portafolio<extra>" + clase + "</extra>",
        )
    fig.update_yaxes(categoryorder="array", categoryarray=[f["etiqueta"] for f in filas], showgrid=False)
    fig.update_xaxes(title_text="US$", tickprefix="$")
    return _layout(fig, alto=_alto_barras(len(filas), por_barra=36), bargap=0.35)


def area_evolucion_local(evolucion: list[dict]) -> str:
    """Saldo diario del portafolio local, apilado por macroactivo."""
    series = defaultdict(dict)
    for f in evolucion:
        series[f["macroactivo"]][f["fecha"]] = float(f["saldo_cop"]) / 1e6
    fechas = sorted({f["fecha"] for f in evolucion})

    fig = go.Figure()
    for macro in ORDEN_LOCAL:
        if macro not in series:
            continue
        fig.add_scatter(
            x=fechas,
            y=[series[macro].get(d, 0) for d in fechas],
            name=macro,
            mode="lines",
            stackgroup="uno",
            line=dict(color=COLORES[macro], width=2),
            hovertemplate="$ %{y:,.1f} M<extra>" + macro + "</extra>",
        )
    fig.update_yaxes(title_text="Millones de COP", ticksuffix=" M", rangemode="tozero")
    fig.update_xaxes(showgrid=False, tickformat="%b %Y")
    return _layout(fig, alto=320, hovermode="x unified")


def barras_evolucion_usd(evolucion: list[dict]) -> str:
    """Valor del portafolio internacional por corte, apilado por clase de activo."""
    cortes = sorted({f["fecha_corte"] for f in evolucion})
    promedio = {f["fecha_corte"] for f in evolucion if f["es_promedio"]}
    etiquetas = [c.strftime("%Y-%m-%d") + (" (promedio)" if c in promedio else "") for c in cortes]
    valores = defaultdict(dict)
    for f in evolucion:
        valores[f["clase_activo"]][f["fecha_corte"]] = float(f["valor_mercado_usd"])

    fig = go.Figure()
    for clase in ORDEN_USD:
        if clase not in valores:
            continue
        fig.add_bar(
            x=etiquetas,
            y=[valores[clase].get(c, 0) for c in cortes],
            name=clase,
            marker=dict(color=COLORES[clase], line=dict(width=2, color="rgba(252,252,251,1)")),
            hovertemplate="US$ %{y:,.2f}<extra>" + clase + "</extra>",
        )
    fig.update_yaxes(title_text="US$", tickprefix="$", rangemode="tozero")
    fig.update_xaxes(showgrid=False, type="category")
    return _layout(fig, alto=320, barmode="stack", bargap=0.45, hovermode="x unified")


def barras_cartera(filas: list[dict], campo_grupo: str) -> str:
    """Total de la cartera local por grupo (una serie: un solo color)."""
    filas = sorted((f for f in filas if f["total_cop"]), key=lambda f: f["total_cop"])
    etiquetas = [
        f"{f[campo_grupo].title()} · {f['clientes']} cliente{'s' if f['clientes'] != 1 else ''}" for f in filas
    ]
    fig = go.Figure(go.Bar(
        y=etiquetas,
        x=[float(f["total_cop"]) / 1e6 for f in filas],
        orientation="h",
        marker=dict(color=COLORES["Renta Variable"], cornerradius=4),
        hovertemplate="<b>%{y}</b><br>$ %{x:,.1f} M COP<extra></extra>",
    ))
    fig.update_yaxes(showgrid=False)
    fig.update_xaxes(title_text="Millones de COP", ticksuffix=" M")
    return _layout(fig, alto=max(200, 46 * len(filas) + 80), bargap=0.35, showlegend=False)
