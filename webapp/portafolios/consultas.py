"""Lectura de las vistas mart del pipeline.

La aplicación solo lee: toda la transformación ya ocurrió en SQL (sql/*.sql).
"""

from django.db import connection


def _filas(sql: str, params=None) -> list[dict]:
    with connection.cursor() as cursor:
        cursor.execute(sql, params or [])
        columnas = [c.name for c in cursor.description]
        return [dict(zip(columnas, fila)) for fila in cursor.fetchall()]


def pipeline_listo() -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('mart.resumen_cliente') IS NOT NULL")
        return cursor.fetchone()[0]


def clientes() -> list[dict]:
    return _filas("""
        SELECT *
        FROM mart.resumen_cliente
        ORDER BY coalesce(total_cop, 0) DESC, id_cliente
    """)


def portafolio_local(id_cliente: str) -> list[dict]:
    return _filas("""
        SELECT fecha_corte, macroactivo, cod_activo, activo, saldo_cop, participacion
        FROM mart.portafolio_local_actual
        WHERE id_cliente = %s
        ORDER BY saldo_cop DESC
    """, [id_cliente])


def portafolio_usd(id_cliente: str) -> list[dict]:
    return _filas("""
        SELECT fecha_corte, clase_activo, tipo_instrumento, id_instrumento, simbolo, nombre,
               cantidad, valor_mercado_usd, participacion, fecha_vencimiento, tasa_cupon
        FROM mart.portafolio_usd_actual
        WHERE id_cliente = %s
        ORDER BY valor_mercado_usd DESC
    """, [id_cliente])


def evolucion_local(id_cliente: str) -> list[dict]:
    return _filas("""
        SELECT fecha, macroactivo, saldo_cop
        FROM mart.evolucion_local
        WHERE id_cliente = %s
        ORDER BY fecha, macroactivo
    """, [id_cliente])


def evolucion_usd(id_cliente: str) -> list[dict]:
    return _filas("""
        SELECT fecha_corte, clase_activo, valor_mercado_usd, es_promedio
        FROM mart.evolucion_usd
        WHERE id_cliente = %s
        ORDER BY fecha_corte, clase_activo
    """, [id_cliente])


def cartera_por(dimension: str) -> list[dict]:
    """Totales de la cartera agrupados por banca o perfil de riesgo."""
    columna = {"banca": "banca", "perfil": "perfil_riesgo"}[dimension]
    return _filas(f"""
        SELECT coalesce({columna}, 'Sin dato') AS grupo,
               count(*)                        AS clientes,
               sum(total_cop)                  AS total_cop,
               sum(total_usd)                  AS total_usd,
               count(total_usd)                AS clientes_usd
        FROM mart.resumen_cliente
        GROUP BY 1
        ORDER BY total_cop DESC NULLS LAST
    """)


def cartera_por_macroactivo() -> list[dict]:
    return _filas("""
        SELECT macroactivo, sum(saldo_cop) AS total_cop, count(DISTINCT id_cliente) AS clientes
        FROM mart.portafolio_local_actual
        GROUP BY 1
        ORDER BY 2 DESC
    """)


def bitacora_calidad() -> list[dict]:
    return _filas("""
        SELECT tabla_origen, regla, accion, filas, descripcion
        FROM calidad.bitacora
        ORDER BY tabla_origen,
                 array_position(ARRAY['descartado', 'corregido', 'imputado', 'informativo'], accion),
                 filas DESC
    """)


def volumen_calidad() -> list[dict]:
    return _filas("SELECT * FROM calidad.resumen_volumen")
