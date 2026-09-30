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
```

_Los siguientes pasos (carga, pipeline SQL, aplicación y modelo) se documentan a medida que se construyen._

## Hallazgos de calidad de datos

_Pendiente — se documentan con el pipeline SQL._

## Modelo analítico

_Pendiente._
