# Agrega esta clase al final de produccion/serializers.py

class OptimizarLNSSerializer(OptimizarSerializer):
    porcentaje_destruccion = serializers.FloatField(min_value=0.05, max_value=0.80, default=0.25)
    temperatura_inicial = serializers.FloatField(min_value=0, max_value=1, default=0.05)
    enfriamiento = serializers.FloatField(min_value=0.90, max_value=0.9999, default=0.995)
    semilla = serializers.IntegerField(min_value=0, max_value=2_147_483_647, default=42)
    max_iteraciones = serializers.IntegerField(min_value=1, max_value=100_000, default=500)
