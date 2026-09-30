from django.db import models


class PropuestaIA(models.Model):
    """Resumen y propuesta comercial redactados por IA para un cliente.

    Se guarda junto con el contexto enviado al modelo, para auditar qué datos
    usó y no repetir el costo de generarla.
    """

    id_cliente = models.CharField(max_length=40, db_index=True)
    creada = models.DateTimeField(auto_now_add=True)
    modelo = models.CharField(max_length=60)
    contenido = models.JSONField()
    contexto = models.JSONField(help_text="Datos agregados enviados al modelo (sin identificadores)")
    tokens_entrada = models.PositiveIntegerField()
    tokens_salida = models.PositiveIntegerField()

    class Meta:
        ordering = ["-creada"]
        verbose_name = "propuesta IA"
        verbose_name_plural = "propuestas IA"

    def __str__(self) -> str:
        return f"{self.id_cliente} · {self.creada:%Y-%m-%d %H:%M}"
