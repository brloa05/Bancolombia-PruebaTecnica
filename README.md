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
                                 └─ mart     portafolio por cliente   (sql/)
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
```

_Los siguientes pasos (pipeline SQL, aplicación y modelo) se documentan a medida que se construyen._

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

## Hallazgos de calidad de datos

_Pendiente — se documentan con el pipeline SQL._

## Modelo analítico

_Pendiente._
