from django.core.management.base import BaseCommand, CommandError

from pipeline.models import Ejecucion
from pipeline.services import ejecutar_pipeline


class Command(BaseCommand):
    help = "Ejecuta el pipeline SQL (y opcionalmente la carga de CSV), registrando la ejecución."

    def add_arguments(self, parser):
        parser.add_argument("--carga-csv", action="store_true", help="recarga los CSV antes del pipeline")

    def handle(self, *args, **options):
        ejecucion = ejecutar_pipeline(incluir_carga_csv=options["carga_csv"], origen="consola")
        for paso in ejecucion.pasos.all():
            estado = self.style.SUCCESS("OK   ") if paso.exitoso else self.style.ERROR("ERROR")
            self.stdout.write(f"  {estado} {paso.nombre:<40} {paso.duracion_s:6.2f} s")
            if not paso.exitoso:
                self.stdout.write(self.style.ERROR(f"        {paso.detalle}"))
        if ejecucion.estado != Ejecucion.Estado.EXITOSA:
            raise CommandError(f"Ejecución {ejecucion.pk} fallida")
        self.stdout.write(self.style.SUCCESS(f"\nEjecución {ejecucion.pk} completada en {ejecucion.duracion_s:.1f} s"))
