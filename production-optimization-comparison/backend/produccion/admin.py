from django.contrib import admin
from .models import Linea, Operario, OrdenFabricacion, SKU, SKULinea, Turno
admin.site.register([SKU, Linea, SKULinea, Turno, Operario, OrdenFabricacion])
