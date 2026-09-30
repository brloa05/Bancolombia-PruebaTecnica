-- =============================================================================
-- 00 · Esquemas y funciones auxiliares
-- -----------------------------------------------------------------------------
-- Capas del pipeline:
--   raw      tablas 1:1 con los CSV (las crea etl/load_raw.py)
--   staging  limpieza fila a fila: tipado, normalización, reparación estructural
--   core     reglas entre filas: imputaciones, deduplicación, dimensiones y hechos
--   mart     vistas de negocio listas para la aplicación
--   calidad  bitácora de reglas de calidad aplicadas
-- El pipeline es idempotente: cada ejecución reconstruye staging, core y mart.
-- =============================================================================

DROP SCHEMA IF EXISTS mart    CASCADE;
DROP SCHEMA IF EXISTS core    CASCADE;
DROP SCHEMA IF EXISTS staging CASCADE;
DROP SCHEMA IF EXISTS calidad CASCADE;

CREATE SCHEMA staging;
CREATE SCHEMA core;
CREATE SCHEMA mart;
CREATE SCHEMA calidad;

-- Texto limpio: recorta espacios y convierte los marcadores de vacío en NULL
CREATE FUNCTION staging.limpiar(v TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN btrim(v) IN ('', 'None', 'none', 'nan', 'NaN', 'NULL', 'null') THEN NULL
        ELSE regexp_replace(btrim(v), '\s+', ' ', 'g')
    END
$$;

-- Número seguro: NULL si el texto no es numérico (en vez de fallar)
CREATE FUNCTION staging.a_numero(v TEXT) RETURNS NUMERIC
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN btrim(v) ~ '^-?\d+(\.\d+)?([eE][+-]?\d+)?$' THEN btrim(v)::NUMERIC
    END
$$;

-- Fecha segura: NULL si la combinación año/mes/día no es válida
CREATE FUNCTION staging.fecha_segura(anio INT, mes INT, dia INT) RETURNS DATE
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
    RETURN make_date(anio, mes, dia);
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END
$$;

-- Bitácora de calidad: una fila por regla aplicada (la llenan los scripts 9x)
CREATE TABLE calidad.bitacora (
    id             SERIAL PRIMARY KEY,
    tabla_origen   TEXT    NOT NULL,
    regla          TEXT    NOT NULL,
    accion         TEXT    NOT NULL,   -- corregido | imputado | descartado | informativo
    filas          INTEGER NOT NULL,
    descripcion    TEXT
);
