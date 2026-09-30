-- =============================================================================
-- 11 · Staging de historico_aba_macroactivos (portafolio local, COP)
-- -----------------------------------------------------------------------------
-- Una fila de staging por cada fila de raw (trazable por _source_row).
-- Aquí solo se aplican reglas que dependen de la propia fila:
--
--  1. Reconstrucción de IDs partidos por corrimiento de columnas
--       · a la derecha:  id vacío o '100', y el resto del ID en 'macroactivo'
--       · a la izquierda: id = '100' + macroactivo ('100FICs') y el resto del
--         ID en 'ingestion_day' (el día de ingestión se pierde)
--  2. Reasignación de columnas por dominio: cada valor se ubica en la columna
--     cuyo dominio cumple, sin importar la posición en que llegó.
--       FICs | Renta Variable | Renta Fija   -> macroactivo
--       PR | PN | PF | EG | PY               -> cod_banca
--       1466..1469                           -> cod_perfil_riesgo
--       10xx | 10007                         -> cod_activo
--       20xx                                 -> año del periodo
--       1..2 dígitos                         -> mes del periodo
--       cualquier otro número                -> aba
--     Con una sola regla se reparan todas las variantes de filas corridas.
--  3. Tipado seguro, código 10007 -> 1007 y marca de IDs en notación científica.
--
-- Las reglas que necesitan mirar otras filas (imputar IDs, fechas o códigos,
-- deduplicar) se aplican en core (21_core_aba_local.sql).
-- =============================================================================

CREATE TABLE staging.aba_macroactivos AS
WITH src AS (
    SELECT
        r._source_row,
        staging.limpiar(r.ingestion_year)     AS ing_anio,
        staging.limpiar(r.ingestion_month)    AS ing_mes,
        staging.limpiar(r.ingestion_day)      AS ing_dia,
        staging.limpiar(r.id_sistema_cliente) AS id_raw,
        ARRAY[
            staging.limpiar(r.macroactivo),
            staging.limpiar(r.cod_activo),
            staging.limpiar(r.aba),
            staging.limpiar(r.cod_perfil_riesgo),
            staging.limpiar(r.cod_banca),
            staging.limpiar(r.year),
            staging.limpiar(r.month)
        ] AS valores
    FROM raw.historico_aba_macroactivos r
),
-- 1. Detección de IDs partidos
corrimiento AS (
    SELECT
        s.*,
        CASE
            WHEN s.id_raw ~ '^100[A-Za-z ]+$'                                   THEN 'izquierda'
            WHEN coalesce(s.id_raw, '100') = '100' AND s.valores[1] ~ '^\d{7,9}$' THEN 'derecha'
        END AS id_partido
    FROM src s
),
ajuste AS (
    SELECT
        _source_row,
        id_raw,
        id_partido,
        ing_anio,
        ing_mes,
        CASE WHEN id_partido = 'izquierda' THEN NULL ELSE ing_dia END AS ing_dia,
        CASE id_partido
            WHEN 'izquierda' THEN '100' || ing_dia
            WHEN 'derecha'   THEN '100' || valores[1]
            ELSE id_raw
        END AS id_reconstruido,
        CASE id_partido
            WHEN 'izquierda' THEN substring(id_raw FROM 4) || valores   -- 'FICs' vuelve a la bolsa de valores
            WHEN 'derecha'   THEN valores[2:7]                          -- el fragmento de ID sale de la bolsa
            ELSE valores
        END AS valores
    FROM corrimiento
),
-- 2. Clasificación de cada valor por su dominio
clasificado AS (
    SELECT
        a._source_row,
        u.pos,
        u.val,
        CASE
            WHEN u.val IN ('FICs', 'Renta Variable', 'Renta Fija') THEN 'macroactivo'
            WHEN u.val ~ '^(PR|PN|PF|EG|PY)$'                     THEN 'cod_banca'
            WHEN u.val ~ '^14(66|67|68|69)$'                      THEN 'cod_perfil_riesgo'
            WHEN u.val ~ '^(10\d{2}|1115|10007)$'                 THEN 'cod_activo'
            WHEN u.val ~ '^20\d{2}$'                              THEN 'anio'
            WHEN u.val ~ '^\d{1,2}$'                              THEN 'mes'
            WHEN u.val ~ '^\d+(\.\d+)?$'                          THEN 'aba'
            ELSE 'no_reconocido'
        END AS campo
    FROM ajuste a
    CROSS JOIN LATERAL unnest(a.valores) WITH ORDINALITY AS u(val, pos)
    WHERE u.val IS NOT NULL
),
pivote AS (
    SELECT
        _source_row,
        (array_agg(val ORDER BY pos) FILTER (WHERE campo = 'macroactivo'))[1]       AS macroactivo,
        (array_agg(val ORDER BY pos) FILTER (WHERE campo = 'cod_activo'))[1]        AS cod_activo,
        (array_agg(val ORDER BY pos) FILTER (WHERE campo = 'aba'))[1]               AS aba,
        (array_agg(val ORDER BY pos) FILTER (WHERE campo = 'cod_perfil_riesgo'))[1] AS cod_perfil_riesgo,
        (array_agg(val ORDER BY pos) FILTER (WHERE campo = 'cod_banca'))[1]         AS cod_banca,
        (array_agg(val ORDER BY pos) FILTER (WHERE campo = 'anio'))[1]              AS periodo_anio,
        (array_agg(val ORDER BY pos) FILTER (WHERE campo = 'mes'))[1]               AS periodo_mes,
        array_agg(val ORDER BY pos) FILTER (WHERE campo = 'no_reconocido')         AS no_reconocidos,
        -- ¿algún valor llegó en una columna distinta a la que le corresponde?
        bool_or(campo IS DISTINCT FROM (ARRAY['macroactivo', 'cod_activo', 'aba', 'cod_perfil_riesgo',
                                              'cod_banca', 'anio', 'mes'])[pos])   AS reasignado
    FROM clasificado
    GROUP BY _source_row
),
tipado AS (
    SELECT
        a._source_row,
        a.id_raw,
        a.id_partido,
        -- 3. ID válido: numérico (10-12 dígitos) o notación científica; lo demás se descarta
        CASE
            WHEN a.id_reconstruido ~ '^\d{9,12}$'          THEN a.id_reconstruido
            WHEN a.id_reconstruido ~ '^\d\.\d+E\+\d{2}$'   THEN a.id_reconstruido
        END                                                     AS id_cliente,
        coalesce(a.id_reconstruido ~ 'E\+', FALSE)              AS id_truncado,
        staging.a_numero(a.ing_anio)::INT                       AS anio,
        coalesce(staging.a_numero(a.ing_mes), staging.a_numero(p.periodo_mes))::INT AS mes,
        staging.a_numero(a.ing_dia)::INT                        AS dia,
        a.ing_mes IS NULL AND p.periodo_mes IS NOT NULL         AS mes_desde_periodo,
        p.macroactivo,
        CASE WHEN p.cod_activo = '10007' THEN '1007' ELSE p.cod_activo END AS cod_activo,
        p.cod_activo                                            AS cod_activo_origen,
        staging.a_numero(p.aba)                                 AS aba,
        p.cod_perfil_riesgo::INT                                AS cod_perfil_riesgo,
        p.cod_banca,
        p.no_reconocidos,
        coalesce(p.reasignado, FALSE) AND a.id_partido IS NULL  AS reasignado
    FROM ajuste a
    LEFT JOIN pivote p USING (_source_row)
)
SELECT
    _source_row,
    id_cliente,
    id_raw                                   AS id_cliente_origen,
    id_truncado,
    staging.fecha_segura(anio, mes, dia)     AS fecha,
    anio,
    mes,
    macroactivo,
    cod_activo,
    aba,
    cod_perfil_riesgo,
    cod_banca,
    array_remove(ARRAY[
        CASE WHEN id_partido IS NOT NULL       THEN 'id_reconstruido_corrimiento_' || id_partido END,
        CASE WHEN reasignado                   THEN 'columnas_reasignadas_por_dominio' END,
        CASE WHEN mes_desde_periodo            THEN 'mes_tomado_de_periodo' END,
        CASE WHEN cod_activo_origen = '10007'  THEN 'cod_activo_10007_a_1007' END,
        CASE WHEN id_truncado                  THEN 'id_en_notacion_cientifica' END,
        CASE WHEN id_partido = 'izquierda'     THEN 'dia_ingestion_perdido' END,
        CASE WHEN id_cliente IS NULL           THEN 'id_cliente_invalido' END
    ], NULL)                                 AS correcciones,
    no_reconocidos
FROM tipado;

CREATE UNIQUE INDEX ON staging.aba_macroactivos (_source_row);
