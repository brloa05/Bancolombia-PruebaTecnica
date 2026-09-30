-- =============================================================================
-- 23 · Dimensión de clientes (core)
-- -----------------------------------------------------------------------------
-- Une los clientes de ambos portafolios. Para los IDs truncados (notación
-- científica) intenta recuperar el ID completo: si alguna fila dañada traía un
-- ID completo que la imputación asignó a ese cliente, y ese ID redondeado da
-- exactamente el valor en notación científica, es el ID original.
-- =============================================================================

CREATE TABLE core.dim_cliente AS
WITH local AS (
    SELECT
        id_cliente,
        mode() WITHIN GROUP (ORDER BY cod_perfil_riesgo) AS cod_perfil_riesgo,
        mode() WITHIN GROUP (ORDER BY cod_banca)         AS cod_banca,
        max(fecha)                                       AS fecha_ultimo_corte_local
    FROM core.aba_local
    GROUP BY id_cliente
),
usd AS (
    SELECT id_cliente, max(fecha_corte) AS fecha_ultimo_corte_usd
    FROM core.aba_usd
    GROUP BY id_cliente
),
recuperado AS (
    SELECT
        t.id_cliente,
        min(t.id_cliente_staging) AS id_completo
    FROM core.aba_local_trazabilidad t
    CROSS JOIN LATERAL (
        SELECT split_part(t.id_cliente, '-', 1)                                 AS sci,
               split_part(split_part(t.id_cliente, '-', 1), 'E+', 2)::INT       AS exponente,
               length(split_part(split_part(t.id_cliente, 'E', 1), '.', 2))     AS decimales
    ) n
    WHERE t.id_cliente ~ 'E\+'
      AND t.id_cliente_staging ~ '^\d+$'
      AND abs(t.id_cliente_staging::NUMERIC - n.sci::NUMERIC) <= 0.5 * power(10, n.exponente - n.decimales)
    GROUP BY t.id_cliente
)
SELECT
    i.id_cliente,
    coalesce(r.id_completo, CASE WHEN i.id_cliente !~ 'E\+' THEN i.id_cliente END) AS id_cliente_completo,
    i.id_cliente ~ 'E\+'                           AS id_truncado,
    i.id_cliente ~ 'E\+\d+-\d+$'                   AS cliente_separado,
    l.cod_banca,
    b.banca,
    l.cod_perfil_riesgo,
    p.perfil_riesgo,
    p.nivel_riesgo,
    l.id_cliente IS NOT NULL                       AS tiene_portafolio_local,
    u.id_cliente IS NOT NULL                       AS tiene_portafolio_usd,
    l.fecha_ultimo_corte_local,
    u.fecha_ultimo_corte_usd
FROM (SELECT id_cliente FROM local UNION SELECT id_cliente FROM usd) i
LEFT JOIN local l                  USING (id_cliente)
LEFT JOIN usd u                    USING (id_cliente)
LEFT JOIN recuperado r             USING (id_cliente)
LEFT JOIN core.dim_banca b         ON b.cod_banca = l.cod_banca
LEFT JOIN core.dim_perfil_riesgo p ON p.cod_perfil_riesgo = l.cod_perfil_riesgo;

ALTER TABLE core.dim_cliente ADD PRIMARY KEY (id_cliente);
