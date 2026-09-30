-- =============================================================================
-- 21 · Portafolio local (core): imputaciones, deduplicación y hecho diario
-- -----------------------------------------------------------------------------
-- Se trabaja sobre core.aba_local_trazabilidad (una fila por fila de raw), que
-- guarda para cada registro las correcciones aplicadas y, si se descarta, el
-- motivo. Sobre ella se aplican, en orden, reglas que miran otras filas:
--
--   A. IDs válidos pero desconocidos (< 50 filas) se tratan como no identificados.
--   B. Imputación de cliente, fecha y código de activo:
--        1) coincidencia exacta: otra fila conocida con la misma fecha, activo y
--           ABA → la fila es un duplicado con un campo dañado.
--        2) hueco único: una sola serie (cliente, activo) no tiene dato en esa
--           fecha → la fila completa ese hueco.
--      Series largas sin código de activo se tratan como un activo propio
--      ("sin código"), no como huecos de otras series.
--   C. Descarte de lo no recuperable.
--   D. Deduplicación por (cliente, fecha, activo): se conserva el ABA más cercano
--      a la mediana de la serie (descarta valores atípicos).
--   E. Separación de IDs truncados que mezclan clientes con perfiles distintos.
--   F. Perfil y banca faltantes: moda de la serie, luego del cliente.
--   G. ABA faltante: último valor conocido de la serie.
--   H. Hecho diario core.aba_local (una fila por cliente, fecha y activo).
-- =============================================================================

CREATE TABLE core.aba_local_trazabilidad AS
SELECT
    _source_row,
    id_cliente AS id_cliente_staging,
    id_cliente,
    id_truncado,
    fecha,
    anio,
    mes,
    macroactivo,
    cod_activo,
    aba,
    cod_perfil_riesgo,
    cod_banca,
    correcciones,
    NULL::TEXT AS motivo_descarte
FROM staging.aba_macroactivos;

CREATE UNIQUE INDEX ON core.aba_local_trazabilidad (_source_row);

-- ---------------------------------------------------------------------------
-- A. Clientes conocidos: IDs con historia suficiente
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE t_clientes ON COMMIT DROP AS
SELECT id_cliente
FROM staging.aba_macroactivos
WHERE id_cliente IS NOT NULL
GROUP BY id_cliente
HAVING count(*) >= 50;

UPDATE core.aba_local_trazabilidad
SET id_cliente   = NULL,
    correcciones = correcciones || 'id_no_reconocido'::TEXT
WHERE id_cliente IS NOT NULL
  AND id_cliente NOT IN (SELECT id_cliente FROM t_clientes);

-- Referencia: filas completas antes de imputar, sus series y el calendario
CREATE TEMP TABLE t_ref ON COMMIT DROP AS
SELECT id_cliente, fecha, cod_activo, aba, macroactivo, cod_perfil_riesgo, cod_banca
FROM core.aba_local_trazabilidad
WHERE id_cliente IS NOT NULL AND fecha IS NOT NULL AND cod_activo IS NOT NULL;

CREATE INDEX ON t_ref (id_cliente, cod_activo, fecha);
CREATE INDEX ON t_ref (fecha, cod_activo);

CREATE TEMP TABLE t_series ON COMMIT DROP AS
SELECT
    id_cliente,
    cod_activo,
    min(fecha)                                        AS desde,
    max(fecha)                                        AS hasta,
    mode() WITHIN GROUP (ORDER BY macroactivo)        AS macroactivo,
    mode() WITHIN GROUP (ORDER BY cod_perfil_riesgo)  AS cod_perfil_riesgo,
    mode() WITHIN GROUP (ORDER BY cod_banca)          AS cod_banca
FROM t_ref
GROUP BY id_cliente, cod_activo;

CREATE TEMP TABLE t_calendario ON COMMIT DROP AS
SELECT DISTINCT fecha FROM t_ref;

-- ---------------------------------------------------------------------------
-- B1. Imputación de cliente
-- ---------------------------------------------------------------------------
WITH candidatos AS (
    SELECT
        t._source_row,
        (SELECT CASE WHEN count(DISTINCT r.id_cliente) = 1 THEN min(r.id_cliente) END
           FROM t_ref r
          WHERE r.fecha = t.fecha AND r.cod_activo = t.cod_activo AND r.aba = t.aba) AS por_coincidencia,
        (SELECT CASE WHEN count(*) = 1 THEN min(s.id_cliente) END
           FROM t_series s
          WHERE s.cod_activo = t.cod_activo
            AND t.fecha BETWEEN s.desde AND s.hasta
            AND (t.cod_perfil_riesgo IS NULL OR t.cod_perfil_riesgo = s.cod_perfil_riesgo)
            AND (t.cod_banca IS NULL OR t.cod_banca = s.cod_banca)
            AND NOT EXISTS (SELECT 1 FROM t_ref r
                             WHERE r.id_cliente = s.id_cliente
                               AND r.cod_activo = s.cod_activo
                               AND r.fecha = t.fecha))                                  AS por_hueco
    FROM core.aba_local_trazabilidad t
    WHERE t.id_cliente IS NULL
      AND t.fecha IS NOT NULL AND t.cod_activo IS NOT NULL AND t.aba IS NOT NULL
)
UPDATE core.aba_local_trazabilidad t
SET id_cliente   = coalesce(c.por_coincidencia, c.por_hueco),
    correcciones = t.correcciones || CASE WHEN c.por_coincidencia IS NOT NULL
                                          THEN 'id_imputado_por_coincidencia_exacta'
                                          ELSE 'id_imputado_por_hueco_en_serie' END
FROM candidatos c
WHERE c._source_row = t._source_row
  AND coalesce(c.por_coincidencia, c.por_hueco) IS NOT NULL;

-- ---------------------------------------------------------------------------
-- B2. Imputación de fecha (filas cuyo día de ingestión se perdió)
-- ---------------------------------------------------------------------------
WITH candidatos AS (
    SELECT
        t._source_row,
        (SELECT CASE WHEN count(DISTINCT r.fecha) = 1 THEN min(r.fecha) END
           FROM t_ref r
          WHERE r.id_cliente = t.id_cliente AND r.cod_activo = t.cod_activo AND r.aba = t.aba
            AND date_trunc('month', r.fecha) = make_date(t.anio, t.mes, 1))            AS por_coincidencia,
        (SELECT CASE WHEN count(*) = 1 THEN min(c.fecha) END
           FROM t_calendario c
           JOIN t_series s ON s.id_cliente = t.id_cliente AND s.cod_activo = t.cod_activo
          WHERE c.fecha BETWEEN s.desde AND s.hasta
            AND date_trunc('month', c.fecha) = make_date(t.anio, t.mes, 1)
            AND NOT EXISTS (SELECT 1 FROM t_ref r
                             WHERE r.id_cliente = t.id_cliente
                               AND r.cod_activo = t.cod_activo
                               AND r.fecha = c.fecha))                                  AS por_hueco
    FROM core.aba_local_trazabilidad t
    WHERE t.fecha IS NULL
      AND t.id_cliente IS NOT NULL AND t.cod_activo IS NOT NULL
      AND t.anio IS NOT NULL AND t.mes BETWEEN 1 AND 12
)
UPDATE core.aba_local_trazabilidad t
SET fecha        = coalesce(c.por_coincidencia, c.por_hueco),
    correcciones = t.correcciones || CASE WHEN c.por_coincidencia IS NOT NULL
                                          THEN 'fecha_imputada_por_coincidencia_exacta'
                                          ELSE 'fecha_imputada_por_hueco_en_serie' END
FROM candidatos c
WHERE c._source_row = t._source_row
  AND coalesce(c.por_coincidencia, c.por_hueco) IS NOT NULL;

-- ---------------------------------------------------------------------------
-- B3. Imputación de código de activo
-- ---------------------------------------------------------------------------
-- Series persistentes sin código (≥ 10 filas del mismo cliente y macroactivo):
-- son un activo real que no trae código → código sintético "SC-<macroactivo>"
WITH persistentes AS (
    SELECT id_cliente, macroactivo
    FROM core.aba_local_trazabilidad
    WHERE cod_activo IS NULL AND id_cliente IS NOT NULL AND macroactivo IS NOT NULL
    GROUP BY id_cliente, macroactivo
    HAVING count(*) >= 10
)
UPDATE core.aba_local_trazabilidad t
SET cod_activo   = 'SC-' || CASE t.macroactivo WHEN 'FICs' THEN 'FIC'
                                               WHEN 'Renta Variable' THEN 'RV'
                                               ELSE 'RF' END,
    correcciones = t.correcciones || 'cod_activo_sintetico_serie_sin_codigo'::TEXT
FROM persistentes p
WHERE t.cod_activo IS NULL
  AND t.id_cliente = p.id_cliente
  AND t.macroactivo = p.macroactivo;

-- Filas sueltas sin código: coincidencia exacta o hueco único en las series del cliente
WITH candidatos AS (
    SELECT
        t._source_row,
        (SELECT CASE WHEN count(DISTINCT r.cod_activo) = 1 THEN min(r.cod_activo) END
           FROM t_ref r
          WHERE r.id_cliente = t.id_cliente AND r.fecha = t.fecha AND r.aba = t.aba
            AND (t.macroactivo IS NULL OR r.macroactivo = t.macroactivo))              AS por_coincidencia,
        (SELECT CASE WHEN count(*) = 1 THEN min(s.cod_activo) END
           FROM t_series s
          WHERE s.id_cliente = t.id_cliente
            AND (t.macroactivo IS NULL OR s.macroactivo = t.macroactivo)
            AND t.fecha BETWEEN s.desde AND s.hasta
            AND NOT EXISTS (SELECT 1 FROM t_ref r
                             WHERE r.id_cliente = s.id_cliente
                               AND r.cod_activo = s.cod_activo
                               AND r.fecha = t.fecha))                                  AS por_hueco
    FROM core.aba_local_trazabilidad t
    WHERE t.cod_activo IS NULL
      AND t.id_cliente IS NOT NULL AND t.fecha IS NOT NULL AND t.aba IS NOT NULL
)
UPDATE core.aba_local_trazabilidad t
SET cod_activo   = coalesce(c.por_coincidencia, c.por_hueco),
    correcciones = t.correcciones || CASE WHEN c.por_coincidencia IS NOT NULL
                                          THEN 'cod_activo_imputado_por_coincidencia_exacta'
                                          ELSE 'cod_activo_imputado_por_hueco_en_serie' END
FROM candidatos c
WHERE c._source_row = t._source_row
  AND coalesce(c.por_coincidencia, c.por_hueco) IS NOT NULL;

-- Lo que queda sin código pero con macroactivo: activo sin código de ese macroactivo
UPDATE core.aba_local_trazabilidad
SET cod_activo   = 'SC-' || CASE macroactivo WHEN 'FICs' THEN 'FIC'
                                             WHEN 'Renta Variable' THEN 'RV'
                                             ELSE 'RF' END,
    correcciones = correcciones || 'cod_activo_sintetico_serie_sin_codigo'::TEXT
WHERE cod_activo IS NULL AND macroactivo IS NOT NULL AND id_cliente IS NOT NULL;

-- Macroactivo faltante: el del catálogo de activos (inferido de los datos)
UPDATE core.aba_local_trazabilidad t
SET macroactivo  = d.macroactivo,
    correcciones = t.correcciones || 'macroactivo_desde_catalogo'::TEXT
FROM core.dim_activo_local d
WHERE t.macroactivo IS NULL AND t.cod_activo = d.cod_activo;

-- Activos sintéticos en la dimensión
INSERT INTO core.dim_activo_local (cod_activo, activo, macroactivo, fuera_de_catalogo,
                                   codigo_corregido, nombre_duplicado, filas_en_datos)
SELECT cod_activo,
       macroactivo || ' sin código',
       macroactivo, TRUE, FALSE, FALSE, count(*)
FROM core.aba_local_trazabilidad
WHERE cod_activo LIKE 'SC-%'
GROUP BY cod_activo, macroactivo;

-- ---------------------------------------------------------------------------
-- C. Descarte de lo no recuperable
-- ---------------------------------------------------------------------------
UPDATE core.aba_local_trazabilidad
SET motivo_descarte = CASE
        WHEN id_cliente IS NULL THEN 'cliente_no_identificable'
        WHEN fecha IS NULL      THEN 'fecha_no_determinable'
        ELSE 'activo_no_identificable'
    END
WHERE id_cliente IS NULL OR fecha IS NULL OR cod_activo IS NULL;

-- ---------------------------------------------------------------------------
-- D. Deduplicación por (cliente, fecha, activo)
-- ---------------------------------------------------------------------------
WITH mediana AS (
    SELECT id_cliente, cod_activo,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY aba) AS mediana
    FROM core.aba_local_trazabilidad
    WHERE motivo_descarte IS NULL AND aba IS NOT NULL
    GROUP BY id_cliente, cod_activo
),
ranking AS (
    SELECT
        t._source_row,
        t.aba,
        row_number()  OVER w AS rn,
        first_value(t.aba) OVER w AS aba_conservado
    FROM core.aba_local_trazabilidad t
    LEFT JOIN mediana m USING (id_cliente, cod_activo)
    WHERE t.motivo_descarte IS NULL
    WINDOW w AS (PARTITION BY t.id_cliente, t.fecha, t.cod_activo
                 ORDER BY (t.aba IS NULL), abs(t.aba - m.mediana),
                          cardinality(t.correcciones), t._source_row)
)
UPDATE core.aba_local_trazabilidad t
SET motivo_descarte = CASE WHEN r.aba IS NULL OR r.aba = r.aba_conservado
                           THEN 'duplicado'
                           ELSE 'duplicado_valor_atipico' END
FROM ranking r
WHERE r._source_row = t._source_row AND r.rn > 1;

-- ---------------------------------------------------------------------------
-- E. IDs truncados que mezclan clientes: series del mismo ID con perfiles de
--    riesgo distintos en las mismas fechas no pueden ser un mismo cliente.
-- ---------------------------------------------------------------------------
WITH perfil_serie AS (
    SELECT id_cliente, cod_activo,
           mode() WITHIN GROUP (ORDER BY cod_perfil_riesgo) AS perfil
    FROM core.aba_local_trazabilidad
    WHERE motivo_descarte IS NULL AND id_truncado
    GROUP BY id_cliente, cod_activo
),
mezclados AS (
    SELECT id_cliente
    FROM perfil_serie
    GROUP BY id_cliente
    HAVING count(DISTINCT perfil) > 1
),
sufijo AS (
    SELECT p.id_cliente, p.cod_activo,
           dense_rank() OVER (PARTITION BY p.id_cliente ORDER BY p.perfil) AS n
    FROM perfil_serie p
    JOIN mezclados USING (id_cliente)
)
UPDATE core.aba_local_trazabilidad t
SET id_cliente   = t.id_cliente || '-' || s.n,
    correcciones = t.correcciones || 'cliente_separado_por_perfil_distinto'::TEXT
FROM sufijo s
WHERE t.id_cliente = s.id_cliente AND t.cod_activo = s.cod_activo;

-- ---------------------------------------------------------------------------
-- F. Perfil de riesgo y banca faltantes
-- ---------------------------------------------------------------------------
WITH serie AS (
    SELECT id_cliente, cod_activo,
           mode() WITHIN GROUP (ORDER BY cod_perfil_riesgo) AS perfil,
           mode() WITHIN GROUP (ORDER BY cod_banca)         AS banca
    FROM core.aba_local_trazabilidad
    WHERE motivo_descarte IS NULL
    GROUP BY id_cliente, cod_activo
),
cliente AS (
    SELECT id_cliente,
           mode() WITHIN GROUP (ORDER BY cod_perfil_riesgo) AS perfil,
           mode() WITHIN GROUP (ORDER BY cod_banca)         AS banca
    FROM core.aba_local_trazabilidad
    WHERE motivo_descarte IS NULL
    GROUP BY id_cliente
)
UPDATE core.aba_local_trazabilidad t
SET cod_perfil_riesgo = coalesce(t.cod_perfil_riesgo, s.perfil, c.perfil, 1466),
    cod_banca         = coalesce(t.cod_banca, s.banca, c.banca),
    correcciones      = t.correcciones
                        || CASE WHEN t.cod_perfil_riesgo IS NULL AND coalesce(s.perfil, c.perfil) IS NULL
                                     THEN ARRAY['perfil_sin_dato_a_sin_definir']
                                WHEN t.cod_perfil_riesgo IS NULL
                                     THEN ARRAY['perfil_imputado_del_cliente']
                                ELSE ARRAY[]::TEXT[] END
                        || CASE WHEN t.cod_banca IS NULL THEN ARRAY['banca_imputada_del_cliente']
                                ELSE ARRAY[]::TEXT[] END
FROM serie s
JOIN cliente c USING (id_cliente)
WHERE t.motivo_descarte IS NULL
  AND t.id_cliente = s.id_cliente AND t.cod_activo = s.cod_activo
  AND (t.cod_perfil_riesgo IS NULL OR t.cod_banca IS NULL);

-- ---------------------------------------------------------------------------
-- G. ABA faltante: último valor conocido de la serie (o el siguiente)
-- ---------------------------------------------------------------------------
UPDATE core.aba_local_trazabilidad t
SET aba = coalesce(
        (SELECT r.aba FROM core.aba_local_trazabilidad r
          WHERE r.id_cliente = t.id_cliente AND r.cod_activo = t.cod_activo
            AND r.motivo_descarte IS NULL AND r.aba IS NOT NULL AND r.fecha < t.fecha
          ORDER BY r.fecha DESC LIMIT 1),
        (SELECT r.aba FROM core.aba_local_trazabilidad r
          WHERE r.id_cliente = t.id_cliente AND r.cod_activo = t.cod_activo
            AND r.motivo_descarte IS NULL AND r.aba IS NOT NULL AND r.fecha > t.fecha
          ORDER BY r.fecha LIMIT 1)),
    correcciones = t.correcciones || 'aba_imputado_ultimo_valor_conocido'::TEXT
WHERE t.motivo_descarte IS NULL AND t.aba IS NULL;

UPDATE core.aba_local_trazabilidad
SET motivo_descarte = 'aba_sin_valor'
WHERE motivo_descarte IS NULL AND aba IS NULL;

-- ---------------------------------------------------------------------------
-- H. Hecho diario: una fila por (cliente, fecha, activo)
--    Tras las imputaciones todas las series quedan completas en su rango; las
--    que terminan antes del último corte son activos que el cliente ya no tiene
--    (p. ej. los fondos cerrados), no datos faltantes.
-- ---------------------------------------------------------------------------
CREATE TABLE core.aba_local AS
SELECT id_cliente, fecha, cod_activo, macroactivo, aba,
       cod_perfil_riesgo, cod_banca, _source_row, correcciones
FROM core.aba_local_trazabilidad
WHERE motivo_descarte IS NULL;

ALTER TABLE core.aba_local ADD PRIMARY KEY (id_cliente, fecha, cod_activo);
