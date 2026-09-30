-- =============================================================================
-- 40 · Posiciones consolidadas y variables de riesgo por cliente (mart)
-- -----------------------------------------------------------------------------
-- Insumo del modelo analítico (analytics/modelo.py). Requiere los precios de
-- mercado en mercado.precios (python -m analytics.mercado).
--
--  1. mapa_factores: a cada instrumento se le asigna la serie de precios que
--     representa su riesgo:
--       · precio_directo: la acción o ETF que el cliente tiene.
--       · identificado_por_correlacion: activo sin catálogo cuyos retornos
--         diarios replican los de una acción de la BVC (Renta Variable sin
--         código ↔ TERPEL, r = 0,64, 555 acciones constantes).
--       · proxy: ETF representativo (fondos UCITS, bonos, notas estructuradas).
--       · volatilidad_observada: FICs y CDT sin precio de mercado; se usa la
--         volatilidad de su propio saldo diario, excluyendo días con aportes o
--         retiros (|variación| > 1 %).
--       · solo_tasa_de_cambio: efectivo y money market en USD (riesgo = TRM).
--  2. posiciones_actuales: portafolio local + internacional en COP, con la TRM
--     del corte USD.
--  3. features_cliente: composición por categoría de riesgo, concentración (HHI)
--     y peso internacional.
-- =============================================================================

DO $$
BEGIN
    IF to_regclass('mercado.precios') IS NULL THEN
        RAISE EXCEPTION 'Falta mercado.precios: ejecuta "python -m analytics.mercado" antes del pipeline';
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- 1. Mapa de factores de riesgo
-- ---------------------------------------------------------------------------
CREATE TABLE mart.mapa_factores AS
WITH local AS (
    SELECT d.cod_activo AS id_instrumento, d.activo AS nombre, m.ticker, m.metodo, m.nota,
           CASE
               WHEN d.macroactivo = 'Renta Variable' THEN 'Renta Variable'
               WHEN d.cod_activo IN ('1007', '1008', '1018', '1019', 'SC-FIC') THEN 'Liquidez'
               ELSE 'Renta Fija'
           END AS categoria_riesgo
    FROM core.dim_activo_local d
    LEFT JOIN (VALUES
        ('1002',  'CELSIA.CL',     'precio_directo',               NULL),
        ('1003',  'CEMARGOS.CL',   'precio_directo',               NULL),
        ('1013',  'CEMARGOS.CL',   'precio_directo',               NULL),
        ('1004',  'ECOPETROL.CL',  'precio_directo',               NULL),
        ('1005',  'ETB.CL',        'precio_directo',               NULL),
        ('1011',  'GRUBOLIVAR.CL', 'precio_directo',               NULL),
        ('1012',  'ISA.CL',        'precio_directo',               NULL),
        ('1014',  'CIB',           'proxy',                        'Sin datos locales de PFBCOLOM: ADR de Bancolombia convertido a COP'),
        ('1015',  'PFCEMARGOS.CL', 'precio_directo',               'Confirma la corrección 1115 → 1015 (r = 0,89 con PFCEMARGOS)'),
        ('1016',  'PFCORFICOL.CL', 'precio_directo',               NULL),
        ('1017',  'PFGRUPSURA.CL', 'precio_directo',               NULL),
        ('SC-RV', 'TERPEL.CL',     'identificado_por_correlacion', 'Retornos diarios replican TERPEL (r = 0,64) con 555 acciones constantes'),
        ('1022',  'ICOLCAP.CL',    'proxy',                        'No se identificó con ninguna acción de la BVC: se usa el índice COLCAP')
    ) AS m(cod_activo, ticker, metodo, nota) ON m.cod_activo = d.cod_activo
    WHERE d.filas_en_datos > 0
),
usd AS (
    SELECT
        i.id_instrumento, i.nombre,
        CASE
            WHEN i.clase_activo = 'Liquidez'            THEN 'USD'
            WHEN i.tipo_instrumento IN ('Acción / ETF', 'ETF de renta fija')
                                                        THEN CASE i.simbolo WHEN 'BRK B' THEN 'BRK-B'
                                                                            WHEN 'SQ'    THEN 'XYZ'
                                                                            ELSE i.simbolo END
            WHEN i.tipo_instrumento = 'Nota estructurada' THEN 'SPY'
            WHEN i.tipo_instrumento = 'Bono' THEN
                CASE WHEN i.fecha_vencimiento < DATE '2024-05-30' + INTERVAL '18 months' THEN 'VGSH'
                     WHEN i.nombre ~* 'UNITED STATES TREAS'                             THEN 'IEI'
                     ELSE 'EMB' END
            -- Fondos UCITS: ETF de referencia según la estrategia que indica el nombre
            WHEN i.nombre ~* 'SHORT DURATION'                   THEN 'VGSH'
            WHEN i.nombre ~* '7-10'                             THEN 'IEF'
            WHEN i.nombre ~* '3-7'                              THEN 'IEI'
            WHEN i.nombre ~* 'DEFENSIVE'                        THEN 'AOK'
            WHEN i.nombre ~* 'MODERATE|MULTI.?ASSET INCOME'     THEN 'AOM'
            WHEN i.nombre ~* 'GLOBAL ALLOCATION'                THEN 'AOR'
            WHEN i.nombre ~* 'GROWTH FUND'                      THEN 'AOA'
            WHEN i.nombre ~* 'INCOME|BOND|FIXED'                THEN 'AGG'
            WHEN i.nombre ~* 'HEALTH'                           THEN 'XLV'
            WHEN i.nombre ~* 'TECHNOLOGY'                       THEN 'XLK'
            WHEN i.nombre ~* 'INFRASTRUCTURE'                   THEN 'IGF'
            WHEN i.nombre ~* 'EUROPE'                           THEN 'VGK'
            WHEN i.nombre ~* 'ASIA'                             THEN 'AAXJ'
            WHEN i.nombre ~* 'U\.?S\.? '                        THEN 'SPY'
            ELSE 'ACWI'
        END AS ticker,
        i.clase_activo, i.tipo_instrumento
    FROM core.dim_instrumento_usd i
)
SELECT 'Local' AS origen, id_instrumento, nombre,
       coalesce(ticker, 'SERIE:' || id_instrumento) AS ticker_referencia,
       CASE WHEN ticker = 'CIB' THEN 'USD' ELSE 'COP' END AS moneda_referencia,
       coalesce(metodo, 'volatilidad_observada') AS metodo,
       categoria_riesgo, nota
FROM local
UNION ALL
SELECT 'Internacional', id_instrumento, nombre, ticker, 'USD',
       CASE WHEN ticker = 'USD'                        THEN 'solo_tasa_de_cambio'
            WHEN tipo_instrumento IN ('Acción / ETF', 'ETF de renta fija') THEN 'precio_directo'
            ELSE 'proxy' END,
       CASE WHEN clase_activo IN ('Liquidez', 'Renta Fija', 'Renta Variable', 'Estructurados') THEN clase_activo
            WHEN ticker IN ('AOK', 'AOM', 'AOR', 'AOA')           THEN 'Multiactivo'
            WHEN ticker IN ('VGSH', 'IEF', 'IEI', 'AGG', 'EMB')   THEN 'Renta Fija'
            ELSE 'Renta Variable' END,
       NULL
FROM usd;

ALTER TABLE mart.mapa_factores ADD PRIMARY KEY (origen, id_instrumento);

-- ---------------------------------------------------------------------------
-- Volatilidad observada de las series locales sin precio de mercado
-- ---------------------------------------------------------------------------
CREATE TABLE mart.volatilidad_series_locales AS
WITH retornos AS (
    SELECT cod_activo,
           aba / lag(aba) OVER (PARTITION BY id_cliente, cod_activo ORDER BY fecha) - 1 AS r
    FROM core.aba_local
)
SELECT cod_activo,
       count(*)                                          AS dias,
       round((stddev_samp(r) * sqrt(252))::NUMERIC, 6)   AS volatilidad_anual,
       round((avg(r) * 252)::NUMERIC, 6)                 AS rendimiento_anual
FROM retornos
WHERE abs(r) <= 0.01            -- excluye días con aportes o retiros
  AND cod_activo IN (SELECT id_instrumento FROM mart.mapa_factores
                      WHERE origen = 'Local' AND metodo = 'volatilidad_observada')
GROUP BY cod_activo;

-- ---------------------------------------------------------------------------
-- 2. Posiciones actuales consolidadas en COP
-- ---------------------------------------------------------------------------
CREATE TABLE mart.posiciones_actuales AS
WITH trm AS (
    SELECT u.fecha_corte,
           (SELECT p.cierre FROM mercado.precios p
             WHERE p.ticker = 'USDCOP=X' AND p.fecha <= u.fecha_corte
             ORDER BY p.fecha DESC LIMIT 1) AS trm
    FROM (SELECT DISTINCT fecha_corte FROM mart.portafolio_usd_actual) u
)
SELECT l.id_cliente, 'Local' AS origen, l.fecha_corte, l.cod_activo AS id_instrumento, l.activo AS nombre,
       l.saldo_cop AS valor_cop, NULL::NUMERIC AS valor_usd, NULL::NUMERIC AS trm
FROM mart.portafolio_local_actual l
UNION ALL
SELECT u.id_cliente, 'Internacional', u.fecha_corte, u.id_instrumento, u.nombre,
       round(u.valor_mercado_usd * t.trm, 2), u.valor_mercado_usd, t.trm
FROM mart.portafolio_usd_actual u
JOIN trm t USING (fecha_corte);

ALTER TABLE mart.posiciones_actuales
    ADD COLUMN ticker_referencia TEXT,
    ADD COLUMN moneda_referencia TEXT,
    ADD COLUMN metodo TEXT,
    ADD COLUMN categoria_riesgo TEXT,
    ADD COLUMN peso NUMERIC;

UPDATE mart.posiciones_actuales p
SET ticker_referencia = m.ticker_referencia,
    moneda_referencia = m.moneda_referencia,
    metodo            = m.metodo,
    categoria_riesgo  = m.categoria_riesgo
FROM mart.mapa_factores m
WHERE m.origen = p.origen AND m.id_instrumento = p.id_instrumento;

UPDATE mart.posiciones_actuales p
SET peso = p.valor_cop / t.total
FROM (SELECT id_cliente, sum(valor_cop) AS total FROM mart.posiciones_actuales GROUP BY 1) t
WHERE t.id_cliente = p.id_cliente AND t.total > 0;

-- ---------------------------------------------------------------------------
-- 3. Variables por cliente
-- ---------------------------------------------------------------------------
CREATE TABLE mart.features_cliente AS
SELECT
    p.id_cliente,
    sum(p.valor_cop)                                                          AS aum_cop,
    coalesce(sum(p.peso) FILTER (WHERE p.origen = 'Internacional'), 0)        AS pct_internacional,
    coalesce(sum(p.peso) FILTER (WHERE p.categoria_riesgo = 'Renta Variable'), 0) AS pct_renta_variable,
    coalesce(sum(p.peso) FILTER (WHERE p.categoria_riesgo = 'Renta Fija'), 0)     AS pct_renta_fija,
    coalesce(sum(p.peso) FILTER (WHERE p.categoria_riesgo = 'Multiactivo'), 0)    AS pct_multiactivo,
    coalesce(sum(p.peso) FILTER (WHERE p.categoria_riesgo = 'Estructurados'), 0)  AS pct_estructurados,
    coalesce(sum(p.peso) FILTER (WHERE p.categoria_riesgo = 'Liquidez'), 0)       AS pct_liquidez,
    count(*)                                                                  AS n_posiciones,
    sum(p.peso * p.peso)                                                      AS hhi,
    max(p.peso)                                                               AS peso_max_posicion,
    (array_agg(p.nombre ORDER BY p.peso DESC))[1]                             AS mayor_posicion,
    (array_agg(p.categoria_riesgo ORDER BY p.peso DESC))[1]                   AS categoria_mayor_posicion
FROM mart.posiciones_actuales p
GROUP BY p.id_cliente;

ALTER TABLE mart.features_cliente ADD PRIMARY KEY (id_cliente);
