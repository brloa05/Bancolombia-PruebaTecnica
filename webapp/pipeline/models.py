from django.db import models


class ConsultaSQL(models.Model):
    """Un script del pipeline (sql/*.sql). El archivo en el repositorio es la
    fuente de verdad; aquí se registra su metadata y se sincroniza al listar."""

    class Capa(models.TextChoices):
        CONFIGURACION = "configuracion", "Configuración"
        STAGING = "staging", "Staging"
        CORE = "core", "Core"
        MART = "mart", "Mart"
        CALIDAD = "calidad", "Calidad"

    archivo = models.CharField(max_length=200, unique=True)
    titulo = models.CharField(max_length=200)
    capa = models.CharField(max_length=20, choices=Capa.choices)
    descripcion = models.TextField(blank=True)
    contenido = models.TextField()
    hash_contenido = models.CharField(max_length=64)
    activo = models.BooleanField(default=True, help_text="Falso si el archivo ya no existe")
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["archivo"]
        verbose_name = "consulta SQL"
        verbose_name_plural = "consultas SQL"

    def __str__(self) -> str:
        return self.archivo


class Ejecucion(models.Model):
    """Una corrida del pipeline, lanzada desde la web o desde la línea de comandos."""

    class Estado(models.TextChoices):
        EN_CURSO = "en_curso", "En curso"
        EXITOSA = "exitosa", "Exitosa"
        FALLIDA = "fallida", "Fallida"

    iniciada = models.DateTimeField(auto_now_add=True)
    finalizada = models.DateTimeField(null=True, blank=True)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.EN_CURSO)
    incluye_carga_csv = models.BooleanField(default=False)
    origen = models.CharField(max_length=20, default="web")

    class Meta:
        ordering = ["-iniciada"]
        verbose_name = "ejecución"
        verbose_name_plural = "ejecuciones"

    def __str__(self) -> str:
        return f"Ejecución {self.pk} ({self.get_estado_display()})"

    @property
    def duracion_s(self) -> float | None:
        return sum(p.duracion_s for p in self.pasos.all()) if self.finalizada else None


class EjecucionPaso(models.Model):
    """Resultado de cada paso de una ejecución: la carga de CSV o un script SQL."""

    ejecucion = models.ForeignKey(Ejecucion, on_delete=models.CASCADE, related_name="pasos")
    consulta = models.ForeignKey(ConsultaSQL, on_delete=models.SET_NULL, null=True, blank=True)
    nombre = models.CharField(max_length=200)
    orden = models.PositiveIntegerField()
    exitoso = models.BooleanField()
    duracion_s = models.FloatField()
    detalle = models.TextField(blank=True, help_text="Resumen del paso o mensaje de error")

    class Meta:
        ordering = ["ejecucion", "orden"]
