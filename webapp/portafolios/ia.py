"""Asistente comercial con IA (Claude).

Redacta, para el gerente comercial, un resumen del cliente y una propuesta de
siguiente paso a partir de lo que ya calculó el pipeline: composición del
portafolio, riesgo, perfil declarado vs. implícito, segmento y recomendaciones.

Decisiones:
- Privacidad: a la API no se envía el ID del cliente ni ningún dato personal;
  solo cifras agregadas del portafolio y nombres de instrumentos de mercado.
- El modelo solo redacta con los datos entregados: no calcula ni inventa
  rentabilidades. Los números vienen del pipeline.
- Salida estructurada (JSON Schema) para mostrarla por secciones.
- Cada propuesta se guarda (PropuestaIA) para no repetir costo ni latencia.

Requiere ANTHROPIC_API_KEY en el archivo .env.
"""

import json
import os

import anthropic
from django.db import connection

from .models import PropuestaIA

MODELO = "claude-opus-5-5"

SISTEMA = """Eres un asistente para gerentes comerciales de inversión de la Vicepresidencia de \
Estructuración de Valores Bancolombia. A partir de los datos del portafolio de un cliente, redactas \
un resumen claro y una propuesta de siguiente paso que el gerente pueda usar para preparar su \
conversación con el cliente.

Reglas:
- Usa únicamente los datos entregados. No inventes cifras, rentabilidades futuras ni productos \
que no se deduzcan de los datos; si algo no se sabe, dilo.
- Respeta la adecuación: si el perfil de riesgo es "SIN DEFINIR", el primer paso es perfilar al \
cliente y no puedes recomendar productos específicos; describe solo qué conversación tener.
- Si el riesgo del portafolio está por encima del perfil declarado, prioriza la revisión de \
adecuación antes de cualquier oferta.
- Escribe en español de Colombia, en tono profesional y directo, para un gerente (no para el cliente).
- Expresa los montos en pesos con separador de miles con punto (p. ej. $ 1.230 M) y los \
porcentajes con coma decimal.
- Sé concreto: frases cortas, sin relleno."""

ESQUEMA = {
    "type": "object",
    "properties": {
        "resumen": {
            "type": "string",
            "description": "Dos o tres frases: quién es el cliente en términos de inversión y qué destaca de su portafolio.",
        },
        "puntos_clave": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Tres a cinco hallazgos concretos del portafolio, con cifras.",
        },
        "propuesta": {
            "type": "string",
            "description": "La siguiente acción comercial recomendada y por qué, coherente con el perfil y las reglas.",
        },
        "guion_llamada": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Tres a cinco preguntas o mensajes para la conversación con el cliente.",
        },
        "alertas": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Advertencias de adecuación, concentración, riesgo cambiario o calidad de datos; lista vacía si no hay.",
        },
    },
    "required": ["resumen", "puntos_clave", "propuesta", "guion_llamada", "alertas"],
    "additionalProperties": False,
}


class ErrorIA(Exception):
    """Error presentable al usuario."""


def disponible() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


def _filas(sql: str, params) -> list[dict]:
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columnas = [c.name for c in cursor.description]
        return [dict(zip(columnas, fila)) for fila in cursor.fetchall()]


def contexto_cliente(id_cliente: str) -> dict:
    """Datos agregados del cliente que se envían al modelo (sin identificadores)."""
    riesgo = _filas("SELECT * FROM analitica.riesgo_cliente WHERE id_cliente = %s", [id_cliente])
    if not riesgo:
        raise ErrorIA("El cliente no tiene resultados del modelo. Ejecuta el pipeline.")
    r = riesgo[0]
    posiciones = _filas("""
        SELECT nombre, origen, categoria_riesgo, round(peso * 100, 1) AS pct_portafolio
        FROM mart.posiciones_actuales
        WHERE id_cliente = %s
        ORDER BY peso DESC
        LIMIT 10
    """, [id_cliente])
    recomendaciones = _filas("""
        SELECT tipo, titulo, detalle FROM analitica.recomendaciones
        WHERE id_cliente = %s ORDER BY prioridad
    """, [id_cliente])
    usd = _filas("""
        SELECT nombre, fecha_vencimiento, round(valor_mercado_usd) AS valor_usd
        FROM mart.portafolio_usd_actual
        WHERE id_cliente = %s AND fecha_vencimiento IS NOT NULL
        ORDER BY fecha_vencimiento LIMIT 5
    """, [id_cliente])

    def pct(valor) -> float:
        return round(float(valor) * 100, 1)

    return {
        "banca": r["banca"],
        "perfil_riesgo_declarado": r["perfil_riesgo"],
        "perfil_riesgo_implicito_por_volatilidad": r["perfil_implicito"],
        "coherencia_perfil": r["coherencia"],
        "segmento": r["segmento"],
        "portafolio_total_cop_millones": round(float(r["aum_cop"]) / 1e6, 1),
        "pct_internacional": pct(r["pct_internacional"]),
        "composicion_pct": {
            "renta_variable": pct(r["pct_renta_variable"]),
            "renta_fija": pct(r["pct_renta_fija"]),
            "multiactivo": pct(r["pct_multiactivo"]),
            "estructurados": pct(r["pct_estructurados"]),
            "liquidez_y_fics_vista": pct(r["pct_liquidez"]),
        },
        "numero_posiciones": int(r["n_posiciones"]),
        "concentracion_hhi": round(float(r["hhi"]), 2),
        "volatilidad_anual_activos_pct": pct(r["volatilidad_anual"]),
        "volatilidad_anual_en_pesos_pct": pct(r["volatilidad_cop"]),
        "pct_del_riesgo_por_tasa_de_cambio": pct(r["pct_riesgo_cambiario"]),
        "var_95_1_dia_cop_millones": round(float(r["var_95_1d_cop"]) / 1e6, 1),
        "rentabilidad_12m_composicion_actual_pct": pct(r["rendimiento_12m"]),
        "principales_posiciones": [
            {**p, "pct_portafolio": float(p["pct_portafolio"])} for p in posiciones
        ],
        "vencimientos_proximos_usd": [
            {**v, "fecha_vencimiento": v["fecha_vencimiento"].isoformat(), "valor_usd": float(v["valor_usd"])}
            for v in usd
        ],
        "recomendaciones_del_modelo": recomendaciones,
        "fecha_de_los_datos": "portafolio local al 2024-05-15, internacional al 2024-05-30",
    }


def generar(id_cliente: str, cliente: anthropic.Anthropic | None = None) -> PropuestaIA:
    """Genera y guarda una propuesta para el cliente."""
    if cliente is None and not disponible():
        raise ErrorIA("Falta ANTHROPIC_API_KEY en el archivo .env.")
    contexto = contexto_cliente(id_cliente)
    cliente = cliente or anthropic.Anthropic(timeout=120.0)

    try:
        respuesta = cliente.beta.messages.create(
            model=MODELO,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # si el modelo declina por política, el servidor reintenta con otro
            system=SISTEMA,
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": ESQUEMA},
            },
            messages=[{
                "role": "user",
                "content": "Datos del cliente (JSON):\n" + json.dumps(contexto, ensure_ascii=False, indent=2),
            }],
        )
    except anthropic.AuthenticationError as exc:
        raise ErrorIA("La API key de Anthropic no es válida.") from exc
    except anthropic.PermissionDeniedError as exc:
        raise ErrorIA("La API key no tiene permiso para usar este modelo.") from exc
    except anthropic.RateLimitError as exc:
        raise ErrorIA("Límite de uso de la API alcanzado. Intenta de nuevo en un momento.") from exc
    except anthropic.APIStatusError as exc:
        raise ErrorIA(f"Error de la API de Anthropic ({exc.status_code}).") from exc
    except anthropic.APIConnectionError as exc:
        raise ErrorIA("No hay conexión con la API de Anthropic.") from exc

    if respuesta.stop_reason == "refusal":
        raise ErrorIA("El modelo no generó la propuesta para este caso.")
    if respuesta.stop_reason == "max_tokens":
        raise ErrorIA("La respuesta quedó incompleta. Intenta de nuevo.")

    texto = next((b.text for b in respuesta.content if b.type == "text"), None)
    if texto is None:
        raise ErrorIA("La respuesta no trajo contenido.")
    contenido = json.loads(texto)  # output_config.format garantiza JSON válido según el esquema

    return PropuestaIA.objects.create(
        id_cliente=id_cliente,
        modelo=respuesta.model,
        contenido=contenido,
        contexto=contexto,
        tokens_entrada=respuesta.usage.input_tokens,
        tokens_salida=respuesta.usage.output_tokens,
    )


def ultima(id_cliente: str) -> PropuestaIA | None:
    return PropuestaIA.objects.filter(id_cliente=id_cliente).order_by("-creada").first()
