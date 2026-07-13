from django.core.validators import MinValueValidator
from django.db import models

class SKU(models.Model):
    class Familia(models.TextChoices):
        SALSA = "SALSA", "Salsa"
        ADEREZO = "ADEREZO", "Aderezo"
        CONSERVA = "CONSERVA", "Conserva"

    codigo = models.CharField(max_length=30, unique=True)
    nombre = models.CharField(max_length=150)
    familia = models.CharField(max_length=20, choices=Familia.choices)
    formato = models.CharField(max_length=50)
    sabor = models.CharField(max_length=60)
    contiene_alergeno = models.BooleanField(default=False)
    unidad = models.CharField(max_length=20, default="unid")

    class Meta:
        ordering = ("familia", "formato", "nombre")

    def __str__(self):
        return f"{self.codigo} - {self.nombre}"

class Linea(models.Model):
    codigo = models.CharField(max_length=20, unique=True)
    nombre = models.CharField(max_length=100, unique=True)
    activa = models.BooleanField(default=True)
    head_count = models.PositiveIntegerField(default=2)
    capacidad_hora = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0.01)])

    class Meta:
        ordering = ("codigo",)

    def __str__(self):
        return self.nombre

class SKULinea(models.Model):
    sku = models.ForeignKey(SKU, on_delete=models.CASCADE, related_name="lineas_compatibles")
    linea = models.ForeignKey(Linea, on_delete=models.CASCADE, related_name="skus_compatibles")
    velocidad_unidades_hora = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0.01)])

    class Meta:
        constraints = [models.UniqueConstraint(fields=("sku", "linea"), name="uq_sku_linea")]

    def __str__(self):
        return f"{self.sku.codigo} → {self.linea.codigo}"

class Turno(models.Model):
    nombre = models.CharField(max_length=80, unique=True)
    hora_inicio = models.TimeField()
    hora_fin = models.TimeField()

    class Meta:
        ordering = ("hora_inicio",)

    def __str__(self):
        return self.nombre

class Operario(models.Model):
    codigo = models.CharField(max_length=30, unique=True)
    nombres = models.CharField(max_length=120)
    turno = models.ForeignKey(Turno, on_delete=models.PROTECT, related_name="operarios")
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ("codigo",)

    def __str__(self):
        return f"{self.codigo} - {self.nombres}"

class OrdenFabricacion(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = "PENDIENTE", "Pendiente"
        PROGRAMADA = "PROGRAMADA", "Programada"
        RETRASADA = "RETRASADA", "Retrasada"
        NO_FACTIBLE = "NO_FACTIBLE", "No factible"
        EN_PROCESO = "EN_PROCESO", "En proceso"
        FINALIZADA = "FINALIZADA", "Finalizada"

    numero = models.CharField(max_length=40, unique=True)
    sku = models.ForeignKey(SKU, on_delete=models.PROTECT, related_name="ordenes")
    cantidad = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    prioridad = models.PositiveSmallIntegerField(default=3, validators=[MinValueValidator(1)])
    fecha_liberacion = models.DateTimeField()
    fecha_compromiso = models.DateTimeField()

    linea_opt = models.ForeignKey(Linea, null=True, blank=True, on_delete=models.SET_NULL, related_name="ordenes_opt")
    inicio_opt = models.DateTimeField(null=True, blank=True)
    fin_opt = models.DateTimeField(null=True, blank=True)
    linea_real = models.ForeignKey(Linea, null=True, blank=True, on_delete=models.SET_NULL, related_name="ordenes_real")
    inicio_real = models.DateTimeField(null=True, blank=True)
    fin_real = models.DateTimeField(null=True, blank=True)

    atraso_horas = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.PENDIENTE)
    motivo = models.TextField(blank=True)

    class Meta:
        ordering = ("fecha_compromiso", "-prioridad")

    def __str__(self):
        return self.numero
