# Analítica de Inversiones — Valores Bancolombia

Herramienta analítica para gerentes comerciales de inversión y equipos estructuradores:
consolida el portafolio **local (COP)** e **internacional (USD)** de cada cliente a partir
de fuentes heterogéneas, lo visualiza en una aplicación Django y lo enriquece con un
modelo de **segmentación de clientes y coherencia perfil de riesgo vs. portafolio**.

> Prueba técnica — Aprendiz VP Estructuración Mercado de Capitales 2027.

## Arquitectura

```
data/*.csv ─────(etl/load_raw.py)──────► PostgreSQL
Yahoo Finance ─(analytics/mercado.py)──►   ├─ raw       tablas 1:1 con cada CSV (todo TEXT)
  (snapshot versionado)                    ├─ mercado   precios diarios y TRM
                                           ├─ staging   limpieza y tipado             (sql/1x)
                                           ├─ core      dimensiones y hechos          (sql/2x)
                                           ├─ mart      portafolios y riesgo          (sql/3x-4x)
                                           ├─ calidad   bitácora de reglas            (sql/9x)
                                           └─ analitica riesgo, perfil, segmentos y
                                                        recomendaciones   (analytics/modelo.py)
                                                  │
                             Django: gestión de queries + dashboard Plotly (webapp/)
```

## Estructura del repositorio

| Carpeta | Contenido |
|---|---|
| `etl/` | Carga automática de los CSV a PostgreSQL |
| `sql/` | Queries de limpieza, transformación y consolidación |
| `webapp/` | Aplicación Django: gestión de queries y dashboard de portafolios |
| `analytics/` | Datos de mercado (con snapshot en `analytics/datos/`) y modelo analítico |
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

# 5. Aplicación web y flujo completo
cd webapp
python manage.py migrate
python manage.py ejecutar_pipeline --carga-csv   # CSV → mercado → SQL → modelo (~8 s)
python manage.py runserver                       # http://127.0.0.1:8000
```

El paso `ejecutar_pipeline` también se puede lanzar desde la aplicación (página *Consultas SQL*).
Cada paso se puede correr por separado desde la raíz del repositorio:

```bash
python etl/load_raw.py            # CSV → esquema raw
python -m analytics.mercado       # snapshot de precios → esquema mercado (--descargar lo actualiza desde Yahoo)
python etl/run_pipeline.py        # sql/*.sql
python -m analytics.modelo        # modelo → esquema analitica
```

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
| `40_mart_riesgo.sql` | mart | Mapeo de cada instrumento a su referencia de mercado, posiciones consolidadas en COP (con TRM) y variables por cliente para el modelo |
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
| **Saltos de un solo día** (9 filas) | Los CDT de un cliente valen **exactamente el doble** tres días aislados (posición sumada dos veces); dos fondos tienen un día con la coma decimal corrida (×0,1 y ×0,01). Los días vecinos coinciden entre sí | Si el salto es un factor exacto se corrige la escala; si no, se toma el valor del día anterior. El valor original queda en `aba_origen`. Los cambios de nivel que persisten (p. ej. un fondo cerrado que vence y se traslada a Fiducuenta) son movimientos reales y no se tocan |
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

## 3. Aplicación Django (`webapp/`)

La aplicación solo **lee** las vistas `mart`: toda la transformación ocurre en SQL.

| Página | Qué muestra |
|---|---|
| **Portafolio por cliente** (`/`) | Selector de cliente; portafolio local (COP) e internacional (USD) en la **última fecha disponible** de cada uno; composición por macroactivo; evolución diaria local y por corte USD; aviso de ID truncado |
| **Cartera** (`/cartera/`) | Totales de todos los clientes, distribución por banca y perfil de riesgo, tabla de clientes |
| **Modelo de riesgo** (`/modelo/`) | Riesgo vs. perfil declarado, segmentos, riesgo cambiario, siguiente mejor acción y validación con precios de mercado |
| **Calidad de datos** (`/calidad/`) | Bitácora de reglas del pipeline y cuántas filas afectó cada una |
| **Consultas SQL** (`/consultas/`) | **Gestión de los queries**: scripts por capa con su descripción, SQL resaltado, vista previa de cada tabla o vista que crean, ejecución del pipeline e historial de ejecuciones con duración y errores por paso |
| **Explorar datos** (`/consultas/explorar/`) | Consola SQL de solo lectura con consultas de ejemplo |
| **Asistente con IA** (en la ficha del cliente) | Borrador de resumen y propuesta comercial redactado por Claude (ver sección 5) |

Decisiones:

- **Los archivos de `sql/` son la fuente de verdad.** La app los sincroniza en el modelo
  `ConsultaSQL` y registra cada corrida en `Ejecucion`/`EjecucionPaso`. Así no hay dos
  versiones de un mismo query.
- **Consola segura:** una sola sentencia `SELECT`/`WITH`, dentro de una transacción `READ ONLY`,
  con límite de 5 s y 500 filas.
- **Visualización (Plotly):** las figuras se construyen en el servidor (`portafolios/graficas.py`)
  y `plotly.js` se sirve desde el paquete de Python, así que el dashboard funciona sin internet.
  El color sigue a la clase de activo en todas las gráficas, con una paleta validada para
  daltonismo en modo claro y oscuro. Cada gráfica tiene su tabla equivalente.

## 4. Modelo analítico (`analytics/`)

**Pregunta de negocio:** ¿el riesgo real del portafolio de cada cliente corresponde a su perfil de
riesgo declarado, y qué acción comercial conviene en cada caso?

### Datos de mercado

`analytics/mercado.py` descarga de Yahoo Finance 12 meses de precios diarios (jun-2023 a may-2024)
de 71 tickers y los guarda como snapshot versionado (`analytics/datos/precios_mercado.csv`), para que
el modelo se pueda reproducir sin internet:

- Las acciones de la BVC que tienen los clientes y el resto de las líquidas.
- Las 27 acciones y ETF de EE. UU. de los portafolios USD.
- ETF de referencia para fondos UCITS, bonos y notas estructuradas (ACWI, AGG, EMB, AOK–AOA, etc.).
- La TRM (`USDCOP=X`).

Se guardan el precio ajustado (retorno total, para el riesgo) y el precio publicado (para validar los saldos).

### Hallazgos al cruzar los datos con el mercado

| Hallazgo | Evidencia |
|---|---|
| **La fecha del archivo es la de ingestión (T+1).** El saldo del día D refleja el cierre de D-1 | La correlación entre la variación diaria del saldo y la del precio es ≤ 0,21 sin rezago (salvo CEMARGOS, que solo tiene 4 días) y sube a **0,49–0,89** con un día de rezago (8 de 9 acciones ≥ 0,70: ECOPETROL 0,78, ISA 0,78, PFCEMARGOS 0,89); la cantidad implícita da números enteros y estables (500 ECOPETROL, 1.000 PFCEMARGOS, 3.000 CELSIA) |
| **Se confirma la corrección del catálogo 1115 → 1015** | El código 1015 replica a PFCEMARGOS (r = 0,89) |
| **"Renta Variable sin código" es TERPEL** | Replica a TERPEL (r = 0,64) con 555 acciones constantes |
| **El activo 1022 no se pudo identificar** | No replica ninguna acción de la BVC disponible; se usa el índice COLCAP como proxy |
| **El portafolio internacional es el 87 % de la cartera** | Consolidado en COP con la TRM del corte: 39.200 M COP en total |
| **Entre el 24 % y el 94 % del riesgo de los portafolios internacionales, medido en pesos, viene de la TRM** | Promedio del 63 % en los clientes con más del 50 % internacional |

### Metodología

1. **Referencia de mercado por instrumento** (`sql/40_mart_riesgo.sql`): el precio propio de cada acción o
   ETF; un activo identificado por correlación (TERPEL); un ETF proxy para fondos, bonos y notas; o, para
   FICs y CDT, la volatilidad observada de su propio saldo, excluyendo los días con aportes o retiros.
2. **Riesgo**: volatilidad anual √(w'Σw) con la matriz de covarianzas de 12 meses. Se calcula dos veces:
   - **En moneda original**: describe los activos elegidos y es la que se usa para el perfil.
   - **En pesos, con la TRM**: se usa para el **VaR paramétrico 95 % a 1 día**.
3. **Perfil implícito** por bandas de volatilidad (conservador ≤ 4 %, moderado ≤ 10 %, agresivo > 10 %) y
   **coherencia** frente al perfil declarado.
4. **Segmentación K-Means** sobre volatilidad, % renta variable, % renta fija, % liquidez, % internacional,
   concentración (HHI) y tamaño (log). k se elige por silhouette (k = 3, silhouette 0,56).
5. **Siguiente mejor acción** con reglas trazables: perfilamiento, adecuación, oportunidad por riesgo bajo,
   excedente de liquidez, concentración, internacionalización y vencimientos en los próximos 90 días.

### Resultados

| | |
|---|---|
| **Perfil sin definir** | **15 de 30 clientes**, con **25.500 M COP (65 % de la cartera)**. Por regulación deben perfilarse antes de recibir recomendaciones: es la acción más urgente |
| **Riesgo por encima del perfil** | 3 clientes "moderados" con portafolios agresivos (acciones locales o renta variable global) |
| **Riesgo por debajo del perfil** | 7 clientes con perfil moderado o agresivo que solo tienen FICs vista: oportunidad comercial |
| **Coherentes** | 5 clientes |
| **Segmentos** | *Inversionista global diversificado* (12 clientes, 36.200 M COP), *Ahorrador en FICs vista* (11, 3.100 M COP) y *Accionista local* (7, volatilidad 32 %) |
| **Recomendaciones** | 36 acciones, entre ellas la reinversión de una nota estructurada de UBS que vence el 6-jun-2024 (US$ 114.690) |

**Limitaciones:** con 30 clientes el modelo es descriptivo, no predictivo; las bandas de volatilidad son un
supuesto razonable, no la metodología oficial de perfilamiento; los proxies (ETF) aproximan fondos y bonos
sin serie propia; los FICs y CDT se tratan como independientes del resto del portafolio.

## 5. Asistente comercial con IA (opcional)

En la ficha de cada cliente, un botón pide a **Claude (Opus 5.5, API de Anthropic)** que redacte un
borrador para el gerente: **resumen del cliente, puntos clave, propuesta de siguiente paso, guion para la
llamada y alertas**. Parte de lo que ya calculó el pipeline (riesgo, perfil declarado vs. implícito,
segmento, recomendaciones y principales posiciones).

**Cómo potencia la herramienta:** el modelo analítico dice *qué* hacer con cada cliente; el asistente lo
convierte en una conversación lista para preparar. Un gerente con decenas de clientes pasa de leer tablas a
tener, en segundos, el argumento y las preguntas para la llamada, siempre coherentes con el perfil de riesgo.

Decisiones de diseño (`webapp/portafolios/ia.py`):

- **Privacidad:** a la API **no se envía el ID del cliente ni datos personales**, solo cifras agregadas
  del portafolio y nombres de instrumentos de mercado. La ficha muestra exactamente qué se envió.
- **Sin cifras inventadas:** el modelo solo redacta con los datos entregados; todos los números vienen
  del pipeline.
- **Adecuación incorporada:** si el cliente no tiene perfil de riesgo, la propuesta no puede recomendar
  productos y debe empezar por el perfilamiento; si el riesgo supera el perfil, prioriza la revisión.
- **Salida estructurada** (JSON Schema) para mostrarla por secciones; `fallbacks: "default"` por si el
  modelo declina; manejo explícito de rechazos y respuestas truncadas.
- **Costo controlado:** cada propuesta se guarda (`PropuestaIA`) con los tokens usados; solo se regenera
  a pedido. Se presenta siempre como borrador que el gerente debe validar.

Para activarlo, agrega `ANTHROPIC_API_KEY=...` al archivo `.env`. Sin la key, el resto de la aplicación
funciona igual y el botón indica cómo activarlo. Las pruebas (`python manage.py test portafolios`)
simulan la API: verifican la petición, que el ID no se envíe y el manejo de rechazos, sin costo.
