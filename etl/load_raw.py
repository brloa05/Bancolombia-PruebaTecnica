"""Carga automática de los CSV suministrados a PostgreSQL.

Por cada archivo ``<nombre>.csv`` en DATA_DIR crea la tabla ``raw.<nombre>`` con
las mismas columnas del archivo y la puebla vía COPY.

Decisiones de diseño:
- Todas las columnas se cargan como TEXT: los datos vienen sucios (IDs en
  notación científica, columnas corridas, 'None' como texto) y un tipado
  estricto en la carga rechazaría filas. La limpieza y el tipado se hacen en
  SQL (capa staging), como pide la prueba.
- Se agrega ``_source_row`` (número de fila en el archivo) para trazabilidad:
  permite identificar filas duplicadas y auditar qué se descarta en la limpieza.
- La carga es idempotente: cada ejecución recrea las tablas.
- Cada carga queda registrada en ``raw.load_audit``.

Uso:
    python etl/load_raw.py
"""

import csv
import os
import re
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import sql

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA = "raw"


def get_connection() -> psycopg.Connection:
    load_dotenv(PROJECT_ROOT / ".env")
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5440"),
        dbname=os.getenv("POSTGRES_DB", "analitica_inversiones"),
        user=os.getenv("POSTGRES_USER", "analitica"),
        password=os.getenv("POSTGRES_PASSWORD", "analitica"),
        autocommit=True,
    )


def normalize_identifier(name: str) -> str:
    """Convierte un nombre de archivo o columna en un identificador SQL válido."""
    ident = re.sub(r"[^0-9a-zA-Z_]+", "_", name.strip().lower()).strip("_")
    if not ident or ident[0].isdigit():
        ident = f"c_{ident}"
    return ident


def read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = [normalize_identifier(col) for col in next(reader)]
        rows = list(reader)

    ragged = [i for i, row in enumerate(rows, start=2) if len(row) != len(header)]
    if ragged:
        raise ValueError(
            f"{path.name}: {len(ragged)} filas con número de columnas distinto "
            f"al encabezado (primeras líneas: {ragged[:5]})"
        )
    return header, rows


def load_file(conn: psycopg.Connection, path: Path) -> int:
    table = normalize_identifier(path.stem)
    header, rows = read_csv(path)
    target = sql.Identifier(SCHEMA, table)

    conn.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(target))
    conn.execute(
        sql.SQL("CREATE TABLE {} (_source_row INTEGER PRIMARY KEY, {})").format(
            target,
            sql.SQL(", ").join(
                sql.SQL("{} TEXT").format(sql.Identifier(col)) for col in header
            ),
        )
    )

    columns = sql.SQL(", ").join(map(sql.Identifier, ["_source_row", *header]))
    with conn.cursor().copy(
        sql.SQL("COPY {} ({}) FROM STDIN").format(target, columns)
    ) as copy:
        # _source_row = línea en el archivo (la 1 es el encabezado)
        for line_number, row in enumerate(rows, start=2):
            # Celdas vacías → NULL; el texto 'None' se conserva y se trata en staging
            copy.write_row([line_number, *(v if v != "" else None for v in row)])

    loaded = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(target)).fetchone()[0]
    if loaded != len(rows):
        raise RuntimeError(f"{path.name}: se leyeron {len(rows)} filas pero se cargaron {loaded}")

    conn.execute(
        sql.SQL(
            "INSERT INTO {}.load_audit (source_file, target_table, rows_loaded, columns) "
            "VALUES (%s, %s, %s, %s)"
        ).format(sql.Identifier(SCHEMA)),
        (path.name, f"{SCHEMA}.{table}", loaded, header),
    )
    return loaded


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    data_dir = (PROJECT_ROOT / os.getenv("DATA_DIR", "data")).resolve()
    files = sorted(data_dir.glob("*.csv"))
    if not files:
        print(f"No se encontraron archivos .csv en {data_dir}", file=sys.stderr)
        return 1

    with get_connection() as conn:
        conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(SCHEMA)))
        conn.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.load_audit (
                    id           SERIAL PRIMARY KEY,
                    source_file  TEXT NOT NULL,
                    target_table TEXT NOT NULL,
                    rows_loaded  INTEGER NOT NULL,
                    columns      TEXT[] NOT NULL,
                    loaded_at    TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            ).format(sql.Identifier(SCHEMA))
        )

        print(f"Cargando {len(files)} archivos desde {data_dir}\n")
        for path in files:
            # Una transacción por archivo: si uno falla, los demás quedan cargados
            with conn.transaction():
                loaded = load_file(conn, path)
            print(f"  {path.name:<40} -> {SCHEMA}.{normalize_identifier(path.stem):<35} {loaded:>6} filas")

    print("\nCarga completada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
