-- =============================================================================
-- 10 · Staging de catálogos
-- -----------------------------------------------------------------------------
-- Limpieza básica de los tres catálogos. Las correcciones de negocio
-- (códigos errados, faltantes) se aplican en core (20_core_dimensiones.sql).
-- =============================================================================

CREATE TABLE staging.cat_perfil_riesgo AS
SELECT DISTINCT
    staging.a_numero(cod_perfil_riesgo)::INT AS cod_perfil_riesgo,
    upper(staging.limpiar(perfil_riesgo))     AS perfil_riesgo
FROM raw.cat_perfil_riesgo
WHERE staging.a_numero(cod_perfil_riesgo) IS NOT NULL;

-- El archivo trae 'PR,Privada' duplicado: DISTINCT lo elimina
CREATE TABLE staging.catalogo_banca AS
SELECT DISTINCT
    upper(staging.limpiar(cod_banca)) AS cod_banca,
    staging.limpiar(banca)            AS banca
FROM raw.catalogo_banca
WHERE staging.limpiar(cod_banca) IS NOT NULL;

CREATE TABLE staging.catalogo_activos AS
SELECT
    staging.limpiar(cod_activo) AS cod_activo,
    staging.limpiar(activo)     AS activo,
    _source_row
FROM raw.catalogo_activos
WHERE staging.limpiar(cod_activo) IS NOT NULL;
