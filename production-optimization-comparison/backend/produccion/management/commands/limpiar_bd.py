from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Limpia todos los datos de la base de datos sin borrar tablas ni migraciones."

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Confirma la limpieza sin pedir confirmacion interactiva.",
        )

    def handle(self, *args, **options):
        if not options["yes"]:
            self.stdout.write(self.style.WARNING(
                "Esto borrara todos los datos de la base de datos configurada."
            ))
            confirmation = input("Escribe LIMPIAR para continuar: ")
            if confirmation != "LIMPIAR":
                raise CommandError("Operacion cancelada.")

        call_command(
            "flush",
            interactive=False,
            verbosity=options["verbosity"],
        )
        self.stdout.write(self.style.SUCCESS("Base de datos limpia."))
