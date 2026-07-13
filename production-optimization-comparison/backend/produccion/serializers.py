from rest_framework import serializers
from .models import Linea, Operario, OrdenFabricacion, SKU, SKULinea, Turno

class SKUSerializer(serializers.ModelSerializer):
    class Meta:
        model = SKU
        fields = "__all__"

class LineaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Linea
        fields = "__all__"

class SKULineaSerializer(serializers.ModelSerializer):
    sku_nombre = serializers.CharField(source="sku.nombre", read_only=True)
    linea_nombre = serializers.CharField(source="linea.nombre", read_only=True)
    class Meta:
        model = SKULinea
        fields = "__all__"

class TurnoSerializer(serializers.ModelSerializer):
    operarios_activos = serializers.IntegerField(read_only=True)
    class Meta:
        model = Turno
        fields = ("id", "nombre", "hora_inicio", "hora_fin", "operarios_activos")

class OperarioSerializer(serializers.ModelSerializer):
    turno_nombre = serializers.CharField(source="turno.nombre", read_only=True)
    class Meta:
        model = Operario
        fields = "__all__"

class OrdenFabricacionSerializer(serializers.ModelSerializer):
    sku_codigo = serializers.CharField(source="sku.codigo", read_only=True)
    sku_nombre = serializers.CharField(source="sku.nombre", read_only=True)
    formato = serializers.CharField(source="sku.formato", read_only=True)
    sabor = serializers.CharField(source="sku.sabor", read_only=True)
    linea_opt_nombre = serializers.CharField(source="linea_opt.nombre", read_only=True)
    linea_real_nombre = serializers.CharField(source="linea_real.nombre", read_only=True)

    class Meta:
        model = OrdenFabricacion
        fields = "__all__"

    def validate(self, attrs):
        release = attrs.get("fecha_liberacion", getattr(self.instance, "fecha_liberacion", None))
        due = attrs.get("fecha_compromiso", getattr(self.instance, "fecha_compromiso", None))
        if release and due and due <= release:
            raise serializers.ValidationError({"fecha_compromiso": "Debe ser posterior a la liberación."})
        return attrs

class OptimizarSerializer(serializers.Serializer):
    ordenes = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False)
    intervalo_minutos = serializers.ChoiceField(choices=(15, 30, 60), default=30)
    tiempo_limite_segundos = serializers.IntegerField(min_value=1, max_value=300, default=20)
    fecha_inicio_optimizacion = serializers.DateTimeField(required=False)
    horizonte_dias = serializers.IntegerField(min_value=1, max_value=60, default=10)
    setup_formato_horas = serializers.FloatField(min_value=0, max_value=8, default=0.5)
    setup_sabor_horas = serializers.FloatField(min_value=0, max_value=8, default=0.25)
    limpieza_alergeno_horas = serializers.FloatField(min_value=0, max_value=12, default=1.0)


class OptimizarComparativoSerializer(OptimizarSerializer):
    porcentaje_destruccion = serializers.FloatField(min_value=0.05, max_value=0.80, default=0.18)
    temperatura_inicial = serializers.FloatField(min_value=0, max_value=1, default=0.05)
    enfriamiento = serializers.FloatField(min_value=0.90, max_value=0.9999, default=0.995)
    semilla = serializers.IntegerField(min_value=0, max_value=2_147_483_647, default=42)
    max_iteraciones = serializers.IntegerField(min_value=1, max_value=100_000, default=2000)
    frecuencia_eventos = serializers.IntegerField(min_value=1, max_value=1000, default=5)
