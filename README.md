# Analítica de Inversiones — Valores Bancolombia

Herramienta analítica para gerentes comerciales de inversión y equipos estructuradores:
consolida el portafolio **local (COP)** e **internacional (USD)** de cada cliente a partir
de fuentes heterogéneas, lo visualiza en una aplicación Django y lo enriquece con un
modelo de **segmentación de clientes y coherencia perfil de riesgo vs. portafolio**.

> Prueba técnica — Aprendiz VP Estructuración Mercado de Capitales 2027.

## Arquitectura

```
data/*.csv ──(Python: etl/)──► PostgreSQL
                                 ├─ raw      tablas 1:1 con cada CSV (todo TEXT)
                                 ├─ staging  limpieza y tipado        (sql/)
                                 ├─ core     dimensiones y hechos     (sql/)
                                 ├─ mart     portafolio por cliente   (sql/)
                                 └─ calidad  bitácora de reglas       (sql/)
                                        │
                        Django (gestión de queries + dashboard Plotly)
                                        │
                     analytics/ (datos de mercado + clustering)
```

## Estructura del repositorio

| Carpeta | Contenido |
|---|---|
| `etl/` | Carga automática de los CSV a PostgreSQL |
| `sql/` | Queries de limpieza, transformación y consolidación |
| `analytics/` | Datos de mercado y modelo analítico |
| `data/` | CSV suministrados — **no versionados** |

## Requisitos

- Docker y Docker Compose
- Python 3.12

## Cómo reproducir

```bash
# 1. Variables de entorno
cp .env.example .env

# 2. Base de datos
docker compose up -d

# 3. Entorno de Python
python -m venv .venv
source .venv/Scripts/activate      # Windows (Git Bash) | Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt

# 4. Copiar los CSV suministrados en ./data

# 5. Cargar los CSV a PostgreSQL (esquema raw)
python etl/load_raw.py

# 6. Ejecutar el pipeline SQL (staging → core → mart → calidad)
python etl/run_pipeline.py
```

_Los siguientes pasos (aplicación y modelo) se documentan a medida que se construyen._

## 1. Carga de datos (`etl/load_raw.py`)

Recorre automáticamente todos los `*.csv` de `DATA_DIR` y por cada uno crea la tabla
`raw.<nombre_archivo>` con las mismas columnas del archivo, poblándola vía `COPY`.

| Archivo | Tabla | Filas |
|---|---|---:|
| `cat_perfil_riesgo.csv` | `raw.cat_perfil_riesgo` | 4 |
| `catalogo_activos.csv` | `raw.catalogo_activos` | 20 |
| `catalogo_banca.csv` | `raw.catalogo_banca` | 6 |
| `historico_aba_macroactivos.csv` | `raw.historico_aba_macroactivos` | 6.694 |
| `historico_aba_usd_internacional.csv` | `raw.historico_aba_usd_internacional` | 5.750 |

Decisiones:

- **Todo como `TEXT`**: los datos traen IDs en notación científica, columnas corridas y
  `'None'` como texto; tiparlos en la carga rechazaría filas. La limpieza y el tipado
  ocurren en SQL, donde quedan documentados y auditables.
- **`_source_row`**: número de línea del archivo original, para trazar cada registro
  (y cada descarte) hasta su origen.
- **Idempotente**: cada ejecución recrea las tablas; una transacción por archivo.
- **Validación**: compara filas leídas vs. cargadas y rechaza archivos con filas de
  ancho distinto al encabezado.
- **Auditoría**: cada carga queda registrada en `raw.load_audit`.

## 2. Pipeline SQL (`sql/`)

Toda la limpieza, transformación y consolidación está en SQL. `etl/run_pipeline.py`
ejecuta los scripts en orden, cada uno en su propia transacción. El pipeline es
idempotente y tarda unos 2 segundos.

| Script | Capa | Qué hace |
|---|---|---|
| `00_esquemas_y_funciones.sql` | — | Esquemas y funciones de conversión segura (`limpiar`, `a_numero`, `fecha_segura`) |
| `10_staging_catalogos.sql` | staging | Catálogos limpios y deduplicados |
| `11_staging_aba_macroactivos.sql` | staging | Portafolio local: reparación de filas corridas y tipado (1 fila por fila de origen) |
| `12_staging_aba_usd_internacional.sql` | staging | Portafolio USD: reparación de filas corridas, fechas, liquidez |
| `20_core_dimensiones.sql` | core | Perfiles, bancas y activos, con las correcciones del catálogo |
| `21_core_aba_local.sql` | core | Imputaciones entre filas, deduplicación, hecho diario `core.aba_local` |
| `22_core_aba_usd.sql` | core | Instrumentos clasificados y hecho por corte `core.aba_usd` |
| `23_core_dim_cliente.sql` | core | Clientes de ambos portafolios, con recuperación de IDs truncados |
| `30_mart_portafolios.sql` | mart | Portafolios en la última fecha, evolución y ficha resumen por cliente |
| `90_calidad_datos.sql` | calidad | Bitácora de cuántas filas afectó cada regla |

Vistas principales para la aplicación:
`mart.portafolio_local_actual`, `mart.portafolio_usd_actual`, `mart.resumen_cliente`,
`mart.evolucion_local`, `mart.evolucion_usd`.

**Trazabilidad:** `core.aba_local_trazabilidad` guarda cada fila de origen (`_source_row`)
con las correcciones aplicadas y, si se descartó, el motivo.

## Hallazgos de calidad de datos

Resultado: de **6.694** filas locales quedan **5.896** válidas. 797 son duplicados y
**solo 1 fila se pierde** por información irrecuperable. De **5.750** filas USD se descarta 1.
El detalle está en `calidad.bitacora` y `calidad.resumen_volumen`.

### Portafolio local (`historico_aba_macroactivos`)

| Hallazgo | Evidencia | Tratamiento |
|---|---|---|
| **IDs en notación científica** (2.018 filas, 11 IDs) | `1.00114E+12`: se perdió precisión al pasar por Excel. Ninguno coincide con un ID completo de otra tabla | Se conservan como identificador y se marcan `id_truncado`; no se inventan dígitos |
| **Un ID truncado mezclaba dos clientes** | `1.00114E+12` tenía ECOPETROL con perfil *Sin definir* y Fiducuenta con perfil *Moderado* en las mismas fechas | Se separa en `1.00114E+12-1` y `-2` |
| **ID completo recuperado** | Una fila corrida traía `100890112256`; la imputación la asignó a `1.0089E+11`, y ese número redondeado da exactamente `1.0089E+11` | Se registra como `id_cliente_completo` |
| **Filas con columnas corridas** (11 filas, 4 variantes) | `PN` en la columna de perfil, `1466` en banca, parte del ID en `macroactivo` o en `ingestion_day` | **Reasignación por dominio**: cada valor va a la columna cuyo dominio cumple; los IDs partidos se reconstruyen |
| **Código 10007 inexistente** (1.338 filas) | Cada cliente tiene 111 días con 10007 y justo 1 día con 1007 (Fiducuenta), en la fecha faltante | 10007 → 1007 |
| **Duplicados** (797 filas) | Series completas repetidas (224 filas en 112 días); 1 caso con saldo 9,8 veces mayor | Se conserva el valor más cercano a la mediana de la serie |
| **Campos faltantes** (cliente, fecha, código, perfil, banca, saldo) | `None`, vacíos o `100` como ID | Imputación por **coincidencia exacta** (la fila es un duplicado dañado) o por **hueco único** en una serie; perfil y banca, de la moda del cliente; saldo, del último valor conocido |
| **Series sin código de activo** (227 filas) | Dos clientes tienen una serie diaria completa sin código | Activo sintético "FICs / Renta Variable sin código" |
| **Mes de ingestión vacío** (4 filas) | La columna `month` del periodo sí lo trae | Se toma del periodo |

El resultado son **30 clientes** y **56 series** activo-cliente **completas**, con una foto diaria de
112 días hábiles (23-nov-2023 a 15-may-2024).

### Portafolio internacional (`historico_aba_usd_internacional`)

| Hallazgo | Evidencia | Tratamiento |
|---|---|---|
| **El corte 2024-03-01 no es una foto** | ~63 valoraciones por cliente e instrumento, con cantidad constante, sin fecha y desordenadas | Se consolidan en una fila (promedio) y se guardan mínimo, máximo y desviación como medida de volatilidad |
| **Filas corridas** (3) | Nombre del activo dentro de `cantidad`; fecha dentro de `valor_mercado`; ID inválido | Se recorren las columnas; el nombre se recupera por ISIN; la fila sin ID se descarta |
| **Liquidez** | `isin = 'Liquidez'` para efectivo (`CASH`) y money market (`MONEYMRKT`) | El instrumento se identifica por CUSIP |
| **Vencimiento centinela** | `1/01/1900` = sin vencimiento; formato `MM/DD/YYYY` | → `NULL`; se usa para clasificar bonos |

Los instrumentos se clasifican en **Renta Fija** (bonos), **Renta Variable** (acciones/ETF),
**Fondos** (UCITS), **Estructurados** (notas CH) y **Liquidez**.

### Catálogos

- `catalogo_banca`: `PR,Privada` duplicado.
- `catalogo_activos`: PFCEMARGOS figura como `1115`, pero en los datos aparece como `1015`
  (error de digitación). CEMARGOS aparece con dos códigos (`1003` y `1013`). El código `1022`
  aparece en los datos pero no en el catálogo.

## Modelo analítico

_Pendiente._
