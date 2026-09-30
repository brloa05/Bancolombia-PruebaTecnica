"""Datos de mercado: precios diarios de cierre de los activos de los portafolios.

Fuente: Yahoo Finance (yfinance). Para que el proyecto sea reproducible sin
internet, la descarga se guarda como snapshot versionado en
analytics/datos/precios_mercado.csv y desde ahí se carga a PostgreSQL
(esquema `mercado`, que el pipeline SQL no borra).

Universo:
- Acciones de la BVC (.CL): las de los portafolios y el resto de las líquidas,
  que se usan para identificar activos sin catálogo por correlación.
- Acciones y ETF de EE. UU. que tienen los clientes en su portafolio USD.
- ETF de referencia (proxies) para fondos UCITS, bonos y notas estructuradas.
- TRM (USDCOP=X) para consolidar el portafolio en pesos.

Uso:
    python -m analytics.mercado              # carga el snapshot a PostgreSQL
    python -m analytics.mercado --descargar  # vuelve a descargar de Yahoo y actualiza el snapshot
"""

import argparse
import io
import sys
from pathlib import Path

import pandas as pd
from psycopg import sql

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from etl.load_raw import get_connection  # noqa: E402

SNAPSHOT = PROJECT_ROOT / "analytics" / "datos" / "precios_mercado.csv"
INICIO, FIN = "2023-05-01", "2024-06-01"  # 12 meses antes del último corte + margen

BVC = [
    # En los portafolios
    "ECOPETROL", "ISA", "CELSIA", "ETB", "GRUBOLIVAR", "PFCORFICOL", "PFGRUPSURA",
    "PFCEMARGOS", "CEMARGOS", "TERPEL", "ICOLCAP",
    # Resto de acciones líquidas (candidatas para identificar activos sin catálogo)
    "GRUPOSURA", "GRUPOARGOS", "PFGRUPOARG", "NUTRESA", "BOGOTA", "PFAVAL", "CNEC",
    "MINEROS", "PROMIGAS", "BHI", "CORFICOLCF", "PFDAVVNDA", "EXITO", "HCOLSEL",
    "CONCONCRET", "GEB", "BVC",
]
ACCIONES_USD = [
    "AAPL", "ABNB", "AMD", "AMZN", "ARKK", "BRK-B", "C", "CHPT", "DIS", "EEM", "GOOG", "IYK",
    "LYG", "META", "MRVL", "NFLX", "PBR", "PLTR", "PYPL", "QS", "SOFI", "SOXL", "XYZ", "VGK",
    "VGSH", "XLB", "XLF",
]
PROXIES = [
    "CIB",                                   # ADR de Bancolombia (proxy de PFBCOLOM, sin datos locales)
    "SPY", "ACWI", "AAXJ", "XLV", "XLK", "IGF",           # renta variable
    "AGG", "EMB", "IEI", "IEF",                            # renta fija
    "AOK", "AOM", "AOR", "AOA",                            # multiactivo (conservador → agresivo)
]
TRM = "USDCOP=X"


def tickers() -> list[str]:
    return [t + ".CL" for t in BVC] + ACCIONES_USD + PROXIES + [TRM]


def descargar() -> pd.DataFrame:
    import yfinance as yf

    datos = yf.download(tickers(), start=INICIO, end=FIN, progress=False, auto_adjust=False)

    def a_largo(tabla: pd.DataFrame, nombre: str) -> pd.DataFrame:
        return tabla.rename_axis("fecha").reset_index().melt(id_vars="fecha", var_name="ticker", value_name=nombre)

    # cierre: ajustado por dividendos (retorno total, para el riesgo)
    # cierre_sin_ajuste: precio de pantalla (el que usa la valoración de los saldos)
    largo = a_largo(datos["Adj Close"], "cierre").merge(
        a_largo(datos["Close"], "cierre_sin_ajuste"), on=["fecha", "ticker"]
    ).dropna()
    largo["fecha"] = pd.to_datetime(largo["fecha"]).dt.date
    faltantes = sorted(set(tickers()) - set(largo["ticker"]))
    if faltantes:
        print(f"Sin datos en Yahoo: {', '.join(faltantes)}")
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    largo.sort_values(["ticker", "fecha"]).to_csv(SNAPSHOT, index=False, float_format="%.6f")
    return largo


def cargar() -> int:
    """Carga el snapshot a mercado.precios. Devuelve el número de filas cargadas."""
    if not SNAPSHOT.exists():
        raise FileNotFoundError(f"No existe {SNAPSHOT}. Ejecuta con --descargar.")
    with get_connection() as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS mercado")
        conn.execute("DROP TABLE IF EXISTS mercado.precios")
        conn.execute("""
            CREATE TABLE mercado.precios (
                fecha  DATE    NOT NULL,
                ticker TEXT    NOT NULL,
                cierre NUMERIC NOT NULL,             -- ajustado por dividendos (retorno total)
                cierre_sin_ajuste NUMERIC NOT NULL,  -- precio de cierre publicado
                PRIMARY KEY (ticker, fecha)
            )
        """)
        with conn.cursor().copy(
            sql.SQL("COPY mercado.precios (fecha, ticker, cierre, cierre_sin_ajuste) FROM STDIN WITH (FORMAT csv, HEADER true)")
        ) as copy:
            copy.write(io.BytesIO(SNAPSHOT.read_bytes()).read())
        return conn.execute("SELECT count(*) FROM mercado.precios").fetchone()[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Datos de mercado")
    parser.add_argument("--descargar", action="store_true", help="descarga de Yahoo y actualiza el snapshot")
    args = parser.parse_args()

    if args.descargar:
        largo = descargar()
        print(f"Snapshot actualizado: {largo['ticker'].nunique()} tickers, {len(largo):,} filas → {SNAPSHOT.name}")
    filas = cargar()
    print(f"mercado.precios: {filas:,} filas cargadas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
