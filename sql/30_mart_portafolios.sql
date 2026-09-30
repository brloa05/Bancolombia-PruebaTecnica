-- =============================================================================
-- 30 · Vistas de negocio (mart) para la aplicación
-- -----------------------------------------------------------------------------
-- "Última fecha disponible" se resuelve por cliente: el último corte en que
-- el cliente tiene datos en cada portafolio.
-- =============================================================================

-- Portafolio local (COP) de cada cliente en su último corte
CREATE VIEW mart.portafolio_local_actual AS
WITH ultimo AS (
    SELECT id_cliente, max(fecha) AS fecha
    FROM core.aba_local
    GROUP BY id_cliente
)
SELECT
    a.id_cliente,
    a.fecha                                                       AS fecha_corte,
    a.macroactivo,
    a.cod_activo,
    d.activo,
    a.aba                                                         AS saldo_cop,
    round(a.aba / sum(a.aba) OVER (PARTITION BY a.id_cliente), 6) AS participacion
FROM core.aba_local a
JOIN ultimo u                 ON u.id_cliente = a.id_cliente AND u.fecha = a.fecha
JOIN core.dim_activo_local d  ON d.cod_activo = a.cod_activo;


-- Portafolio internacional (USD) de cada cliente en su último corte
CREATE VIEW mart.portafolio_usd_actual AS
WITH ultimo AS (
    SELECT id_cliente, max(fecha_corte) AS fecha_corte
    FROM core.aba_usd
    GROUP BY id_cliente
)
SELECT
    a.id_cliente,
    a.fecha_corte,
    i.clase_activo,
    i.tipo_instrumento,
    a.id_instrumento,
    i.simbolo,
    i.nombre,
    a.cantidad,
    a.valor_mercado_usd,
    round(a.valor_mercado_usd / nullif(sum(a.valor_mercado_usd) OVER (PARTITION BY a.id_cliente), 0), 6)
                                                                  AS participacion,
    i.fecha_vencimiento,
    i.tasa_cupon
FROM core.aba_usd a
JOIN ultimo u                    ON u.id_cliente = a.id_cliente AND u.fecha_corte = a.fecha_corte
JOIN core.dim_instrumento_usd i  ON i.id_instrumento = a.id_instrumento;


-- Evolución diaria del portafolio local por macroactivo
CREATE VIEW mart.evolucion_local AS
SELECT id_cliente, fecha, macroactivo, sum(aba) AS saldo_cop
FROM core.aba_local
GROUP BY id_cliente, fecha, macroactivo;


-- Evolución por corte del portafolio internacional por clase de activo
CREATE VIEW mart.evolucion_usd AS
SELECT a.id_cliente, a.fecha_corte, i.clase_activo, sum(a.valor_mercado_usd) AS valor_mercado_usd
FROM core.aba_usd a
JOIN core.dim_instrumento_usd i USING (id_instrumento)
GROUP BY a.id_cliente, a.fecha_corte, i.clase_activo;


-- Ficha resumen por cliente: datos maestros + totales y composición actual
CREATE VIEW mart.resumen_cliente AS
WITH local AS (
    SELECT
        id_cliente,
        max(fecha_corte)                                                  AS fecha_corte_local,
        sum(saldo_cop)                                                    AS total_cop,
        count(*)                                                          AS n_activos_local,
        coalesce(sum(participacion) FILTER (WHERE macroactivo = 'Renta Variable'), 0) AS pct_renta_variable,
        coalesce(sum(participacion) FILTER (WHERE macroactivo = 'Renta Fija'), 0)     AS pct_renta_fija,
        coalesce(sum(participacion) FILTER (WHERE macroactivo = 'FICs'), 0)           AS pct_fics
    FROM mart.portafolio_local_actual
    GROUP BY id_cliente
),
usd AS (
    SELECT
        id_cliente,
        max(fecha_corte)          AS fecha_corte_usd,
        sum(valor_mercado_usd)    AS total_usd,
        count(*)                  AS n_instrumentos_usd
    FROM mart.portafolio_usd_actual
    GROUP BY id_cliente
)
SELECT
    c.id_cliente,
    c.id_cliente_completo,
    c.id_truncado,
    c.cliente_separado,
    c.banca,
    c.perfil_riesgo,
    c.nivel_riesgo,
    l.fecha_corte_local,
    l.total_cop,
    l.n_activos_local,
    round(l.pct_renta_variable, 4) AS pct_renta_variable,
    round(l.pct_renta_fija, 4)     AS pct_renta_fija,
    round(l.pct_fics, 4)           AS pct_fics,
    u.fecha_corte_usd,
    u.total_usd,
    u.n_instrumentos_usd
FROM core.dim_cliente c
LEFT JOIN local l USING (id_cliente)
LEFT JOIN usd u   USING (id_cliente);
