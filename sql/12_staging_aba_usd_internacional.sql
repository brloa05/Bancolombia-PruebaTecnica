-- =============================================================================
-- 12 · Staging de historico_aba_usd_internacional (portafolio internacional, USD)
-- -----------------------------------------------------------------------------
-- Una fila de staging por cada fila de raw. Reglas:
--   · Filas corridas (detectadas por el tipo de dato de cada columna):
--       - a la derecha: se insertó un valor extra y el nombre quedó en
--         'cantidad' (cantidad no numérica) → se corre todo a la izquierda.
--       - a la izquierda: falta el nombre y 'valor_mercado' trae una fecha
--         → se corre todo a la derecha; el nombre se completa en core.
--       - ID de cliente no numérico: el ID se perdió → se descarta.
--   · 'Liquidez' en isin identifica efectivo (CASH) y fondo money market
--     (MONEYMRKT): el instrumento se identifica por cusip en esos casos.
--   · fecha_vencimiento viene en formato MM/DD/YYYY; 1/01/1900 = sin vencimiento.
--   · simbol 'None'/'nan' → NULL; nombres con espacios sobrantes → normalizados.
-- =============================================================================

CREATE TABLE staging.aba_usd_internacional AS
WITH src AS (
    SELECT
        _source_row,
        staging.a_numero(ingestion_year)::INT  AS anio,
        staging.a_numero(ingestion_month)::INT AS mes,
        staging.a_numero(ingestion_day)::INT   AS dia,
        staging.limpiar(id_sistema_cliente)    AS id_raw,
        staging.limpiar(simbol)                AS simbolo,
        staging.limpiar(cusip)                 AS cusip,
        staging.limpiar(isin)                  AS isin,
        staging.limpiar(nombre_activo)         AS nombre,
        staging.limpiar(cantidad)              AS cantidad,
        staging.limpiar(valor_mercado)         AS valor,
        staging.limpiar(fecha_vencimiento)     AS vencimiento,
        staging.limpiar(tasa_cupon)            AS cupon
    FROM raw.historico_aba_usd_internacional
),
deteccion AS (
    SELECT
        *,
        CASE
            WHEN cantidad IS NOT NULL AND staging.a_numero(cantidad) IS NULL THEN 'derecha'
            WHEN valor ~ '^\d{1,2}/\d{1,2}/\d{4}$'                         THEN 'izquierda'
        END AS corrimiento
    FROM src
),
reparado AS (
    SELECT
        _source_row, anio, mes, dia, id_raw, simbolo, cusip, isin, corrimiento,
        CASE corrimiento WHEN 'derecha' THEN cantidad    WHEN 'izquierda' THEN NULL        ELSE nombre      END AS nombre,
        CASE corrimiento WHEN 'derecha' THEN valor       WHEN 'izquierda' THEN nombre      ELSE cantidad    END AS cantidad,
        CASE corrimiento WHEN 'derecha' THEN vencimiento WHEN 'izquierda' THEN cantidad    ELSE valor       END AS valor,
        CASE corrimiento WHEN 'derecha' THEN cupon       WHEN 'izquierda' THEN valor       ELSE vencimiento END AS vencimiento,
        CASE corrimiento WHEN 'derecha' THEN NULL        WHEN 'izquierda' THEN vencimiento ELSE cupon       END AS cupon
    FROM deteccion
)
SELECT
    _source_row,
    CASE WHEN id_raw ~ '^\d{9,12}$' THEN id_raw END           AS id_cliente,
    id_raw                                                    AS id_cliente_origen,
    staging.fecha_segura(anio, mes, dia)                      AS fecha_corte,
    CASE WHEN isin = 'Liquidez' THEN cusip ELSE isin END      AS id_instrumento,
    NULLIF(isin, 'Liquidez')                                  AS isin,
    cusip,
    simbolo,
    nombre,
    staging.a_numero(cantidad)                                AS cantidad,
    staging.a_numero(valor)                                   AS valor_mercado_usd,
    NULLIF(CASE WHEN vencimiento ~ '^\d{1,2}/\d{1,2}/\d{4}$'
                THEN to_date(vencimiento, 'MM/DD/YYYY') END,
           DATE '1900-01-01')                                 AS fecha_vencimiento,
    staging.a_numero(cupon)                                   AS tasa_cupon,
    array_remove(ARRAY[
        CASE WHEN corrimiento IS NOT NULL THEN 'columnas_corridas_' || corrimiento END,
        CASE WHEN corrimiento = 'izquierda' THEN 'nombre_perdido' END
    ], NULL)                                                  AS correcciones,
    CASE WHEN id_raw !~ '^\d{9,12}$' OR id_raw IS NULL
         THEN 'cliente_no_identificable' END                  AS motivo_descarte
FROM reparado;

CREATE UNIQUE INDEX ON staging.aba_usd_internacional (_source_row);
