from django.contrib import admin

from .models import PropuestaIA


@admin.register(PropuestaIA)
class PropuestaIAAdmin(admin.ModelAdmin):
    list_display = ("id_cliente", "creada", "modelo", "tokens_entrada", "tokens_salida")
    search_fields = ("id_cliente",)
    readonly_fields = ("contenido", "contexto")
