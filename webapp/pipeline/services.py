"""Lógica de gestión del pipeline SQL: sincronización, ejecución y consulta."""

import contextlib
import hashlib
import io
import re
import sys
import time
from dataclasses import dataclass

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from .models import ConsultaSQL, Ejecucion, EjecucionPaso

# Los paquetes etl y analytics viven en la raíz del repositorio
if str(settings.PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(settings.PROJECT_ROOT))

from analytics import mercado, modelo  # noqa: E402
from etl.load_raw import load_all  # noqa: E402

CAPA_POR_PREFIJO = {
    "0": ConsultaSQL.Capa.CONFIGURACION,
    "1": ConsultaSQL.Capa.STAGING,
    "2": ConsultaSQL.Capa.CORE,
    "3": ConsultaSQL.Capa.MART,
    "4": ConsultaSQL.Capa.MART,
    "9": ConsultaSQL.Capa.CALIDAD,
}

RE_TITULO = re.compile(r"^--\s*\d+\s*·\s*(.+)$", re.MULTILINE)
RE_OBJETO = re.compile(r"CREATE\s+(TABLE|VIEW)\s+([a-z_]+\.[a-z_]+)", re.IGNORECASE)
RE_IDENTIFICADOR = re.compile(r"^[a-z_]+\.[a-z_]+$")


def _encabezado(contenido: str) -> tuple[str, str]:
    """Extrae título y descripción del bloque de comentarios inicial del script."""
    titulo = RE_TITULO.search(contenido)
    lineas = []
    for linea in contenido.splitlines()[3:]:
        if not linea.startswith("--") or linea.startswith("-- ====="):
            break
        texto = linea[2:].strip()
        if not set(texto) <= {"-"}:
            lineas.append(linea[3:] if linea.startswith("-- ") else texto)
    return (titulo.group(1).strip() if titulo else ""), "\n".join(lineas).strip()


def sincronizar_consultas() -> list[ConsultaSQL]:
    """Registra en la base los scripts de sql/ y marca inactivos los que ya no existen."""
    vistos = set()
    for path in sorted(settings.SQL_DIR.glob("*.sql")):
        contenido = path.read_text(encoding="utf-8")
        titulo, descripcion = _encabezado(contenido)
        ConsultaSQL.objects.update_or_create(
            archivo=path.name,
            defaults={
                "titulo": titulo or path.stem,
                "capa": CAPA_POR_PREFIJO.get(path.name[0], ConsultaSQL.Capa.CORE),
                "descripcion": descripcion,
                "contenido": contenido,
                "hash_contenido": hashlib.sha256(contenido.encode()).hexdigest(),
                "activo": True,
            },
        )
        vistos.add(path.name)
    ConsultaSQL.objects.exclude(archivo__in=vistos).update(activo=False)
    return list(ConsultaSQL.objects.filter(activo=True))


def objetos_creados(contenido: str) -> list[dict]:
    """Tablas y vistas que crea un script (para ver su resultado)."""
    objetos = {}
    for tipo, nombre in RE_OBJETO.findall(contenido):
        objetos.setdefault(nombre.lower(), tipo.upper())
    return [{"nombre": n, "tipo": t} for n, t in objetos.items()]


def ejecutar_pipeline(incluir_carga_csv: bool = False, origen: str = "web") -> Ejecucion:
    """Ejecuta el flujo completo, registrando cada paso:

    1. Carga de CSV (opcional)          etl/load_raw.py
    2. Precios de mercado (snapshot)    analytics/mercado.py
    3. Scripts SQL en orden             sql/*.sql, cada uno en su propia transacción
    4. Modelo analítico                 analytics/modelo.py

    Se detiene en el primer error y lo registra.
    """
    consultas = sincronizar_consultas()
    ejecucion = Ejecucion.objects.create(incluye_carga_csv=incluir_carga_csv, origen=origen)
    pasos = []
    if incluir_carga_csv:
        pasos.append(("Carga de CSV (etl/load_raw.py)", None, _cargar_csv))
    pasos.append(("Precios de mercado (analytics/mercado.py)", None,
                  lambda: f"mercado.precios: {mercado.cargar():,} filas"))
    for consulta in consultas:
        pasos.append((consulta.archivo, consulta, _ejecutor_sql(consulta)))
    pasos.append(("Modelo analítico (analytics/modelo.py)", None, _ejecutar_modelo))

    for orden, (nombre, consulta, funcion) in enumerate(pasos):
        inicio = time.perf_counter()
        try:
            detalle, exitoso = funcion() or "", True
        except Exception as exc:  # noqa: BLE001 - se registra en la ejecución
            detalle, exitoso = str(exc), False
        EjecucionPaso.objects.create(
            ejecucion=ejecucion, consulta=consulta, nombre=nombre, orden=orden,
            exitoso=exitoso, duracion_s=time.perf_counter() - inicio, detalle=detalle,
        )
        if not exitoso:
            return _finalizar(ejecucion, Ejecucion.Estado.FALLIDA)

    return _finalizar(ejecucion, Ejecucion.Estado.EXITOSA)


def _cargar_csv() -> str:
    cargados = load_all(settings.DATA_DIR)
    return "\n".join(f"{archivo} → {tabla}: {filas:,} filas" for archivo, tabla, filas in cargados)


def _ejecutor_sql(consulta: ConsultaSQL):
    def ejecutar():
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(consulta.contenido)
    return ejecutar


def _ejecutar_modelo() -> str:
    salida = io.StringIO()
    with contextlib.redirect_stdout(salida):
        modelo.main()
    return salida.getvalue().strip()


def _finalizar(ejecucion: Ejecucion, estado: str) -> Ejecucion:
    ejecucion.estado = estado
    ejecucion.finalizada = timezone.now()
    ejecucion.save(update_fields=["estado", "finalizada"])
    return ejecucion


@dataclass
class Resultado:
    columnas: list[str]
    filas: list[tuple]
    total: int | None = None
    truncado: bool = False
    duracion_ms: float = 0.0


def vista_previa(objeto: str, limite: int = 50) -> Resultado:
    """Primeras filas y conteo de una tabla o vista del pipeline."""
    if not RE_IDENTIFICADOR.match(objeto):
        raise ValueError("Nombre de objeto inválido")
    esquema, nombre = objeto.split(".")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
            [esquema, nombre],
        )
        if cursor.fetchone() is None:
            raise ValueError(f"{objeto} no existe. ¿Ya se ejecutó el pipeline?")
        # El identificador ya fue validado contra el catálogo de la base
        cursor.execute(f'SELECT count(*) FROM "{esquema}"."{nombre}"')
        total = cursor.fetchone()[0]
        cursor.execute(f'SELECT * FROM "{esquema}"."{nombre}" LIMIT %s', [limite])
        columnas = [c.name for c in cursor.description]
        return Resultado(columnas, cursor.fetchall(), total, total > limite)


RE_SOLO_LECTURA = re.compile(r"^\s*(SELECT|WITH|TABLE|VALUES)\b", re.IGNORECASE)


def consulta_lectura(texto: str, limite: int = 500) -> Resultado:
    """Ejecuta una consulta de solo lectura para explorar los datos.

    Protecciones: una sola sentencia que empiece por SELECT/WITH, transacción
    READ ONLY (Postgres rechaza cualquier escritura) y tiempo máximo de 5 s.
    """
    texto = texto.strip().rstrip(";").strip()
    if not RE_SOLO_LECTURA.match(texto):
        raise ValueError("Solo se permiten consultas de lectura (SELECT o WITH).")
    if ";" in texto:
        raise ValueError("Escribe una sola sentencia.")

    inicio = time.perf_counter()
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute("SET LOCAL statement_timeout = '5s'")
        cursor.execute(texto)
        columnas = [c.name for c in cursor.description] if cursor.description else []
        filas = cursor.fetchmany(limite + 1) if columnas else []
        # Deshace cualquier efecto (no debería haberlo: la transacción es READ ONLY)
        transaction.set_rollback(True)
    truncado = len(filas) > limite
    return Resultado(columnas, filas[:limite], None, truncado, (time.perf_counter() - inicio) * 1000)
