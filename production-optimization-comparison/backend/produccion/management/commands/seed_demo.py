import random
from datetime import time, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from produccion.models import Linea, Operario, OrdenFabricacion, SKU, SKULinea, Turno

class Command(BaseCommand):
    help = "Carga datos sintéticos para la demostración."

    def add_arguments(self, parser):
        parser.add_argument("--ordenes", type=int, default=18)
        parser.add_argument("--liberacion-horas", type=int, default=18)
        parser.add_argument("--compromiso-min-horas", type=int, default=24)
        parser.add_argument("--compromiso-max-horas", type=int, default=48)

    def handle(self, *args, **options):
        random.seed(27)
        OrdenFabricacion.objects.all().delete()
        SKULinea.objects.all().delete()
        Operario.objects.all().delete()
        Turno.objects.all().delete()
        Linea.objects.all().delete()
        SKU.objects.all().delete()

        shifts = [
            Turno.objects.create(nombre="Turno mañana", hora_inicio=time(6), hora_fin=time(14)),
            Turno.objects.create(nombre="Turno tarde", hora_inicio=time(14), hora_fin=time(22)),
            Turno.objects.create(nombre="Turno noche", hora_inicio=time(22), hora_fin=time(6)),
        ]
        for i in range(15):
            Operario.objects.create(codigo=f"OP-{i+1:03}", nombres=f"Operario Demo {i+1}", turno=shifts[i % 3])

        lines = [
            Linea.objects.create(codigo="L-BOT", nombre="Línea de botellas", head_count=4, capacidad_hora=1500),
            Linea.objects.create(codigo="L-DOY", nombre="Línea de doypacks", head_count=3, capacidad_hora=1100),
            Linea.objects.create(codigo="L-FRA", nombre="Línea de frascos", head_count=4, capacidad_hora=900),
        ]
        specs = [
            ("SAL-250-TOM", "Salsa de tomate 250 ml", "SALSA", "Botella 250 ml", "Tomate", False),
            ("SAL-500-BBQ", "Salsa BBQ 500 ml", "SALSA", "Botella 500 ml", "BBQ", False),
            ("ADE-250-MOS", "Aderezo mostaza 250 ml", "ADEREZO", "Botella 250 ml", "Mostaza", True),
            ("ADE-500-AJO", "Aderezo de ajo 500 g", "ADEREZO", "Doypack 500 g", "Ajo", True),
            ("SAL-500-PIC", "Salsa picante 500 g", "SALSA", "Doypack 500 g", "Picante", False),
            ("CON-300-ALC", "Alcachofa en conserva 300 g", "CONSERVA", "Frasco 300 g", "Alcachofa", False),
            ("CON-500-PIM", "Pimiento en conserva 500 g", "CONSERVA", "Frasco 500 g", "Pimiento", False),
            ("ADE-300-CES", "Aderezo César 300 ml", "ADEREZO", "Frasco 300 g", "César", True),
        ]
        skus = [SKU.objects.create(codigo=c, nombre=n, familia=f, formato=fo, sabor=s, contiene_alergeno=a) for c,n,f,fo,s,a in specs]
        for sku in skus:
            if "Botella" in sku.formato:
                candidates = [lines[0]]
            elif "Doypack" in sku.formato:
                candidates = [lines[1]]
            else:
                candidates = [lines[2]]
            # A few flexible products create meaningful assignment decisions.
            if sku.codigo in {"SAL-250-TOM", "ADE-300-CES"}:
                candidates.append(lines[2] if lines[2] not in candidates else lines[0])
            for line in candidates:
                speed = float(line.capacidad_hora) * random.uniform(0.75, 1.05)
                SKULinea.objects.create(sku=sku, linea=line, velocidad_unidades_hora=round(speed, 2))

        release_window = options["liberacion_horas"]
        due_min = options["compromiso_min_horas"]
        due_max = options["compromiso_max_horas"]

        if due_max < due_min:
            self.stderr.write(self.style.ERROR(
                "--compromiso-max-horas debe ser mayor o igual que --compromiso-min-horas."
            ))
            return

        base = timezone.now().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        for i in range(options["ordenes"]):
            sku = random.choice(skus)
            release = base + timedelta(hours=random.randint(0, release_window))
            due = release + timedelta(hours=random.randint(due_min, due_max))
            OrdenFabricacion.objects.create(
                numero=f"OF-{2026001+i}", sku=sku, cantidad=random.choice([1800, 2400, 3200, 4000, 5000]),
                prioridad=random.choice([1, 2, 3, 4, 5]), fecha_liberacion=release, fecha_compromiso=due,
            )
        self.stdout.write(self.style.SUCCESS(
            f"Demo creada con {options['ordenes']} órdenes. "
            f"Liberación: 0-{release_window} h. "
            f"Compromiso: {due_min}-{due_max} h después de liberar."
        ))
