import math
import time
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from ortools.sat.python import cp_model
from rest_framework.exceptions import ValidationError

from .models import Operario, OrdenFabricacion, SKULinea, Turno

W_ATRASO = 1_000_000
W_EXTRA = 10_000
W_MAKESPAN = 1


def _ceil_slot(moment, origin, interval):
    return math.ceil((moment - origin).total_seconds() / (60 * interval))


def _floor_slot(moment, origin, interval):
    return math.floor((moment - origin).total_seconds() / (60 * interval))


def _hours(slots, interval):
    return Decimal(str(round(slots * interval / 60, 2)))


def _in_shift(moment, shift):
    current = timezone.localtime(moment).time().replace(tzinfo=None)
    if shift.hora_inicio < shift.hora_fin:
        return shift.hora_inicio <= current < shift.hora_fin
    return current >= shift.hora_inicio or current < shift.hora_fin


def _setup_slots(a, b, interval, format_hours, flavor_hours, allergen_hours):
    hours = 0.0
    if a.formato != b.formato:
        hours += format_hours
    if a.sabor != b.sabor:
        hours += flavor_hours
    if a.contiene_alergeno and not b.contiene_alergeno:
        hours += allergen_hours
    return math.ceil(hours * 60 / interval)


def _build_initial_schedule(
    *,
    orders,
    compat,
    origin,
    interval,
    horizon,
    setup_formato_horas,
    setup_sabor_horas,
    limpieza_alergeno_horas,
    order_strategy="due",
    line_strategy="late",
    iteration=0,
):
    line_available = defaultdict(int)
    line_last_sku = {}
    hints = {}
    rows = []
    total_late = 0
    weighted_late = 0

    order_keys = {
        "due": lambda order: (
            order.fecha_compromiso,
            order.prioridad,
            order.fecha_liberacion,
        ),
        "priority": lambda order: (
            order.prioridad,
            order.fecha_compromiso,
            order.fecha_liberacion,
        ),
        "release": lambda order: (
            order.fecha_liberacion,
            order.fecha_compromiso,
            order.prioridad,
        ),
        "largest": lambda order: (
            -order.cantidad,
            order.fecha_compromiso,
            order.prioridad,
        ),
    }
    ordered = sorted(orders, key=order_keys[order_strategy])

    for order in ordered:
        compatible = compat.get(order.sku_id, [])
        if not compatible:
            continue

        release = max(0, _ceil_slot(order.fecha_liberacion, origin, interval))
        due = _floor_slot(order.fecha_compromiso, origin, interval)
        best = None

        for link in compatible:
            duration = max(
                1,
                math.ceil(
                    order.cantidad
                    / float(link.velocidad_unidades_hora)
                    * 60
                    / interval
                ),
            )
            previous_sku = line_last_sku.get(link.linea_id)
            setup = (
                _setup_slots(
                    previous_sku,
                    order.sku,
                    interval,
                    setup_formato_horas,
                    setup_sabor_horas,
                    limpieza_alergeno_horas,
                )
                if previous_sku
                else 0
            )
            start = max(release, line_available[link.linea_id] + setup)
            end = start + duration
            if end > horizon:
                continue

            late = max(0, end - due)
            if line_strategy == "fastest":
                score = (
                    late,
                    -float(link.velocidad_unidades_hora),
                    end,
                    link.linea_id,
                )
            elif line_strategy == "balanced":
                score = (
                    late,
                    line_available[link.linea_id],
                    end,
                    link.linea_id,
                )
            else:
                score = (late, end, link.linea_id)
            if best is None or score < best["score"]:
                best = {
                    "score": score,
                    "link": link,
                    "start": start,
                    "end": end,
                    "duration": duration,
                    "late": late,
                }

        if best is None:
            continue

        link = best["link"]
        line_available[link.linea_id] = best["end"]
        line_last_sku[link.linea_id] = order.sku
        hints[order.id] = {
            "linea_id": link.linea_id,
            "start": best["start"],
            "end": best["end"],
            "late": best["late"],
        }
        total_late += best["late"]
        weighted_late += best["late"] * max(1, 6 - order.prioridad)

        start_dt = origin + timedelta(minutes=best["start"] * interval)
        end_dt = origin + timedelta(minutes=best["end"] * interval)
        rows.append({
            "id": order.id,
            "numero": order.numero,
            "sku": order.sku_id,
            "sku_nombre": order.sku.nombre,
            "cantidad": order.cantidad,
            "prioridad": order.prioridad,
            "fecha_liberacion": order.fecha_liberacion.isoformat(),
            "fecha_compromiso": order.fecha_compromiso.isoformat(),
            "linea_opt": link.linea.id,
            "linea_opt_nombre": link.linea.nombre,
            "inicio_opt": start_dt.isoformat(),
            "fin_opt": end_dt.isoformat(),
            "atraso_horas": float(_hours(best["late"], interval)),
            "estado": (
                OrdenFabricacion.Estado.RETRASADA
                if best["late"]
                else OrdenFabricacion.Estado.PROGRAMADA
            ),
        })

    makespan = max((hint["end"] for hint in hints.values()), default=0)
    objective = W_ATRASO * weighted_late + W_MAKESPAN * makespan
    progress = {
        "iteracion": iteration,
        "segundo": 0,
        "objetivo": round(objective, 2),
        "mejor_cota": 0,
        "atraso_horas": float(_hours(total_late, interval)),
        "personal_extra_slots": 0,
        "makespan_horas": float(_hours(makespan, interval)),
        "origen": "heuristica",
    }

    return {
        "hints": hints,
        "objective": objective,
        "event": {
            "tipo": "solution",
            "progreso": progress,
            "ordenes": rows,
        },
    }


def _save_schedule_rows(rows):
    orders = OrdenFabricacion.objects.in_bulk([row["id"] for row in rows])
    for row in rows:
        order = orders.get(row["id"])
        if not order:
            continue

        order.linea_opt_id = row["linea_opt"]
        order.inicio_opt = parse_datetime(row["inicio_opt"])
        order.fin_opt = parse_datetime(row["fin_opt"])
        order.atraso_horas = Decimal(str(row["atraso_horas"]))
        order.estado = row["estado"]
        order.motivo = "Programada mediante heurística inicial de visualización."
        order.save(
            update_fields=[
                "linea_opt",
                "inicio_opt",
                "fin_opt",
                "atraso_horas",
                "estado",
                "motivo",
            ]
        )


class ProgressCallback(cp_model.CpSolverSolutionCallback):
    """
    Captura cada solución factible mejorada encontrada por CP-SAT.

    event_handler recibe diccionarios serializables:
      {"tipo": "solution", "progreso": {...}, "ordenes": [...]}
    """

    def __init__(
        self,
        *,
        makespan,
        atraso_vars,
        extras,
        interval,
        origin,
        orders,
        alternatives,
        alternative_meta,
        event_handler=None,
        max_snapshots=120,
    ):
        super().__init__()
        self._makespan = makespan
        self._atraso_vars = atraso_vars
        self._extras = extras
        self._interval = interval
        self._origin = origin
        self._orders = orders
        self._alternatives = alternatives
        self._alternative_meta = alternative_meta
        self._event_handler = event_handler
        self._max_snapshots = max_snapshots
        self._started = time.perf_counter()
        self.history = []
        self.snapshots = []

    def _current_schedule(self):
        rows = []
        for order in self._orders:
            if order.id not in self._alternatives:
                continue

            chosen = None
            for (order_id, _line_id), meta in self._alternative_meta.items():
                if order_id == order.id and self.Value(meta["present"]):
                    chosen = meta
                    break

            if not chosen:
                continue

            start_slot = self.Value(chosen["start"])
            end_slot = self.Value(chosen["end"])
            late_slots = self.Value(self._atraso_vars[order.id])

            start = self._origin + timedelta(minutes=start_slot * self._interval)
            end = self._origin + timedelta(minutes=end_slot * self._interval)

            rows.append({
                "id": order.id,
                "numero": order.numero,
                "sku": order.sku_id,
                "sku_nombre": order.sku.nombre,
                "cantidad": order.cantidad,
                "prioridad": order.prioridad,
                "fecha_liberacion": order.fecha_liberacion.isoformat(),
                "fecha_compromiso": order.fecha_compromiso.isoformat(),
                "linea_opt": chosen["linea"].id,
                "linea_opt_nombre": chosen["linea"].nombre,
                "inicio_opt": start.isoformat(),
                "fin_opt": end.isoformat(),
                "atraso_horas": float(_hours(late_slots, self._interval)),
                "estado": (
                    OrdenFabricacion.Estado.RETRASADA
                    if late_slots
                    else OrdenFabricacion.Estado.PROGRAMADA
                ),
            })
        return rows

    def on_solution_callback(self):
        if len(self.history) >= self._max_snapshots:
            return

        elapsed = round(time.perf_counter() - self._started, 3)
        atraso_slots = sum(self.Value(v) for v in self._atraso_vars.values())
        extra_slots = sum(self.Value(v) for v in self._extras)

        progress = {
            "iteracion": len(self.history) + 1,
            "segundo": elapsed,
            "objetivo": round(self.ObjectiveValue(), 2),
            "mejor_cota": round(self.BestObjectiveBound(), 2),
            "atraso_horas": float(_hours(atraso_slots, self._interval)),
            "personal_extra_slots": extra_slots,
            "makespan_horas": float(
                _hours(self.Value(self._makespan), self._interval)
            ),
        }
        orders = self._current_schedule()
        event = {"tipo": "solution", "progreso": progress, "ordenes": orders}

        self.history.append(progress)
        self.snapshots.append(event)

        if self._event_handler:
            self._event_handler(event)


@transaction.atomic
def optimizar(
    ordenes=None,
    intervalo_minutos=30,
    tiempo_limite_segundos=20,
    fecha_inicio_optimizacion=None,
    horizonte_dias=10,
    setup_formato_horas=0.5,
    setup_sabor_horas=0.25,
    limpieza_alergeno_horas=1.0,
    event_handler=None,
):
    now = fecha_inicio_optimizacion or timezone.now()
    origin = now.replace(second=0, microsecond=0) - timedelta(
        minutes=now.minute % intervalo_minutos
    )

    query = OrdenFabricacion.objects.select_related("sku").filter(
        estado__in=["PENDIENTE", "PROGRAMADA", "RETRASADA", "NO_FACTIBLE"],
        inicio_real__isnull=True,
    )
    if ordenes:
        query = query.filter(pk__in=ordenes)

    orders = list(query.distinct())
    if not orders:
        raise ValidationError({"ordenes": "No hay órdenes reprogramables."})
    if len(orders) > 40:
        raise ValidationError(
            {"ordenes": "La demostración admite hasta 40 órdenes por corrida."}
        )

    horizon_end = max(
        max(o.fecha_compromiso for o in orders) + timedelta(days=2),
        origin + timedelta(days=horizonte_dias),
    )
    horizon = _ceil_slot(horizon_end, origin, intervalo_minutos)
    if horizon <= 0:
        raise ValidationError({"horizonte": "Horizonte inválido."})

    links = SKULinea.objects.select_related("sku", "linea").filter(
        sku__in=[o.sku for o in orders],
        linea__activa=True,
    )
    compat = defaultdict(list)
    for link in links:
        compat[link.sku_id].append(link)

    heuristic_variants = [
        ("due", "late"),
        ("priority", "late"),
        ("release", "balanced"),
        ("largest", "fastest"),
        ("due", "balanced"),
        ("priority", "fastest"),
    ]
    best_heuristic = None
    for iteration, (order_strategy, line_strategy) in enumerate(
        heuristic_variants,
        start=1,
    ):
        candidate = _build_initial_schedule(
            orders=orders,
            compat=compat,
            origin=origin,
            interval=intervalo_minutos,
            horizon=horizon,
            setup_formato_horas=setup_formato_horas,
            setup_sabor_horas=setup_sabor_horas,
            limpieza_alergeno_horas=limpieza_alergeno_horas,
            order_strategy=order_strategy,
            line_strategy=line_strategy,
            iteration=iteration,
        )
        if not candidate["event"]["ordenes"]:
            continue

        if best_heuristic is None or candidate["objective"] < best_heuristic["objective"]:
            best_heuristic = candidate
            if event_handler:
                event_handler(candidate["event"])

    initial_hints = best_heuristic["hints"] if best_heuristic else {}

    model = cp_model.CpModel()
    alternatives = defaultdict(list)
    alternative_meta = {}
    intervals_by_line = defaultdict(list)
    demands_by_slot = defaultdict(list)
    atraso_vars = {}
    all_presences = []
    order_end = {}
    use_personnel_constraints = len(orders) <= 20

    for order in orders:
        compatible = compat.get(order.sku_id, [])
        if not compatible:
            order.estado = OrdenFabricacion.Estado.NO_FACTIBLE
            order.motivo = "No existe una línea activa compatible con el SKU."
            order.linea_opt = None
            order.inicio_opt = None
            order.fin_opt = None
            order.save(
                update_fields=[
                    "estado",
                    "motivo",
                    "linea_opt",
                    "inicio_opt",
                    "fin_opt",
                ]
            )
            continue

        release = max(
            0, _ceil_slot(order.fecha_liberacion, origin, intervalo_minutos)
        )
        due = _floor_slot(order.fecha_compromiso, origin, intervalo_minutos)

        master_start = model.NewIntVar(release, horizon, f"start_{order.id}")
        master_end = model.NewIntVar(release, horizon, f"end_{order.id}")
        order_end[order.id] = master_end

        atraso = model.NewIntVar(0, horizon, f"late_{order.id}")
        model.Add(atraso >= master_end - due)
        atraso_vars[order.id] = atraso

        hint = initial_hints.get(order.id)
        if hint:
            model.AddHint(master_start, hint["start"])
            model.AddHint(master_end, hint["end"])
            model.AddHint(atraso, hint["late"])

        for link in compatible:
            duration = max(
                1,
                math.ceil(
                    order.cantidad
                    / float(link.velocidad_unidades_hora)
                    * 60
                    / intervalo_minutos
                ),
            )
            if horizon - duration < release:
                continue

            start = model.NewIntVar(
                release, horizon - duration, f"s_{order.id}_{link.linea_id}"
            )
            end = model.NewIntVar(
                release + duration, horizon, f"e_{order.id}_{link.linea_id}"
            )
            present = model.NewBoolVar(f"p_{order.id}_{link.linea_id}")
            interval_var = model.NewOptionalIntervalVar(
                start,
                duration,
                end,
                present,
                f"i_{order.id}_{link.linea_id}",
            )

            model.Add(master_start == start).OnlyEnforceIf(present)
            model.Add(master_end == end).OnlyEnforceIf(present)

            if hint:
                is_hint_line = int(hint["linea_id"] == link.linea_id)
                model.AddHint(present, is_hint_line)
                if is_hint_line:
                    model.AddHint(start, hint["start"])
                    model.AddHint(end, hint["end"])

            alternatives[order.id].append(present)
            intervals_by_line[link.linea_id].append(interval_var)
            all_presences.append(present)

            alternative_meta[(order.id, link.linea_id)] = {
                "order": order,
                "linea": link.linea,
                "start": start,
                "end": end,
                "present": present,
                "duration": duration,
            }

            if use_personnel_constraints:
                # Uso de personal discretizado por slot. Para corridas grandes,
                # esta familia de variables crece mucho y puede impedir que
                # CP-SAT encuentre siquiera la primera solución en una demo.
                for slot in range(release, horizon):
                    active = model.NewBoolVar(
                        f"active_{order.id}_{link.linea_id}_{slot}"
                    )
                    before = model.NewBoolVar(
                        f"before_{order.id}_{link.linea_id}_{slot}"
                    )
                    after = model.NewBoolVar(
                        f"after_{order.id}_{link.linea_id}_{slot}"
                    )

                    model.Add(start <= slot).OnlyEnforceIf(active)
                    model.Add(end > slot).OnlyEnforceIf(active)
                    model.AddImplication(active, present)

                    model.Add(start > slot).OnlyEnforceIf(before)
                    model.Add(start <= slot).OnlyEnforceIf(before.Not())
                    model.Add(end <= slot).OnlyEnforceIf(after)
                    model.Add(end > slot).OnlyEnforceIf(after.Not())
                    model.AddBoolOr([present.Not(), before, after, active])

                    demands_by_slot[slot].append((active, link.linea.head_count))

        if alternatives[order.id]:
            model.AddExactlyOne(alternatives[order.id])

    if not all_presences:
        result = {
            "estado": "SIN_DATOS",
            "detalle": "Ninguna orden tiene líneas compatibles.",
            "resultado": [],
            "progreso": [],
        }
        if event_handler:
            event_handler({"tipo": "complete", "resultado": result})
        return result

    for intervals in intervals_by_line.values():
        model.AddNoOverlap(intervals)

    # Setups dependientes de la secuencia.
    by_line_meta = defaultdict(list)
    for (_, line_id), meta in alternative_meta.items():
        by_line_meta[line_id].append(meta)

    for line_id, metas in by_line_meta.items():
        for i in range(len(metas)):
            for j in range(i + 1, len(metas)):
                a, b = metas[i], metas[j]
                if a["order"].id == b["order"].id:
                    continue

                a_before_b = model.NewBoolVar(
                    f"seq_{line_id}_{a['order'].id}_{b['order'].id}"
                )
                setup_ab = _setup_slots(
                    a["order"].sku,
                    b["order"].sku,
                    intervalo_minutos,
                    setup_formato_horas,
                    setup_sabor_horas,
                    limpieza_alergeno_horas,
                )
                setup_ba = _setup_slots(
                    b["order"].sku,
                    a["order"].sku,
                    intervalo_minutos,
                    setup_formato_horas,
                    setup_sabor_horas,
                    limpieza_alergeno_horas,
                )

                model.Add(
                    b["start"] >= a["end"] + setup_ab
                ).OnlyEnforceIf(
                    [a["present"], b["present"], a_before_b]
                )
                model.Add(
                    a["start"] >= b["end"] + setup_ba
                ).OnlyEnforceIf(
                    [a["present"], b["present"], a_before_b.Not()]
                )

    extras = []
    if use_personnel_constraints:
        shifts = list(Turno.objects.all())
        workers = {
            shift.id: Operario.objects.filter(
                turno=shift, activo=True
            ).count()
            for shift in shifts
        }
        max_people = max(
            1,
            sum(
                {
                    meta["linea"].id: meta["linea"].head_count
                    for meta in alternative_meta.values()
                }.values()
            ),
        )

        for slot, demands in demands_by_slot.items():
            moment = origin + timedelta(minutes=slot * intervalo_minutos)
            capacity = sum(
                workers[shift.id]
                for shift in shifts
                if _in_shift(moment, shift)
            )
            extra = model.NewIntVar(0, max_people, f"extra_{slot}")
            model.Add(
                sum(active * head_count for active, head_count in demands)
                <= capacity + extra
            )
            extras.append(extra)

    makespan = model.NewIntVar(0, horizon, "makespan")
    for end in order_end.values():
        model.Add(makespan >= end)

    weighted_lateness = [
        atraso_vars[order.id] * max(1, 6 - order.prioridad)
        for order in orders
        if order.id in atraso_vars
    ]
    model.Minimize(
        W_ATRASO * sum(weighted_lateness)
        + W_EXTRA * sum(extras)
        + W_MAKESPAN * makespan
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = tiempo_limite_segundos
    solver.parameters.num_search_workers = 8
    solver.parameters.log_search_progress = False

    callback = ProgressCallback(
        makespan=makespan,
        atraso_vars=atraso_vars,
        extras=extras,
        interval=intervalo_minutos,
        origin=origin,
        orders=orders,
        alternatives=alternatives,
        alternative_meta=alternative_meta,
        event_handler=event_handler,
    )

    if event_handler:
        event_handler({
            "tipo": "searching",
            "mensaje": "Modelo construido. CP-SAT esta buscando soluciones factibles.",
        })

    status = solver.Solve(model, callback)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        if best_heuristic and best_heuristic["event"]["ordenes"]:
            rows = best_heuristic["event"]["ordenes"]
            _save_schedule_rows(rows)
            result = {
                "estado": "HEURISTICA",
                "detalle": (
                    "CP-SAT agoto el tiempo sin una solucion propia; se "
                    "conservo la mejor programacion heuristica encontrada."
                ),
                "estado_solver": solver.StatusName(status),
                "soluciones_encontradas": len(callback.history),
                "ordenes_programadas": len(rows),
                "atraso_total_horas": best_heuristic["event"]["progreso"]["atraso_horas"],
                "personal_extra_slots": 0,
                "makespan_horas": best_heuristic["event"]["progreso"]["makespan_horas"],
                "objetivo": best_heuristic["event"]["progreso"]["objetivo"],
                "mejor_cota": solver.BestObjectiveBound(),
                "progreso": [best_heuristic["event"]["progreso"]],
                "resultado": rows,
            }
            if event_handler:
                event_handler({"tipo": "complete", "resultado": result})
            return result

        solver_status = solver.StatusName(status)
        if status == cp_model.UNKNOWN:
            estado = "SIN_SOLUCION_EN_TIEMPO"
            detalle = (
                "El solver agoto el limite de tiempo sin encontrar una "
                "solucion factible para visualizar."
            )
        elif status == cp_model.INFEASIBLE:
            estado = "INFACTIBLE"
            detalle = "Las restricciones actuales no permiten una solucion factible."
        elif status == cp_model.MODEL_INVALID:
            estado = "MODELO_INVALIDO"
            detalle = "El modelo CP-SAT generado no es valido."
        else:
            estado = "SIN_SOLUCION"
            detalle = "No se encontro solucion factible."

        result = {
            "estado": estado,
            "detalle": detalle,
            "estado_solver": solver_status,
            "soluciones_encontradas": len(callback.history),
            "resultado": [],
            "progreso": callback.history,
        }
        if event_handler:
            event_handler({"tipo": "complete", "resultado": result})
        return result

    result_rows = []
    total_late = 0

    for order in orders:
        if order.id not in alternatives:
            continue

        chosen = None
        for (order_id, _line_id), meta in alternative_meta.items():
            if order_id == order.id and solver.Value(meta["present"]):
                chosen = meta
                break

        if not chosen:
            continue

        start_slot = solver.Value(chosen["start"])
        end_slot = solver.Value(chosen["end"])
        late = solver.Value(atraso_vars[order.id])
        total_late += late

        order.linea_opt = chosen["linea"]
        order.inicio_opt = origin + timedelta(
            minutes=start_slot * intervalo_minutos
        )
        order.fin_opt = origin + timedelta(
            minutes=end_slot * intervalo_minutos
        )
        order.atraso_horas = _hours(late, intervalo_minutos)
        order.estado = (
            OrdenFabricacion.Estado.RETRASADA
            if late
            else OrdenFabricacion.Estado.PROGRAMADA
        )
        order.motivo = (
            "Programada mediante OR-Tools CP-SAT con compatibilidad, "
            "capacidad, personal y setups."
        )
        order.save()

        result_rows.append({
            "id": order.id,
            "numero": order.numero,
            "sku": order.sku_id,
            "sku_nombre": order.sku.nombre,
            "cantidad": order.cantidad,
            "prioridad": order.prioridad,
            "fecha_liberacion": order.fecha_liberacion.isoformat(),
            "fecha_compromiso": order.fecha_compromiso.isoformat(),
            "linea_opt": chosen["linea"].id,
            "linea_opt_nombre": chosen["linea"].nombre,
            "inicio_opt": order.inicio_opt.isoformat(),
            "fin_opt": order.fin_opt.isoformat(),
            "atraso_horas": float(order.atraso_horas),
            "estado": order.estado,
        })

    result = {
        "estado": (
            "OPTIMO" if status == cp_model.OPTIMAL else "FACTIBLE"
        ),
        "detalle": "Programación generada correctamente.",
        "ordenes_programadas": len(result_rows),
        "atraso_total_horas": float(
            _hours(total_late, intervalo_minutos)
        ),
        "personal_extra_slots": sum(
            solver.Value(extra) for extra in extras
        ),
        "makespan_horas": float(
            _hours(solver.Value(makespan), intervalo_minutos)
        ),
        "objetivo": round(solver.ObjectiveValue(), 2),
        "mejor_cota": round(solver.BestObjectiveBound(), 2),
        "progreso": callback.history,
        "resultado": result_rows,
    }

    if event_handler:
        event_handler({"tipo": "complete", "resultado": result})

    return result
