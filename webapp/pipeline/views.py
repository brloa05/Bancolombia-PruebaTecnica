from django.contrib import messages
from django.db import DatabaseError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import services
from .models import ConsultaSQL, Ejecucion

EJEMPLOS = [
    ("Portafolio local de un cliente",
     "SELECT activo, macroactivo, saldo_cop, participacion\n"
     "FROM mart.portafolio_local_actual\nWHERE id_cliente = '10020203023'\nORDER BY saldo_cop DESC"),
    ("Clientes por perfil y banca",
     "SELECT perfil_riesgo, banca, count(*) AS clientes, sum(total_cop) AS total_cop\n"
     "FROM mart.resumen_cliente\nGROUP BY 1, 2\nORDER BY total_cop DESC"),
    ("Filas descartadas y su motivo",
     "SELECT motivo_descarte, count(*)\nFROM core.aba_local_trazabilidad\n"
     "WHERE motivo_descarte IS NOT NULL\nGROUP BY 1"),
    ("Instrumentos USD más tenidos",
     "SELECT i.nombre, i.clase_activo, count(DISTINCT a.id_cliente) AS clientes\n"
     "FROM core.aba_usd a JOIN core.dim_instrumento_usd i USING (id_instrumento)\n"
     "GROUP BY 1, 2\nORDER BY clientes DESC\nLIMIT 10"),
]


def lista(request):
    consultas = services.sincronizar_consultas()
    for consulta in consultas:
        consulta.objetos = services.objetos_creados(consulta.contenido)
    return render(request, "pipeline/lista.html", {
        "consultas": consultas,
        "ejecuciones": Ejecucion.objects.prefetch_related("pasos")[:8],
    })


def detalle(request, pk):
    consulta = get_object_or_404(ConsultaSQL, pk=pk)
    objetos = services.objetos_creados(consulta.contenido)
    objeto = request.GET.get("objeto") or (objetos[0]["nombre"] if objetos else None)
    previa, error = None, None
    if objeto:
        try:
            previa = services.vista_previa(objeto)
        except (ValueError, DatabaseError) as exc:
            error = str(exc)
    return render(request, "pipeline/detalle.html", {
        "consulta": consulta,
        "objetos": objetos,
        "objeto": objeto,
        "previa": previa,
        "error": error,
    })


@require_POST
def ejecutar(request):
    ejecucion = services.ejecutar_pipeline(incluir_carga_csv=bool(request.POST.get("carga_csv")))
    if ejecucion.estado == Ejecucion.Estado.EXITOSA:
        messages.success(request, f"Pipeline ejecutado en {ejecucion.duracion_s:.1f} s.")
    else:
        messages.error(request, "El pipeline falló. Revisa el detalle del paso con error.")
    return redirect("pipeline:ejecucion", pk=ejecucion.pk)


def ejecucion(request, pk):
    ejecucion = get_object_or_404(Ejecucion.objects.prefetch_related("pasos"), pk=pk)
    return render(request, "pipeline/ejecucion.html", {"ejecucion": ejecucion})


def explorar(request):
    texto = request.POST.get("sql") or request.GET.get("sql") or EJEMPLOS[0][1]
    resultado, error = None, None
    if request.method == "POST":
        try:
            resultado = services.consulta_lectura(texto)
        except (ValueError, DatabaseError) as exc:
            error = str(exc).strip()
    return render(request, "pipeline/explorar.html", {
        "texto": texto,
        "resultado": resultado,
        "error": error,
        "ejemplos": EJEMPLOS,
    })
