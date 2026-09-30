from django.contrib import admin

from .models import ConsultaSQL, Ejecucion, EjecucionPaso


@admin.register(ConsultaSQL)
class ConsultaSQLAdmin(admin.ModelAdmin):
    list_display = ("archivo", "titulo", "capa", "activo", "actualizado")
    list_filter = ("capa", "activo")
    readonly_fields = ("contenido", "hash_contenido")


class EjecucionPasoInline(admin.TabularInline):
    model = EjecucionPaso
    extra = 0
    readonly_fields = ("nombre", "orden", "exitoso", "duracion_s", "detalle")


@admin.register(Ejecucion)
class EjecucionAdmin(admin.ModelAdmin):
    list_display = ("pk", "iniciada", "estado", "incluye_carga_csv", "origen")
    list_filter = ("estado",)
    inlines = [EjecucionPasoInline]
