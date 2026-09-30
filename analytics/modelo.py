"""Modelo analítico: riesgo del portafolio, perfil implícito, segmentación y
recomendaciones por cliente.

Enfoque (explicable, pensado para el gerente comercial):

1. Riesgo de mercado del portafolio consolidado en COP
   - Cada posición se representa con la serie de precios de su factor de riesgo
     (mart.mapa_factores). Los activos en USD se convierten a COP con la TRM,
     así que el riesgo cambiario queda incluido.
   - Volatilidad anual = sqrt(w' Σ w), con Σ = covarianza de 12 meses de
     retornos diarios. Los FICs y CDT sin precio de mercado entran como
     factores independientes con su volatilidad observada.
   - VaR paramétrico 95 % a 1 día y contribución al riesgo por categoría.
2. Perfil de riesgo implícito por bandas de volatilidad y comparación con el
   perfil declarado → coherente / por encima / por debajo / sin definir.
3. Segmentación K-Means (k elegido por silhouette) sobre composición,
   concentración, internacionalización, riesgo y tamaño.
4. Recomendaciones (siguiente mejor acción) con reglas trazables.
5. Validación con mercado: los saldos de las acciones locales replican los
   precios de la BVC con un día de rezago (el saldo del día D es el cierre de D-1).

Salida: esquema `analitica` (tablas riesgo_cliente, segmentos,
recomendaciones, validacion_mercado, metadatos).

Uso:
    python -m analytics.modelo
"""

import io
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from etl.load_raw import get_connection  # noqa: E402

INICIO_VENTANA, FIN_VENTANA = pd.Timestamp("2023-05-31"), pd.Timestamp("2024-05-31")
DIAS_ANIO = 252
Z_95 = 1.645
FECHA_CORTE_USD = date(2024, 5, 30)

# Bandas de volatilidad anual para el perfil implícito (supuesto documentado):
# conservador ≈ FICs vista, CDT y renta fija corta; moderado ≈ portafolio
# balanceado (hasta ~40 % en renta variable); agresivo ≈ mayoritariamente acciones.
BANDAS = [(0.04, "CONSERVADOR", 1), (0.10, "MODERADO", 2), (np.inf, "AGRESIVO", 3)]
NIVEL_A_PERFIL = {nivel: perfil for _, perfil, nivel in BANDAS}
LIMITE_SUPERIOR = {nivel: tope for tope, _, nivel in BANDAS}

CATEGORIAS = ["Renta Variable", "Renta Fija", "Multiactivo", "Estructurados", "Liquidez"]


# ---------------------------------------------------------------------------
# Lectura y escritura
# ---------------------------------------------------------------------------
def leer(conn, consulta: str) -> pd.DataFrame:
    cur = conn.execute(consulta)
    return pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])


def escribir(conn, df: pd.DataFrame, tabla: str) -> None:
    tipos = {"i": "BIGINT", "f": "DOUBLE PRECISION", "b": "BOOLEAN", "M": "DATE"}
    columnas = ", ".join(f'"{c}" {tipos.get(df[c].dtype.kind, "TEXT")}' for c in df.columns)
    conn.execute(f"DROP TABLE IF EXISTS analitica.{tabla}")
    conn.execute(f"CREATE TABLE analitica.{tabla} ({columnas})")
    buffer = io.StringIO()
    df.to_csv(buffer, index=False, header=False, na_rep="\\N", date_format="%Y-%m-%d")
    with conn.cursor().copy(f"COPY analitica.{tabla} FROM STDIN WITH (FORMAT csv, NULL '\\N')") as copy:
        copy.write(buffer.getvalue())


# ---------------------------------------------------------------------------
# 1. Riesgo de mercado
# ---------------------------------------------------------------------------
def retornos(precios: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Retornos diarios de cada ticker en la ventana de 12 meses: en COP (incluye
    la TRM) y en su moneda original (sin riesgo cambiario)."""
    p = precios.pivot(index="fecha", columns="ticker", values="cierre").astype(float)
    p.index = pd.to_datetime(p.index)
    p = p.sort_index().ffill()  # festivos distintos en Colombia y EE. UU.
    r = p.pct_change(fill_method=None).loc[INICIO_VENTANA:FIN_VENTANA].iloc[1:]
    fx = r["USDCOP=X"]
    cop = {}
    for t in r.columns:
        if t == "USDCOP=X":
            continue
        cop[t] = r[t] if t.endswith(".CL") else (1 + r[t]) * (1 + fx) - 1
    cop["USD"] = fx  # efectivo y money market en USD: solo riesgo cambiario
    original = r.drop(columns="USDCOP=X").assign(USD=0.0)
    return pd.DataFrame(cop).fillna(0.0), original.fillna(0.0)


def _sigma(g: pd.DataFrame, ret: pd.DataFrame, es_serie, vol_obs, mediana_cat) -> np.ndarray:
    """Matriz de covarianza anual a nivel de posición."""
    tick = g.loc[~es_serie, "ticker_referencia"].tolist()
    sigma = np.zeros((len(g), len(g)))
    idx_m = np.where(~es_serie)[0]
    if tick:
        sigma[np.ix_(idx_m, idx_m)] = ret[tick].cov().to_numpy() * DIAS_ANIO
    # FICs y CDT: factores independientes con su volatilidad observada
    for i in np.where(es_serie)[0]:
        v = vol_obs.get(g.at[i, "id_instrumento"], np.nan)
        sigma[i, i] = (mediana_cat.get(g.at[i, "categoria_riesgo"], 0.02) if pd.isna(v) else v) ** 2
    return sigma


def riesgo_clientes(pos: pd.DataFrame, ret_cop: pd.DataFrame, ret_orig: pd.DataFrame,
                    vol_series: pd.DataFrame) -> pd.DataFrame:
    """Volatilidad (en COP y sin TRM), VaR, rendimiento 12 m y contribución al riesgo."""
    vol_obs = vol_series.set_index("cod_activo")["volatilidad_anual"].astype(float)
    rend_obs = vol_series.set_index("cod_activo")["rendimiento_anual"].astype(float)
    # Serie sin historia suficiente: mediana de su categoría
    mediana_cat = {}
    for cat in pos["categoria_riesgo"].unique():
        cods = pos.loc[(pos.categoria_riesgo == cat) & pos.ticker_referencia.str.startswith("SERIE:"), "id_instrumento"]
        mediana_cat[cat] = vol_obs.reindex(cods).dropna().median()

    rend_12m = (1 + ret_cop).prod() - 1
    filas = []
    for cliente, g in pos.groupby("id_cliente"):
        g = g.reset_index(drop=True)
        w = g["peso"].astype(float).to_numpy()
        es_serie = g["ticker_referencia"].str.startswith("SERIE:").to_numpy()
        sigma_cop = _sigma(g, ret_cop, es_serie, vol_obs, mediana_cat)
        sigma_orig = _sigma(g, ret_orig, es_serie, vol_obs, mediana_cat)

        var_cop, var_orig = float(w @ sigma_cop @ w), float(w @ sigma_orig @ w)
        vol_cop, vol_orig = np.sqrt(max(var_cop, 0.0)), np.sqrt(max(var_orig, 0.0))
        # La contribución por categoría se mide sin TRM: describe el riesgo de los activos elegidos
        contrib = w * (sigma_orig @ w) / var_orig if var_orig > 0 else np.zeros_like(w)
        rend = np.array([
            rend_obs.get(g.at[i, "id_instrumento"], 0.0) if es_serie[i] else rend_12m[g.at[i, "ticker_referencia"]]
            for i in range(len(g))
        ])
        aum = float(g["valor_cop"].astype(float).sum())
        fila = {
            "id_cliente": cliente,
            "volatilidad_anual": vol_orig,
            "volatilidad_cop": vol_cop,
            "pct_riesgo_cambiario": float(np.clip((var_cop - var_orig) / var_cop, 0, 1)) if var_cop > 0 else 0.0,
            "var_95_1d_cop": Z_95 * vol_cop / np.sqrt(DIAS_ANIO) * aum,
            "rendimiento_12m": float(w @ rend),
        }
        for cat in CATEGORIAS:
            clave = "riesgo_" + cat.lower().replace(" ", "_")
            fila[clave] = float(contrib[g["categoria_riesgo"].to_numpy() == cat].sum())
        filas.append(fila)
    return pd.DataFrame(filas)


# ---------------------------------------------------------------------------
# 2. Perfil implícito y coherencia
# ---------------------------------------------------------------------------
def perfil_implicito(vol: float) -> tuple[str, int]:
    for tope, perfil, nivel in BANDAS:
        if vol <= tope:
            return perfil, nivel
    return BANDAS[-1][1], BANDAS[-1][2]


def coherencia(nivel_declarado, nivel_implicito: int) -> str:
    if pd.isna(nivel_declarado):
        return "Perfil sin definir"
    if nivel_implicito > nivel_declarado:
        return "Riesgo por encima del perfil"
    if nivel_implicito < nivel_declarado:
        return "Riesgo por debajo del perfil"
    return "Coherente"


# ---------------------------------------------------------------------------
# 3. Segmentación
# ---------------------------------------------------------------------------
VARIABLES_SEGMENTO = ["volatilidad_anual", "pct_renta_variable_total", "pct_renta_fija",
                      "pct_liquidez", "pct_internacional", "hhi", "log_aum"]


def segmentar(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    X = StandardScaler().fit_transform(df[VARIABLES_SEGMENTO])
    candidatos = {}
    for k in range(3, 7):
        km = KMeans(n_clusters=k, n_init=50, random_state=42).fit(X)
        candidatos[k] = (silhouette_score(X, km.labels_), km)
    # Mayor silhouette; ante un empate práctico (< 0,005) se prefiere más segmentos
    mejor = max(v[0] for v in candidatos.values())
    k = max(kk for kk, v in candidatos.items() if v[0] >= mejor - 0.005)
    sil, km = candidatos[k]

    df = df.copy()
    df["cluster"] = km.labels_

    centroides = df.groupby("cluster").agg(
        clientes=("id_cliente", "count"),
        aum_cop=("aum_cop", "sum"),
        aum_mediano_cop=("aum_cop", "median"),
        **{v: (v, "mean") for v in VARIABLES_SEGMENTO if v != "log_aum"},
    ).reset_index()
    centroides["segmento"] = centroides.apply(_nombre_segmento, axis=1)
    # Nombres únicos: si dos segmentos coinciden, se distinguen por tamaño
    for nombre, grupo in centroides.groupby("segmento"):
        if len(grupo) > 1:
            orden = grupo.sort_values("aum_mediano_cop", ascending=False).index
            for i, etiqueta in zip(orden, ["patrimonio alto", "patrimonio medio", "patrimonio bajo"]):
                centroides.at[i, "segmento"] = f"{nombre} · {etiqueta}"
    df = df.merge(centroides[["cluster", "segmento"]], on="cluster")
    metadatos = {"k": int(k), "silhouette": round(float(sil), 3),
                 "silhouette_por_k": {int(kk): round(float(v[0]), 3) for kk, v in candidatos.items()}}
    return df, centroides.drop(columns="cluster"), metadatos


def _nombre_segmento(c: pd.Series) -> str:
    if c.pct_internacional >= 0.5:
        if c.pct_renta_variable_total >= 0.5:
            return "Inversionista global en renta variable"
        if c.pct_renta_fija >= 0.5:
            return "Inversionista global en renta fija"
        return "Inversionista global diversificado"
    if c.pct_liquidez >= 0.6:
        return "Ahorrador en FICs vista"
    if c.pct_renta_variable_total >= 0.6:
        return "Accionista local"
    return "Inversionista local mixto"


# ---------------------------------------------------------------------------
# 4. Recomendaciones (siguiente mejor acción)
# ---------------------------------------------------------------------------
def recomendar(df: pd.DataFrame, pos: pd.DataFrame, vencimientos: pd.DataFrame) -> pd.DataFrame:
    recs = []
    aum_mediano = df["aum_cop"].median()

    def agregar(c, tipo, prioridad, titulo, detalle, monto=None):
        recs.append({"id_cliente": c.id_cliente, "tipo": tipo, "prioridad": prioridad,
                     "titulo": titulo, "detalle": detalle, "monto_cop": monto})

    def pct(valor: float, decimales: int = 0) -> str:
        return f"{valor:.{decimales}%}".replace(".", ",").replace("%", " %")

    for c in df.itertuples():
        vol_txt = pct(c.volatilidad_anual, 1)
        n_pos = int(c.n_posiciones)
        riesgo_principal = max(
            CATEGORIAS, key=lambda cat: getattr(c, "riesgo_" + cat.lower().replace(" ", "_")))
        # El monto de perfilamiento y adecuación es el portafolio completo: es lo que está en juego
        if c.coherencia == "Perfil sin definir":
            agregar(c, "Perfilamiento", 1, "Realizar perfilamiento de riesgo",
                    f"El cliente no tiene perfil de riesgo definido; debe perfilarse antes de recomendarle "
                    f"productos. Su portafolio actual (volatilidad {vol_txt}) corresponde a un perfil "
                    f"{c.perfil_implicito.lower()}.",
                    round(c.aum_cop, 0))
        elif c.coherencia == "Riesgo por encima del perfil":
            agregar(c, "Adecuación", 1, "Revisar adecuación del portafolio",
                    f"La volatilidad del portafolio ({vol_txt}) supera el rango de un perfil "
                    f"{c.perfil_riesgo.lower()} (hasta {pct(LIMITE_SUPERIOR[c.nivel_riesgo])}). "
                    f"El mayor aporte al riesgo viene de {riesgo_principal.lower()}. "
                    f"Proponer rebalanceo o actualizar el perfil.",
                    round(c.aum_cop, 0))
        elif c.coherencia == "Riesgo por debajo del perfil":
            producto = ("fondos multiactivo o renta variable diversificada" if c.nivel_riesgo == 2
                        else "renta variable global diversificada")
            agregar(c, "Oportunidad", 2, "Portafolio más conservador que su perfil",
                    f"Perfil {c.perfil_riesgo.lower()} con un portafolio de riesgo "
                    f"{c.perfil_implicito.lower()} (volatilidad {vol_txt}). Hay espacio para proponer "
                    f"{producto}. Liquidez disponible: {pct(c.pct_liquidez)} del portafolio.",
                    round(c.aum_cop * c.pct_liquidez, 0) or None)
        if (c.coherencia != "Riesgo por debajo del perfil"  # ese caso ya incluye la liquidez disponible
                and c.pct_liquidez >= 0.5 and c.aum_cop >= aum_mediano):
            # La mayor parte del portafolio en FICs vista / money market
            agregar(c, "Liquidez", 2, "Excedente de liquidez",
                    f"El {pct(c.pct_liquidez)} del portafolio está en FICs vista o money market. "
                    f"Proponer alternativas acordes a su perfil con mejor rentabilidad esperada "
                    f"(CDT, renta fija corta o fondos de crédito).",
                    round(c.aum_cop * c.pct_liquidez, 0))

        # Concentración en un solo activo de riesgo (una posición en liquidez no es un riesgo de concentración)
        concentrado = c.peso_max_posicion >= 0.5 and (n_pos > 1 or c.aum_cop >= aum_mediano)
        if concentrado and c.categoria_mayor_posicion != "Liquidez":
            agregar(c, "Diversificación", 2, "Portafolio concentrado",
                    f"{c.mayor_posicion} concentra el {pct(c.peso_max_posicion)} del portafolio "
                    f"({n_pos} posici{'ones' if n_pos != 1 else 'ón'}). Proponer diversificación.",
                    round(c.aum_cop * c.peso_max_posicion, 0))

        if c.pct_internacional == 0 and c.aum_cop >= aum_mediano:
            agregar(c, "Internacional", 3, "Sin portafolio internacional",
                    "El cliente solo tiene inversiones locales. Por su tamaño puede diversificar con "
                    "fondos globales, que además reducen el riesgo concentrado en Colombia.")

    for v in vencimientos.itertuples():
        agregar(v, "Reinversión", 1, f"Vencimiento el {v.fecha_vencimiento:%Y-%m-%d}",
                f"Vence {v.nombre} por US$ {v.valor_mercado_usd:,.0f}. Preparar una propuesta de "
                f"reinversión (p. ej. una nueva nota estructurada) antes del vencimiento.",
                round(float(v.valor_cop), 0))

    return (pd.DataFrame(recs)
            .sort_values(["prioridad", "monto_cop"], ascending=[True, False], na_position="last")
            .reset_index(drop=True))


# ---------------------------------------------------------------------------
# 5. Validación con precios de mercado
# ---------------------------------------------------------------------------
def validar_con_mercado(aba: pd.DataFrame, precios: pd.DataFrame, mapa: pd.DataFrame) -> pd.DataFrame:
    """Correlación entre el saldo diario de cada acción local y el precio de la BVC (rezago 1 día)."""
    # Precio sin ajustar: es el que usa la valoración de los saldos
    p = (precios[precios.ticker.str.endswith(".CL")]
         .pivot(index="fecha", columns="ticker", values="cierre_sin_ajuste").astype(float))
    p.index = pd.to_datetime(p.index)
    r_lag = p.sort_index().pct_change(fill_method=None).shift(1)  # saldo del día D = cierre de D-1
    r_lag = r_lag.loc[:, r_lag.std() > 0]
    filas = []
    for cod, g in aba.groupby("cod_activo"):
        # Serie de un solo cliente (la más larga): mezclar clientes rompería los retornos
        cliente = g["id_cliente"].value_counts().idxmax()
        serie = g[g.id_cliente == cliente].set_index("fecha")["aba"].astype(float).sort_index()
        serie.index = pd.to_datetime(serie.index)
        j = r_lag.join(serie.pct_change().rename("saldo"), how="inner").dropna(subset=["saldo"])
        if j["saldo"].std() == 0:
            continue
        with np.errstate(invalid="ignore", divide="ignore"):
            corr = j.drop(columns="saldo").corrwith(j["saldo"]).dropna().sort_values(ascending=False)
        if corr.empty:
            continue
        asignado = mapa.get(cod)
        filas.append({
            "cod_activo": cod,
            "activo": g["activo"].iloc[0],
            "ticker_asignado": asignado,
            "correlacion_asignado": float(corr.get(asignado, np.nan)) if asignado else np.nan,
            "ticker_mejor": corr.index[0],
            "correlacion_mejor": float(corr.iloc[0]),
            "correlacion_sin_rezago": _corr_sin_rezago(p, serie, corr.index[0]),
            "dias": int(len(j)),
        })
    return pd.DataFrame(filas).sort_values("cod_activo")


def _corr_sin_rezago(p: pd.DataFrame, serie: pd.Series, ticker: str) -> float:
    j = p.sort_index().pct_change(fill_method=None)[[ticker]].join(serie.pct_change().rename("s"), how="inner").dropna()
    return float(j.corr().iloc[0, 1])


# ---------------------------------------------------------------------------
def main() -> int:
    with get_connection() as conn:
        precios = leer(conn, "SELECT fecha, ticker, cierre, cierre_sin_ajuste FROM mercado.precios")
        pos = leer(conn, "SELECT * FROM mart.posiciones_actuales")
        feats = leer(conn, "SELECT * FROM mart.features_cliente")
        vol_series = leer(conn, "SELECT * FROM mart.volatilidad_series_locales")
        clientes = leer(conn, "SELECT id_cliente, perfil_riesgo, nivel_riesgo, banca FROM core.dim_cliente")
        aba_rv = leer(conn, """
            SELECT a.id_cliente, a.cod_activo, d.activo, a.fecha, a.aba
            FROM core.aba_local a JOIN core.dim_activo_local d USING (cod_activo)
            WHERE a.macroactivo = 'Renta Variable'
        """)
        mapa_local = dict(conn.execute(
            "SELECT id_instrumento, ticker_referencia FROM mart.mapa_factores "
            "WHERE origen = 'Local' AND ticker_referencia LIKE '%.CL'").fetchall())
        vencimientos = leer(conn, f"""
            SELECT p.id_cliente, p.nombre, u.fecha_vencimiento, u.valor_mercado_usd, p.valor_cop
            FROM mart.posiciones_actuales p
            JOIN mart.portafolio_usd_actual u USING (id_cliente, id_instrumento)
            WHERE u.fecha_vencimiento BETWEEN DATE '{FECHA_CORTE_USD}' AND DATE '{FECHA_CORTE_USD + timedelta(days=90)}'
        """)

        numericas = [c for c in feats.columns if c not in ("id_cliente", "mayor_posicion", "categoria_mayor_posicion")]
        feats[numericas] = feats[numericas].astype(float)
        pos[["peso", "valor_cop"]] = pos[["peso", "valor_cop"]].astype(float)
        vencimientos[["valor_mercado_usd", "valor_cop"]] = vencimientos[["valor_mercado_usd", "valor_cop"]].astype(float)

        # 1. Riesgo
        ret_cop, ret_orig = retornos(precios)
        riesgo = riesgo_clientes(pos, ret_cop, ret_orig, vol_series)
        df = feats.merge(riesgo, on="id_cliente").merge(clientes, on="id_cliente", how="left")
        df["pct_renta_variable_total"] = df["pct_renta_variable"] + df["pct_estructurados"]
        df["log_aum"] = np.log10(df["aum_cop"].clip(lower=1))

        # 2. Perfil implícito y coherencia
        implicito = df["volatilidad_anual"].apply(perfil_implicito)
        df["perfil_implicito"] = [p for p, _ in implicito]
        df["nivel_implicito"] = [n for _, n in implicito]
        df["coherencia"] = [coherencia(d, i) for d, i in zip(df["nivel_riesgo"], df["nivel_implicito"])]

        # 3. Segmentación
        df, segmentos, meta_seg = segmentar(df)

        # 4. Recomendaciones
        recs = recomendar(df, pos, vencimientos)

        # 5. Validación con mercado
        validacion = validar_con_mercado(aba_rv, precios, mapa_local)

        # Escritura
        conn.execute("CREATE SCHEMA IF NOT EXISTS analitica")
        salida = df.drop(columns=["cluster", "log_aum"])
        salida["nivel_riesgo"] = salida["nivel_riesgo"].astype("Int64")
        escribir(conn, salida, "riesgo_cliente")
        escribir(conn, segmentos, "segmentos")
        escribir(conn, recs, "recomendaciones")
        escribir(conn, validacion, "validacion_mercado")
        metadatos = {
            "ventana": [str(INICIO_VENTANA.date()), str(FIN_VENTANA.date())],
            "bandas_volatilidad": {p: (None if np.isinf(t) else t) for t, p, _ in BANDAS},
            "segmentacion": meta_seg,
            "variables_segmentacion": VARIABLES_SEGMENTO,
        }
        escribir(conn, pd.DataFrame([{"clave": "modelo", "valor": json.dumps(metadatos, ensure_ascii=False)}]), "metadatos")

    print(f"Clientes: {len(df)} · segmentos: {meta_seg['k']} (silhouette {meta_seg['silhouette']}) · "
          f"recomendaciones: {len(recs)}")
    print(df["coherencia"].value_counts().to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
