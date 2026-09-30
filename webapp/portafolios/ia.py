"""Asistente comercial con IA (modelo abierto local vía Ollama).

Redacta, para el gerente comercial, un resumen del cliente y una propuesta de
siguiente paso a partir de lo que ya calculó el pipeline: composición del
portafolio, riesgo, perfil declarado vs. implícito, segmento y recomendaciones.

Decisiones:
- Modelo abierto que corre en la máquina (Ollama, por defecto Qwen 2.5 7B):
  gratis, sin API key y sin enviar datos a terceros; funciona sin internet.
- Aun así, al modelo no se le pasa el ID del cliente ni datos personales:
  solo cifras agregadas del portafolio y nombres de instrumentos de mercado.
- El modelo solo redacta con los datos entregados: no calcula ni inventa
  rentabilidades. Los números vienen del pipeline.
- Salida estructurada (JSON Schema en `format`) para mostrarla por secciones.
- Cada propuesta se guarda (PropuestaIA) para no repetir la espera.

Configuración (.env): OLLAMA_HOST y OLLAMA_MODEL (opcionales).
"""

import json
import os
import re

import httpx
import ollama
from django.db import connection

from .models import PropuestaIA

HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
MODELO = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
TIEMPO_MAXIMO_S = 300  # en CPU, un modelo de 7B puede tardar más de un minuto

SISTEMA = """Eres un asistente para gerentes comerciales de inversión de la Vicepresidencia de \
Estructuración de Valores Bancolombia. A partir de los datos del portafolio de un cliente, redactas \
un resumen claro y una propuesta de siguiente paso que el gerente pueda usar para preparar su \
conversación con el cliente.

Reglas:
- Usa únicamente los datos entregados. No inventes cifras, rentabilidades futuras ni productos \
que no se deduzcan de los datos; si algo no se sabe, dilo.
- Respeta la adecuación: si el perfil de riesgo declarado es "SIN DEFINIR", el primer paso es \
perfilar al cliente y no puedes recomendar productos específicos; describe solo qué conversación tener.
- Si el riesgo del portafolio está por encima del perfil declarado, prioriza la revisión de \
adecuación antes de cualquier oferta.
- Escribe en español de Colombia, en tono profesional y directo, para un gerente (no para el cliente).
- Los "Hechos clave" del mensaje son exactos: no los contradigas ni los cambies.
- Para comparar el riesgo con el perfil usa la volatilidad de los activos (sin efecto de la tasa de \
cambio); la volatilidad en pesos solo sirve para hablar del riesgo cambiario.
- Llama a cada instrumento por su tipo (acción, bono, nota estructurada, fondo); no llames bono a \
una nota estructurada.
- Expresa los montos en pesos con separador de miles con punto (p. ej. $ 1.230 M) y los \
porcentajes con coma decimal (p. ej. 60,9 %), nunca con punto.
- Sé concreto: frases cortas, sin relleno.
- Responde solo con el JSON pedido."""

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


def _cliente() -> ollama.Client:
    return ollama.Client(host=HOST, timeout=TIEMPO_MAXIMO_S)


def estado() -> tuple[bool, str]:
    """(disponible, mensaje): si Ollama responde y tiene el modelo descargado."""
    try:
        modelos = {m.model for m in ollama.Client(host=HOST, timeout=2).list().models}
    except (httpx.HTTPError, ConnectionError, ollama.ResponseError):
        return False, "Ollama no está corriendo. Ábrelo o ejecuta «ollama serve»."
    if MODELO not in modelos:
        return False, f"Falta descargar el modelo: ejecuta «ollama pull {MODELO}»."
    return True, ""


def _filas(sql: str, params) -> list[dict]:
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columnas = [c.name for c in cursor.description]
        return [dict(zip(columnas, fila)) for fila in cursor.fetchall()]


def contexto_cliente(id_cliente: str) -> dict:
    """Datos agregados del cliente que se pasan al modelo (sin identificadores)."""
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
        SELECT nombre, tipo_instrumento, fecha_vencimiento, round(valor_mercado_usd) AS valor_usd
        FROM mart.portafolio_usd_actual
        WHERE id_cliente = %s AND fecha_vencimiento IS NOT NULL
        ORDER BY fecha_vencimiento LIMIT 5
    """, [id_cliente])

    def pct(valor) -> float:
        return round(float(valor) * 100, 1)

    def millones_cop(valor) -> str:
        return "$ " + f"{float(valor) / 1e6:,.1f}".replace(",", "_").replace(".", ",").replace("_", ".") + " millones de COP"

    def dolares(valor) -> str:
        return "US$ " + f"{float(valor):,.0f}".replace(",", ".")

    return {
        "banca": r["banca"],
        "perfil_riesgo_declarado": r["perfil_riesgo"],
        "perfil_riesgo_implicito_por_volatilidad": r["perfil_implicito"],
        "coherencia_perfil": r["coherencia"],
        "segmento": r["segmento"],
        "portafolio_total": millones_cop(r["aum_cop"]),
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
        "var_95_1_dia": millones_cop(r["var_95_1d_cop"]),
        "rentabilidad_12m_composicion_actual_pct": pct(r["rendimiento_12m"]),
        "principales_posiciones": [
            {**p, "pct_portafolio": float(p["pct_portafolio"])} for p in posiciones
        ],
        "vencimientos_proximos_usd": [
            {**v, "fecha_vencimiento": v["fecha_vencimiento"].isoformat(), "valor_usd": dolares(v["valor_usd"])}
            for v in usd
        ],
        "recomendaciones_del_modelo": recomendaciones,
        "fecha_de_los_datos": "portafolio local al 2024-05-15, internacional al 2024-05-30",
    }


def _validar(contenido) -> dict:
    """Los modelos pequeños pueden desviarse del esquema: se valida antes de guardar."""
    if not isinstance(contenido, dict):
        raise ErrorIA("La respuesta del modelo no tiene el formato esperado.")
    faltantes = [c for c in ESQUEMA["required"] if c not in contenido]
    if faltantes:
        raise ErrorIA(f"La respuesta del modelo no trae: {', '.join(faltantes)}. Intenta de nuevo.")
    for campo in ("puntos_clave", "guion_llamada", "alertas"):
        if not isinstance(contenido[campo], list):
            contenido[campo] = [str(contenido[campo])] if contenido[campo] else []
    return {c: contenido[c] for c in ESQUEMA["required"]}


RE_PERFIL = re.compile(
    r"perfil (?:de riesgo )?(declarado|impl[ií]cito)[^.\n]{0,25}?\b(sin definir|conservador|moderado|agresivo)\b"
)

RE_COHERENTE = re.compile(r"\b(est[aá] en coherencia|es coherente|son coherentes|est[aá] alineado)\b")


def _mensaje(contexto: dict) -> str:
    """Hechos clave en líneas explícitas (los modelos pequeños los respetan mejor) + datos completos."""
    hechos = [
        f"- Perfil de riesgo declarado: {contexto['perfil_riesgo_declarado']}",
        f"- Perfil implícito según la volatilidad de los activos: {contexto['perfil_riesgo_implicito_por_volatilidad']}",
        f"- Coherencia: {contexto['coherencia_perfil']}",
        f"- Volatilidad anual de los activos: {contexto['volatilidad_anual_activos_pct']} %",
        f"- Portafolio total: {contexto['portafolio_total']}",
    ]
    return ("Hechos clave (exactos):\n" + "\n".join(hechos)
            + "\n\nDatos completos del cliente (JSON):\n" + json.dumps(contexto, ensure_ascii=False, indent=2))


def inconsistencias(contenido: dict, contexto: dict) -> list[str]:
    """Detecta si el texto atribuye al cliente un perfil distinto al registrado."""
    texto = json.dumps(contenido, ensure_ascii=False).lower()
    esperado = {
        "declarado": contexto["perfil_riesgo_declarado"].lower(),
        "implicito": contexto["perfil_riesgo_implicito_por_volatilidad"].lower(),
    }
    hallazgos = set()
    for tipo, perfil in RE_PERFIL.findall(texto):
        clave = "declarado" if tipo == "declarado" else "implicito"
        if perfil != esperado[clave]:
            hallazgos.add(f"El texto menciona un perfil {clave} «{perfil}», pero el registrado es "
                          f"«{esperado[clave]}».")
    if contexto["coherencia_perfil"] != "Coherente" and RE_COHERENTE.search(texto):
        hallazgos.add(f"El texto dice que el portafolio es coherente con el perfil, pero el modelo lo "
                      f"clasifica como «{contexto['coherencia_perfil'].lower()}».")
    return sorted(hallazgos)


def _llamar(cliente: ollama.Client, contexto: dict):
    try:
        respuesta = cliente.chat(
            model=MODELO,
            messages=[
                {"role": "system", "content": SISTEMA},
                {"role": "user", "content": _mensaje(contexto)},
            ],
            format=ESQUEMA,                   # salida restringida al esquema JSON
            options={"temperature": 0},       # lo más apegado posible a los datos
            keep_alive="10m",                 # mantiene el modelo en memoria entre propuestas
        )
    except ollama.ResponseError as exc:
        if exc.status_code == 404:
            raise ErrorIA(f"Falta descargar el modelo: ejecuta «ollama pull {MODELO}».") from exc
        raise ErrorIA(f"Error de Ollama: {exc.error}") from exc
    except httpx.TimeoutException as exc:
        raise ErrorIA("El modelo tardó demasiado en responder. Intenta de nuevo.") from exc
    except (httpx.HTTPError, ConnectionError) as exc:
        raise ErrorIA("Ollama no está corriendo. Ábrelo o ejecuta «ollama serve».") from exc

    if respuesta.done_reason == "length":
        raise ErrorIA("La respuesta quedó incompleta. Intenta de nuevo.")
    try:
        return respuesta, _validar(json.loads(respuesta.message.content))
    except json.JSONDecodeError as exc:
        raise ErrorIA("La respuesta del modelo no es JSON válido. Intenta de nuevo.") from exc


def generar(id_cliente: str, cliente: ollama.Client | None = None) -> PropuestaIA:
    """Genera y guarda una propuesta para el cliente.

    Si el texto contradice el perfil registrado, se reintenta una vez; si persiste,
    se guarda con la advertencia visible para que el gerente no lo pase por alto.
    """
    contexto = contexto_cliente(id_cliente)
    cliente = cliente or _cliente()

    respuesta, contenido = _llamar(cliente, contexto)
    if inconsistencias(contenido, contexto):
        respuesta, contenido = _llamar(cliente, contexto)
    contenido["verificacion"] = inconsistencias(contenido, contexto)

    return PropuestaIA.objects.create(
        id_cliente=id_cliente,
        modelo=respuesta.model or MODELO,
        contenido=contenido,
        contexto=contexto,
        tokens_entrada=respuesta.prompt_eval_count or 0,
        tokens_salida=respuesta.eval_count or 0,
    )


def ultima(id_cliente: str) -> PropuestaIA | None:
    return PropuestaIA.objects.filter(id_cliente=id_cliente).order_by("-creada").first()
