-- =============================================================================
-- 90 · Bitácora de calidad de datos
-- -----------------------------------------------------------------------------
-- Resume cuántas filas afectó cada regla, a partir de la trazabilidad fila a
-- fila (correcciones y motivos de descarte). Permite auditar el pipeline y
-- responder "¿qué se hizo con los datos y por qué?".
-- =============================================================================

CREATE TABLE calidad.reglas (
    regla        TEXT PRIMARY KEY,
    accion       TEXT NOT NULL,
    descripcion  TEXT NOT NULL
);

INSERT INTO calidad.reglas (regla, accion, descripcion) VALUES
    -- Portafolio local
    ('id_en_notacion_cientifica',                   'informativo', 'ID de cliente en notación científica (dañado al abrir en Excel); se conserva como identificador y se marca como truncado'),
    ('cod_activo_10007_a_1007',                     'corregido',   'Código 10007 inexistente: es Fiducuenta (1007) con un cero de más; las series son complementarias día a día'),
    ('id_reconstruido_corrimiento_derecha',         'corregido',   'ID partido por corrimiento de columnas: el resto del ID llegó en la columna macroactivo'),
    ('id_reconstruido_corrimiento_izquierda',       'corregido',   'ID partido por corrimiento de columnas: el resto del ID llegó en ingestion_day'),
    ('columnas_reasignadas_por_dominio',            'corregido',   'Valores en columnas equivocadas reubicados según su dominio (banca, perfil, código, monto)'),
    ('mes_tomado_de_periodo',                       'corregido',   'Mes de ingestión vacío: se toma de la columna month del periodo'),
    ('dia_ingestion_perdido',                       'informativo', 'El día de ingestión se perdió por el corrimiento de columnas'),
    ('id_cliente_invalido',                         'informativo', 'ID de cliente vacío o inválido (p. ej. "100")'),
    ('id_no_reconocido',                            'informativo', 'ID con formato válido pero sin historia suficiente (menos de 50 filas)'),
    ('id_imputado_por_coincidencia_exacta',         'imputado',    'Cliente asignado porque otra fila idéntica (fecha, activo y saldo) pertenece a un único cliente'),
    ('id_imputado_por_hueco_en_serie',              'imputado',    'Cliente asignado porque es el único cuya serie de ese activo no tiene dato en esa fecha'),
    ('fecha_imputada_por_coincidencia_exacta',      'imputado',    'Fecha asignada porque el mismo saldo aparece en una única fecha del mes para esa serie'),
    ('fecha_imputada_por_hueco_en_serie',           'imputado',    'Fecha asignada porque es el único día del mes sin dato en esa serie'),
    ('cod_activo_imputado_por_coincidencia_exacta', 'imputado',    'Código de activo asignado porque el mismo saldo aparece ese día en un único activo del cliente'),
    ('cod_activo_imputado_por_hueco_en_serie',      'imputado',    'Código de activo asignado porque es la única serie del cliente sin dato ese día'),
    ('cod_activo_sintetico_serie_sin_codigo',       'imputado',    'Serie persistente sin código de activo: se crea un activo "sin código" por macroactivo'),
    ('macroactivo_desde_catalogo',                  'imputado',    'Macroactivo vacío: se toma del catálogo según el código de activo'),
    ('cliente_separado_por_perfil_distinto',        'corregido',   'Un ID truncado mezclaba dos clientes (perfiles de riesgo distintos en las mismas fechas): se separa en dos'),
    ('perfil_imputado_del_cliente',                 'imputado',    'Perfil de riesgo vacío: se toma el más frecuente del cliente'),
    ('perfil_sin_dato_a_sin_definir',               'imputado',    'Cliente sin ningún perfil informado: se asigna SIN DEFINIR (1466)'),
    ('banca_imputada_del_cliente',                  'imputado',    'Banca vacía: se toma la más frecuente del cliente'),
    ('aba_imputado_ultimo_valor_conocido',          'imputado',    'Saldo (ABA) vacío: se toma el último valor conocido de la serie'),
    ('duplicado',                                   'descartado',  'Registro repetido para el mismo cliente, fecha y activo'),
    ('duplicado_valor_atipico',                     'descartado',  'Registro repetido con saldo distinto: se conserva el más cercano a la mediana de la serie'),
    ('cliente_no_identificable',                    'descartado',  'No fue posible determinar el cliente'),
    ('fecha_no_determinable',                       'descartado',  'No fue posible determinar la fecha (el saldo coincide con varios días)'),
    ('activo_no_identificable',                     'descartado',  'No fue posible determinar el activo'),
    ('aba_sin_valor',                               'descartado',  'Saldo vacío sin valores de referencia en la serie'),
    -- Portafolio internacional
    ('columnas_corridas_derecha',                   'corregido',   'Valor extra insertado antes de la cantidad: columnas corridas a su posición'),
    ('columnas_corridas_izquierda',                 'corregido',   'Nombre del activo ausente: columnas corridas a su posición'),
    ('nombre_perdido',                              'imputado',    'Nombre del instrumento recuperado desde su ISIN en el catálogo de instrumentos'),
    ('corte_consolidado',                           'corregido',   'Varias valoraciones sin fecha en el mismo corte (2024-03-01): se consolidan en su promedio'),
    -- Catálogos
    ('catalogo_banca_duplicado',                    'corregido',   'Código de banca PR repetido en el catálogo'),
    ('catalogo_activo_codigo_1115_a_1015',          'corregido',   'PFCEMARGOS figura como 1115 pero en los datos aparece como 1015 (1115 nunca aparece)'),
    ('catalogo_activo_nombre_duplicado',            'informativo', 'CEMARGOS aparece con dos códigos (1003 y 1013)'),
    ('catalogo_activo_fuera_de_catalogo',           'informativo', 'Código presente en los datos pero ausente del catálogo (1022)');


INSERT INTO calidad.bitacora (tabla_origen, regla, accion, filas, descripcion)
SELECT x.tabla_origen, x.regla, r.accion, x.filas, r.descripcion
FROM (
    -- Correcciones del portafolio local
    SELECT 'historico_aba_macroactivos' AS tabla_origen, c AS regla, count(*) AS filas
    FROM core.aba_local_trazabilidad, unnest(correcciones) c
    GROUP BY c
    UNION ALL
    -- Descartes del portafolio local
    SELECT 'historico_aba_macroactivos', motivo_descarte, count(*)
    FROM core.aba_local_trazabilidad
    WHERE motivo_descarte IS NOT NULL
    GROUP BY motivo_descarte
    UNION ALL
    -- Correcciones y descartes del portafolio internacional
    SELECT 'historico_aba_usd_internacional', c, count(*)
    FROM staging.aba_usd_internacional, unnest(correcciones) c
    GROUP BY c
    UNION ALL
    SELECT 'historico_aba_usd_internacional', motivo_descarte, count(*)
    FROM staging.aba_usd_internacional
    WHERE motivo_descarte IS NOT NULL
    GROUP BY motivo_descarte
    UNION ALL
    SELECT 'historico_aba_usd_internacional', 'corte_consolidado', sum(n_observaciones - 1)::INT
    FROM core.aba_usd
    WHERE n_observaciones > 1
    UNION ALL
    -- Catálogos
    SELECT 'catalogo_banca', 'catalogo_banca_duplicado',
           (SELECT count(*) FROM raw.catalogo_banca) - (SELECT count(*) FROM staging.catalogo_banca)
    UNION ALL
    SELECT 'catalogo_activos', 'catalogo_activo_codigo_1115_a_1015', count(*)
    FROM core.dim_activo_local WHERE codigo_corregido
    UNION ALL
    SELECT 'catalogo_activos', 'catalogo_activo_nombre_duplicado', count(*)
    FROM core.dim_activo_local WHERE nombre_duplicado
    UNION ALL
    SELECT 'catalogo_activos', 'catalogo_activo_fuera_de_catalogo', count(*)
    FROM core.dim_activo_local WHERE fuera_de_catalogo AND cod_activo NOT LIKE 'SC-%'
) x
JOIN calidad.reglas r USING (regla)
WHERE x.filas > 0;


-- Resumen de volumen: filas de origen vs. filas finales
CREATE VIEW calidad.resumen_volumen AS
SELECT 'historico_aba_macroactivos' AS tabla_origen,
       (SELECT count(*) FROM raw.historico_aba_macroactivos)                              AS filas_origen,
       (SELECT count(*) FROM core.aba_local_trazabilidad WHERE motivo_descarte IS NULL)   AS filas_validas,
       (SELECT count(*) FROM core.aba_local_trazabilidad WHERE motivo_descarte IS NOT NULL) AS filas_descartadas,
       (SELECT count(*) FROM core.aba_local)                                              AS filas_modelo_final
UNION ALL
SELECT 'historico_aba_usd_internacional',
       (SELECT count(*) FROM raw.historico_aba_usd_internacional),
       (SELECT count(*) FROM staging.aba_usd_internacional WHERE motivo_descarte IS NULL),
       (SELECT count(*) FROM staging.aba_usd_internacional WHERE motivo_descarte IS NOT NULL),
       (SELECT count(*) FROM core.aba_usd);
