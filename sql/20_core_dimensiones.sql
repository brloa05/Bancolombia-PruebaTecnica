-- =============================================================================
-- 20 · Dimensiones de catálogo (core)
-- -----------------------------------------------------------------------------
-- Correcciones de negocio sobre los catálogos, con la evidencia en los datos:
--   · PFCEMARGOS figura como 1115, pero en los datos nunca aparece 1115 y sí
--     aparece 1015 (Renta Variable, sin nombre en catálogo, precio en el rango
--     de la preferencial de Cementos Argos). 1115 es un error de digitación.
--   · CEMARGOS aparece con dos códigos (1003 y 1013): se conservan ambos
--     apuntando al mismo nombre y se marca el duplicado.
--   · 1022 aparece en los datos (Renta Variable) pero no en el catálogo.
--   · El macroactivo de cada código se toma de los datos (valor más frecuente).
-- =============================================================================

CREATE TABLE core.dim_perfil_riesgo AS
SELECT
    cod_perfil_riesgo,
    perfil_riesgo,
    CASE perfil_riesgo
        WHEN 'CONSERVADOR' THEN 1
        WHEN 'MODERADO'    THEN 2
        WHEN 'AGRESIVO'    THEN 3
    END AS nivel_riesgo              -- NULL para SIN DEFINIR
FROM staging.cat_perfil_riesgo;

ALTER TABLE core.dim_perfil_riesgo ADD PRIMARY KEY (cod_perfil_riesgo);


CREATE TABLE core.dim_banca AS
SELECT cod_banca, banca FROM staging.catalogo_banca;

ALTER TABLE core.dim_banca ADD PRIMARY KEY (cod_banca);


CREATE TABLE core.dim_activo_local AS
WITH catalogo AS (
    SELECT
        CASE WHEN cod_activo = '1115' THEN '1015' ELSE cod_activo END AS cod_activo,
        activo,
        cod_activo = '1115' AS codigo_corregido
    FROM staging.catalogo_activos
),
en_datos AS (
    SELECT
        cod_activo,
        mode() WITHIN GROUP (ORDER BY macroactivo) AS macroactivo,
        count(*)                                   AS filas
    FROM staging.aba_macroactivos
    WHERE cod_activo IS NOT NULL AND macroactivo IS NOT NULL
    GROUP BY cod_activo
)
SELECT
    coalesce(c.cod_activo, d.cod_activo)                         AS cod_activo,
    coalesce(c.activo, 'Activo ' || d.cod_activo || ' (sin catálogo)') AS activo,
    d.macroactivo,
    c.cod_activo IS NULL                                         AS fuera_de_catalogo,
    coalesce(c.codigo_corregido, FALSE)                          AS codigo_corregido,
    count(*) OVER (PARTITION BY coalesce(c.activo, d.cod_activo)) > 1 AS nombre_duplicado,
    coalesce(d.filas, 0)                                         AS filas_en_datos
FROM catalogo c
FULL JOIN en_datos d USING (cod_activo);

ALTER TABLE core.dim_activo_local ADD PRIMARY KEY (cod_activo);
