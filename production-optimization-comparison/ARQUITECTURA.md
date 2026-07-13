# Arquitectura y fases

## Fase 1 — Dominio y datos
Django Models + SQLite: SKU, Línea, compatibilidad, Turno, Operario y Orden.

## Fase 2 — API operativa
Django REST Framework expone CRUD, filtros, indicadores y acciones de optimización/reinicio.

## Fase 3 — Modelo matemático
OR-Tools CP-SAT asigna línea e inicio, evita solapamientos, introduce setups de secuencia, penaliza atraso y personal extra, y minimiza makespan.

## Fase 4 — Observabilidad del solver
`ProgressCallback` captura tiempo, objetivo, cota, atraso, personal extra y makespan de cada solución encontrada.

## Fase 5 — Experiencia web
Next.js consume la API y presenta controles, KPIs, Gantt por línea, gráfico de progreso y resumen técnico.

## Flujo

```text
Next.js UI
   │ Axios / JSON
   ▼
Django REST API ───► ORM / SQLite
   │
   └──────────────► OR-Tools CP-SAT
                       │
                       └── programa + historial de progreso
```
