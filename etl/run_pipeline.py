"""Ejecuta el pipeline SQL (sql/*.sql) en orden alfabético.

Cada archivo corre en su propia transacción: si uno falla, se revierte y el
pipeline se detiene mostrando el error.

Uso:
    python etl/run_pipeline.py            # todos los scripts
    python etl/run_pipeline.py --hasta 21 # solo hasta el script con prefijo 21
"""

import argparse
import sys
import time
from pathlib import Path

from load_raw import PROJECT_ROOT, get_connection

SQL_DIR = PROJECT_ROOT / "sql"


def sql_files(hasta: str | None = None) -> list[Path]:
    files = sorted(SQL_DIR.glob("*.sql"))
    if hasta:
        files = [f for f in files if f.name[: len(hasta)] <= hasta]
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hasta", help="prefijo del último script a ejecutar (p. ej. 21)")
    args = parser.parse_args()

    files = sql_files(args.hasta)
    with get_connection() as conn:
        for path in files:
            start = time.perf_counter()
            try:
                with conn.transaction():
                    conn.execute(path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 - se reporta y se detiene
                print(f"  ERROR en {path.name}: {exc}", file=sys.stderr)
                return 1
            print(f"  {path.name:<40} {time.perf_counter() - start:6.2f} s")

    print(f"\nPipeline completado ({len(files)} scripts).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
