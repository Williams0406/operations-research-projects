# LNS independiente para el planificador

El archivo principal es:

```text
backend/produccion/lns_optimizer.py
```

No reemplaza `optimizer.py` y no modifica los modelos.

## Instalación

1. Copia `lns_optimizer.py` a `backend/produccion/`.
2. Añade `serializers_lns_patch.py` al final de `serializers.py`.
3. Añade `views_lns_patch.py` al final de `views.py`.
4. Añade las rutas indicadas en `urls_lns_patch.py`.
5. Cambia el endpoint del frontend a `/api/optimizacion/lns/stream/`.

## Parámetros

```json
{
  "intervalo_minutos": 30,
  "tiempo_limite_segundos": 20,
  "horizonte_dias": 10,
  "setup_formato_horas": 0.5,
  "setup_sabor_horas": 0.25,
  "limpieza_alergeno_horas": 1,
  "porcentaje_destruccion": 0.25,
  "temperatura_inicial": 0.05,
  "enfriamiento": 0.995,
  "semilla": 42,
  "max_iteraciones": 500
}
```

## Funcionamiento

- Construye una solución inicial por fecha compromiso.
- Destruye parte de la solución usando operadores `random`, `late`, `line` y `setup`.
- Repara insertando cada orden en una línea y posición factible.
- Evalúa atraso ponderado, personal adicional, setups y makespan.
- Emite por SSE cada solución aceptada o mejorada.
- Guarda únicamente la mejor solución encontrada.

No necesita migraciones.
