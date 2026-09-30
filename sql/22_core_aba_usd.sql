-- =============================================================================
-- 22 · Portafolio internacional (core): instrumentos y hecho por corte
-- -----------------------------------------------------------------------------
-- Hallazgo clave: la ingestión del 2024-03-01 no es una foto, sino ~63
-- valoraciones por cliente e instrumento, con cantidad constante, sin fecha de
-- valoración y desordenadas en el archivo (un histórico de precios empaquetado).
-- Como no se puede ordenar en el tiempo, cada (cliente, corte, instrumento) se
-- consolida en una fila:
--   · cantidad: la más frecuente (es constante)
--   · valor de mercado: promedio de las observaciones del corte
--   · mínimo, máximo y desviación estándar: miden la volatilidad observada,
--     útil para el modelo analítico
-- Los cortes 2024-04-26 y 2024-05-30 traen una observación por instrumento,
-- así que la consolidación no los altera.
-- =============================================================================

CREATE TABLE core.dim_instrumento_usd AS
WITH base AS (
    SELECT
        id_instrumento,
        mode() WITHIN GROUP (ORDER BY isin)              AS isin,
        mode() WITHIN GROUP (ORDER BY cusip)             AS cusip,
        mode() WITHIN GROUP (ORDER BY simbolo)           AS simbolo,
        mode() WITHIN GROUP (ORDER BY nombre)            AS nombre,
        max(fecha_vencimiento)                           AS fecha_vencimiento,
        mode() WITHIN GROUP (ORDER BY tasa_cupon)        AS tasa_cupon
    FROM staging.aba_usd_internacional
    WHERE motivo_descarte IS NULL AND id_instrumento IS NOT NULL
    GROUP BY id_instrumento
),
tipo AS (
    SELECT
        b.*,
        CASE
            WHEN cusip = 'USD999997'        THEN 'Efectivo'
            WHEN cusip = 'MONEYMRKT'        THEN 'Fondo money market'
            WHEN isin ~ '^(LU|IE)'          THEN 'Fondo mutuo (UCITS)'
            -- notas ligadas a índices (autocall, twin-win), aunque tengan ISIN XS
            WHEN isin ~ '^CH' OR nombre ~* '\m(LKD|LNKD|LINKED|AUTOCALL|TWIN)' THEN 'Nota estructurada'
            WHEN fecha_vencimiento IS NOT NULL THEN 'Bono'
            -- ETF sin vencimiento que invierte en bonos (p. ej. VGSH, Tesoros de corto plazo)
            WHEN nombre ~* '\m(TREAS|TREASURY|BOND|BD)\M' THEN 'ETF de renta fija'
            ELSE 'Acción / ETF'
        END AS tipo_instrumento
    FROM base b
)
SELECT
    *,
    CASE tipo_instrumento
        WHEN 'Efectivo'            THEN 'Liquidez'
        WHEN 'Fondo money market'  THEN 'Liquidez'
        WHEN 'Fondo mutuo (UCITS)' THEN 'Fondos'
        WHEN 'Nota estructurada'   THEN 'Estructurados'
        WHEN 'Bono'                THEN 'Renta Fija'
        WHEN 'ETF de renta fija'   THEN 'Renta Fija'
        ELSE 'Renta Variable'
    END AS clase_activo,
    left(isin, 2) AS pais_isin
FROM tipo;

ALTER TABLE core.dim_instrumento_usd ADD PRIMARY KEY (id_instrumento);


CREATE TABLE core.aba_usd AS
SELECT
    id_cliente,
    fecha_corte,
    id_instrumento,
    mode() WITHIN GROUP (ORDER BY cantidad)  AS cantidad,
    round(avg(valor_mercado_usd), 2)         AS valor_mercado_usd,
    count(*)                                 AS n_observaciones,
    min(valor_mercado_usd)                   AS valor_min_usd,
    max(valor_mercado_usd)                   AS valor_max_usd,
    round(stddev_samp(valor_mercado_usd), 2) AS desviacion_usd
FROM staging.aba_usd_internacional
WHERE motivo_descarte IS NULL
GROUP BY id_cliente, fecha_corte, id_instrumento;

ALTER TABLE core.aba_usd ADD PRIMARY KEY (id_cliente, fecha_corte, id_instrumento);
