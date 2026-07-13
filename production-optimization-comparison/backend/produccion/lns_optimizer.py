import math
import random
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import Linea, Operario, OrdenFabricacion, SKULinea, Turno

W_ATRASO = 1_000_000
W_EXTRA = 10_000
W_MAKESPAN = 1
W_SETUP = 100


@dataclass
class ScheduledOrder:
    order_id: int
    linea_id: int
    start: int
    end: int
    late: int
    setup: int


@dataclass
class EvaluatedSolution:
    sequences: dict
    scheduled: dict
    objective: int
    weighted_lateness: int
    total_lateness: int
    extra_slots: int
    makespan: int
    total_setup: int


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
    if a is None:
        return 0
    hours = 0.0
    if a.formato != b.formato:
        hours += format_hours
    if a.sabor != b.sabor:
        hours += flavor_hours
    if a.contiene_alergeno and not b.contiene_alergeno:
        hours += allergen_hours
    return math.ceil(hours * 60 / interval)


def _duration_slots(order, link, interval):
    return max(1, math.ceil(order.cantidad / float(link.velocidad_unidades_hora) * 60 / interval))


def _copy_sequences(sequences):
    return {line_id: list(order_ids) for line_id, order_ids in sequences.items()}


def _signature(sequences):
    return tuple((line_id, tuple(ids)) for line_id, ids in sorted(sequences.items()))


def _build_context(orders, origin, interval):
    order_by_id = {o.id: o for o in orders}
    links = SKULinea.objects.select_related("sku", "linea").filter(
        sku__in=[o.sku for o in orders], linea__activa=True
    )
    compatible = defaultdict(list)
    link_by_pair = {}
    for link in links:
        compatible[link.sku_id].append(link)
        link_by_pair[(link.sku_id, link.linea_id)] = link

    release = {o.id: max(0, _ceil_slot(o.fecha_liberacion, origin, interval)) for o in orders}
    due = {o.id: _floor_slot(o.fecha_compromiso, origin, interval) for o in orders}
    shifts = list(Turno.objects.all())
    workers = {s.id: Operario.objects.filter(turno=s, activo=True).count() for s in shifts}
    return {
        "order_by_id": order_by_id,
        "compatible": compatible,
        "link_by_pair": link_by_pair,
        "release": release,
        "due": due,
        "shifts": shifts,
        "workers": workers,
        "use_personnel": len(orders) <= 20,
    }


def _evaluate(sequences, context, origin, interval, horizon,
              setup_formato_horas, setup_sabor_horas, limpieza_alergeno_horas,
              require_all=True):
    order_by_id = context["order_by_id"]
    scheduled = {}
    total_lateness = 0
    weighted_lateness = 0
    total_setup = 0
    makespan = 0
    slot_demands = defaultdict(int)

    for line_id, order_ids in sequences.items():
        current = 0
        previous_sku = None
        for order_id in order_ids:
            order = order_by_id[order_id]
            link = context["link_by_pair"].get((order.sku_id, line_id))
            if not link:
                return None
            setup = _setup_slots(previous_sku, order.sku, interval,
                                 setup_formato_horas, setup_sabor_horas,
                                 limpieza_alergeno_horas)
            duration = _duration_slots(order, link, interval)
            start = max(context["release"][order_id], current + setup)
            end = start + duration
            if end > horizon:
                return None
            late = max(0, end - context["due"][order_id])
            scheduled[order_id] = ScheduledOrder(order_id, line_id, start, end, late, setup)
            if context["use_personnel"]:
                for slot in range(start, end):
                    slot_demands[slot] += link.linea.head_count
            total_lateness += late
            weighted_lateness += late * max(1, 6 - order.prioridad)
            total_setup += setup
            makespan = max(makespan, end)
            current = end
            previous_sku = order.sku

    if require_all and len(scheduled) != len(order_by_id):
        return None

    extra_slots = 0
    if context["use_personnel"]:
        for slot, demand in slot_demands.items():
            moment = origin + timedelta(minutes=slot * interval)
            capacity = sum(
                context["workers"][shift.id]
                for shift in context["shifts"]
                if _in_shift(moment, shift)
            )
            extra_slots += max(0, demand - capacity)

    objective = (
        W_ATRASO * weighted_lateness
        + W_EXTRA * extra_slots
        + W_SETUP * total_setup
        + W_MAKESPAN * makespan
    )
    return EvaluatedSolution(
        _copy_sequences(sequences), scheduled, int(objective),
        weighted_lateness, total_lateness, extra_slots, makespan, total_setup
    )


def _initial_solution(orders, context, origin, interval, horizon,
                      setup_formato_horas, setup_sabor_horas, limpieza_alergeno_horas,
                      strategy="due"):
    sequences = defaultdict(list)
    order_keys = {
        "due": lambda o: (o.fecha_compromiso, o.prioridad, o.fecha_liberacion, -o.cantidad),
        "priority": lambda o: (o.prioridad, o.fecha_compromiso, o.fecha_liberacion, -o.cantidad),
        "release": lambda o: (o.fecha_liberacion, o.fecha_compromiso, o.prioridad, -o.cantidad),
        "largest": lambda o: (-o.cantidad, o.fecha_compromiso, o.prioridad),
        "slack": lambda o: (
            (o.fecha_compromiso - o.fecha_liberacion).total_seconds(),
            o.prioridad,
            -o.cantidad,
        ),
    }
    ordered = sorted(orders, key=order_keys[strategy])
    for order in ordered:
        best = None
        for link in context["compatible"].get(order.sku_id, []):
            candidate = _copy_sequences(sequences)
            candidate.setdefault(link.linea_id, []).append(order.id)
            evaluated = _evaluate(
                candidate, context, origin, interval, horizon,
                setup_formato_horas, setup_sabor_horas,
                limpieza_alergeno_horas, require_all=False
            )
            if evaluated:
                key = (evaluated.objective, evaluated.makespan, link.linea_id)
                if best is None or key < best[0]:
                    best = (key, candidate)
        if best is None:
            raise ValidationError({"ordenes": f"No se pudo insertar la orden {order.numero}."})
        sequences = defaultdict(list, best[1])

    solution = _evaluate(
        sequences, context, origin, interval, horizon,
        setup_formato_horas, setup_sabor_horas,
        limpieza_alergeno_horas, require_all=True
    )
    if solution is None:
        raise ValidationError({"detalle": "No se pudo construir una solución inicial factible."})
    return solution


def _best_initial_solution(orders, context, origin, interval, horizon,
                           setup_formato_horas, setup_sabor_horas, limpieza_alergeno_horas):
    best = None
    for strategy in ("due", "priority", "release", "largest", "slack"):
        try:
            candidate = _initial_solution(
                orders, context, origin, interval, horizon,
                setup_formato_horas, setup_sabor_horas, limpieza_alergeno_horas,
                strategy=strategy,
            )
        except ValidationError:
            continue
        if best is None or candidate.objective < best.objective:
            best = candidate
    if best is None:
        raise ValidationError({"detalle": "No se pudo construir una solución inicial factible."})
    return best


def _destroy_random(solution, count, rng, context):
    return set(rng.sample(list(solution.scheduled), min(count, len(solution.scheduled))))


def _destroy_late(solution, count, rng, context):
    ranked = sorted(solution.scheduled.values(), key=lambda x: (x.late, x.end), reverse=True)
    return {x.order_id for x in ranked[:count]}


def _destroy_line(solution, count, rng, context):
    lines = [line_id for line_id, ids in solution.sequences.items() if ids]
    if not lines:
        return _destroy_random(solution, count, rng, context)
    ids = list(solution.sequences[rng.choice(lines)])
    if len(ids) >= count:
        start = rng.randint(0, len(ids) - count)
        return set(ids[start:start + count])
    selected = set(ids)
    rest = [i for i in solution.scheduled if i not in selected]
    selected.update(rng.sample(rest, min(count - len(selected), len(rest))))
    return selected


def _destroy_setup(solution, count, rng, context):
    order_by_id = context["order_by_id"]
    scored = []
    for ids in solution.sequences.values():
        prev = None
        for order_id in ids:
            sku = order_by_id[order_id].sku
            score = 0
            if prev:
                score += int(prev.formato != sku.formato)
                score += int(prev.sabor != sku.sabor)
                score += 2 * int(prev.contiene_alergeno and not sku.contiene_alergeno)
            scored.append((score, order_id))
            prev = sku
    scored.sort(reverse=True)
    return {order_id for _, order_id in scored[:count]}


def _destroy_mixed(solution, count, rng, context):
    late_count = max(1, count // 2)
    selected = set(_destroy_late(solution, late_count, rng, context))
    remaining = [order_id for order_id in solution.scheduled if order_id not in selected]
    selected.update(rng.sample(remaining, min(count - len(selected), len(remaining))))
    return selected


def _remove_orders(sequences, removed):
    return {
        line_id: [order_id for order_id in ids if order_id not in removed]
        for line_id, ids in sequences.items()
    }


def _candidate_positions(current, max_positions, rng):
    positions = list(range(len(current) + 1))
    if max_positions is None or len(positions) <= max_positions:
        return positions

    selected = {0, len(current)}
    while len(selected) < max_positions:
        selected.add(rng.choice(positions))
    return sorted(selected)


def _best_insertion(sequences, order_id, context, origin, interval, horizon,
                    setup_formato_horas, setup_sabor_horas, limpieza_alergeno_horas,
                    rng=None, max_positions=None):
    order = context["order_by_id"][order_id]
    best = None
    for link in context["compatible"].get(order.sku_id, []):
        current = sequences.get(link.linea_id, [])
        positions = (
            _candidate_positions(current, max_positions, rng)
            if rng
            else range(len(current) + 1)
        )
        for position in positions:
            candidate = _copy_sequences(sequences)
            candidate.setdefault(link.linea_id, []).insert(position, order_id)
            evaluated = _evaluate(
                candidate, context, origin, interval, horizon,
                setup_formato_horas, setup_sabor_horas,
                limpieza_alergeno_horas, require_all=False
            )
            if evaluated:
                key = (evaluated.objective, evaluated.makespan, link.linea_id, position)
                if best is None or key < best[0]:
                    best = (key, candidate)
    return best[1] if best else None


def _repair(partial_sequences, removed, context, origin, interval, horizon,
            setup_formato_horas, setup_sabor_horas, limpieza_alergeno_horas,
            rng=None, max_positions=None):
    sequences = _copy_sequences(partial_sequences)
    remaining = set(removed)
    while remaining:
        best = None
        for order_id in remaining:
            candidate = _best_insertion(
                sequences, order_id, context, origin, interval, horizon,
                setup_formato_horas, setup_sabor_horas,
                limpieza_alergeno_horas,
                rng=rng,
                max_positions=max_positions,
            )
            if candidate is None:
                continue
            evaluated = _evaluate(
                candidate, context, origin, interval, horizon,
                setup_formato_horas, setup_sabor_horas,
                limpieza_alergeno_horas, require_all=False
            )
            key = (evaluated.objective, evaluated.makespan, order_id)
            if best is None or key < best[0]:
                best = (key, order_id, candidate)
        if best is None:
            return None
        _, order_id, sequences = best
        remaining.remove(order_id)
    return sequences


def _relocate_late_orders(solution, context, origin, interval, horizon,
                          setup_formato_horas, setup_sabor_horas,
                          limpieza_alergeno_horas, limit=10):
    ranked = sorted(
        solution.scheduled.values(),
        key=lambda item: (item.late, item.end),
        reverse=True,
    )
    best = solution

    for item in ranked[:limit]:
        partial = _remove_orders(best.sequences, {item.order_id})
        repaired = _best_insertion(
            partial,
            item.order_id,
            context,
            origin,
            interval,
            horizon,
            setup_formato_horas,
            setup_sabor_horas,
            limpieza_alergeno_horas,
        )
        if repaired is None:
            continue

        candidate = _evaluate(
            repaired,
            context,
            origin,
            interval,
            horizon,
            setup_formato_horas,
            setup_sabor_horas,
            limpieza_alergeno_horas,
            require_all=True,
        )
        if candidate and candidate.objective < best.objective:
            best = candidate

    return best if best.objective < solution.objective else None


def _rows(solution, context, origin, interval):
    line_names = {line.id: line.nombre for line in Linea.objects.filter(id__in=solution.sequences)}
    rows = []
    for order_id, item in solution.scheduled.items():
        order = context["order_by_id"][order_id]
        start = origin + timedelta(minutes=item.start * interval)
        end = origin + timedelta(minutes=item.end * interval)
        rows.append({
            "id": order.id,
            "numero": order.numero,
            "sku": order.sku_id,
            "sku_nombre": order.sku.nombre,
            "cantidad": order.cantidad,
            "prioridad": order.prioridad,
            "fecha_liberacion": order.fecha_liberacion.isoformat(),
            "fecha_compromiso": order.fecha_compromiso.isoformat(),
            "linea_opt": item.linea_id,
            "linea_opt_nombre": line_names[item.linea_id],
            "inicio_opt": start.isoformat(),
            "fin_opt": end.isoformat(),
            "atraso_horas": float(_hours(item.late, interval)),
            "setup_horas": float(_hours(item.setup, interval)),
            "estado": OrdenFabricacion.Estado.RETRASADA if item.late else OrdenFabricacion.Estado.PROGRAMADA,
        })
    return sorted(rows, key=lambda r: (r["linea_opt_nombre"], r["inicio_opt"]))


def _progress(solution, iteration, elapsed, interval, operator, accepted, is_best):
    return {
        "iteracion": iteration,
        "segundo": round(elapsed, 3),
        "objetivo": solution.objective,
        "mejor_cota": None,
        "atraso_horas": float(_hours(solution.total_lateness, interval)),
        "atraso_ponderado_slots": solution.weighted_lateness,
        "personal_extra_slots": solution.extra_slots,
        "makespan_horas": float(_hours(solution.makespan, interval)),
        "setup_horas": float(_hours(solution.total_setup, interval)),
        "operador": operator,
        "aceptada": accepted,
        "es_mejor": is_best,
        "origen": "LNS",
    }


@transaction.atomic
def _save(solution, context, origin, interval):
    lines = Linea.objects.in_bulk(solution.sequences.keys())
    for order_id, item in solution.scheduled.items():
        order = context["order_by_id"][order_id]
        order.linea_opt = lines[item.linea_id]
        order.inicio_opt = origin + timedelta(minutes=item.start * interval)
        order.fin_opt = origin + timedelta(minutes=item.end * interval)
        order.atraso_horas = _hours(item.late, interval)
        order.estado = OrdenFabricacion.Estado.RETRASADA if item.late else OrdenFabricacion.Estado.PROGRAMADA
        order.motivo = "Programada mediante Large Neighborhood Search."
        order.save()


def optimizar_lns(
    ordenes=None,
    intervalo_minutos=30,
    tiempo_limite_segundos=20,
    fecha_inicio_optimizacion=None,
    horizonte_dias=10,
    setup_formato_horas=0.5,
    setup_sabor_horas=0.25,
    limpieza_alergeno_horas=1.0,
    porcentaje_destruccion=0.25,
    temperatura_inicial=0.05,
    enfriamiento=0.995,
    semilla=42,
    max_iteraciones=500,
    frecuencia_eventos=10,
    guardar_resultado=True,
    event_handler=None,
):
    if not 0.05 <= porcentaje_destruccion <= 0.80:
        raise ValidationError({"porcentaje_destruccion": "Debe estar entre 0.05 y 0.80."})

    now = fecha_inicio_optimizacion or timezone.now()
    origin = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % intervalo_minutos)

    query = OrdenFabricacion.objects.select_related("sku").filter(
        estado__in=["PENDIENTE", "PROGRAMADA", "RETRASADA", "NO_FACTIBLE"],
        inicio_real__isnull=True,
    )
    if ordenes:
        query = query.filter(pk__in=ordenes)
    orders = list(query.distinct())
    if not orders:
        raise ValidationError({"ordenes": "No hay órdenes reprogramables."})
    if len(orders) > 100:
        raise ValidationError({"ordenes": "La demostración LNS admite hasta 100 órdenes."})

    horizon_end = max(
        max(o.fecha_compromiso for o in orders) + timedelta(days=2),
        origin + timedelta(days=horizonte_dias),
    )
    horizon = _ceil_slot(horizon_end, origin, intervalo_minutos)
    context = _build_context(orders, origin, intervalo_minutos)

    for order in orders:
        if not context["compatible"].get(order.sku_id):
            raise ValidationError({"ordenes": f"La orden {order.numero} no posee línea compatible."})

    rng = random.Random(semilla)
    started = time.perf_counter()
    current = _best_initial_solution(
        orders, context, origin, intervalo_minutos, horizon,
        setup_formato_horas, setup_sabor_horas, limpieza_alergeno_horas
    )
    best = current
    operators = {
        "random": _destroy_random,
        "late": _destroy_late,
        "line": _destroy_line,
        "setup": _destroy_setup,
        "mixed": _destroy_mixed,
    }
    scores = {name: 1.0 for name in operators}
    uses = {name: 0 for name in operators}
    history = []
    seen = {_signature(current.sequences)}

    p0 = _progress(current, 0, 0, intervalo_minutos, "initial", True, True)
    history.append(p0)
    if event_handler:
        event_handler({"tipo": "solution", "progreso": p0, "ordenes": _rows(current, context, origin, intervalo_minutos)})

    temperature = max(1.0, current.objective * temperatura_inicial)
    iteration = 0

    while iteration < max_iteraciones and time.perf_counter() - started < tiempo_limite_segundos:
        iteration += 1
        names = list(operators)
        weights = [scores[n] / max(1, uses[n]) for n in names]
        operator_name = rng.choices(names, weights=weights, k=1)[0]
        uses[operator_name] += 1

        adaptive_pct = porcentaje_destruccion
        if len(orders) >= 25:
            adaptive_pct = min(adaptive_pct, 0.18)
        if iteration % 17 == 0:
            adaptive_pct = min(0.35, adaptive_pct * 1.6)
        count = max(1, min(len(orders) - 1, round(len(orders) * adaptive_pct)))
        removed = operators[operator_name](current, count, rng, context)
        partial = _remove_orders(current.sequences, removed)
        repaired = _repair(
            partial, removed, context, origin, intervalo_minutos, horizon,
            setup_formato_horas, setup_sabor_horas, limpieza_alergeno_horas,
            rng=rng,
            max_positions=7 if len(orders) >= 25 else None,
        )
        if repaired is None:
            temperature *= enfriamiento
            continue

        sig = _signature(repaired)
        if sig in seen:
            temperature *= enfriamiento
            continue
        seen.add(sig)

        candidate = _evaluate(
            repaired, context, origin, intervalo_minutos, horizon,
            setup_formato_horas, setup_sabor_horas,
            limpieza_alergeno_horas, require_all=True
        )
        if candidate is None:
            temperature *= enfriamiento
            continue

        refined = _relocate_late_orders(
            candidate,
            context,
            origin,
            intervalo_minutos,
            horizon,
            setup_formato_horas,
            setup_sabor_horas,
            limpieza_alergeno_horas,
            limit=8,
        )
        if refined is not None:
            candidate = refined

        delta = candidate.objective - current.objective
        accepted = delta < 0 or (temperature > 0 and rng.random() < math.exp(-delta / temperature))
        is_best = candidate.objective < best.objective

        if accepted:
            current = candidate
            scores[operator_name] += 2.0
        if is_best:
            best = candidate
            scores[operator_name] += 8.0

        emitted = False
        if accepted or is_best:
            progress = _progress(
                candidate, iteration, time.perf_counter() - started,
                intervalo_minutos, operator_name, accepted, is_best
            )
            history.append(progress)
            if event_handler:
                event_handler({
                    "tipo": "solution",
                    "progreso": progress,
                    "ordenes": _rows(candidate, context, origin, intervalo_minutos),
                })
            emitted = True

        if (
            not emitted
            and event_handler
            and frecuencia_eventos
            and iteration % frecuencia_eventos == 0
        ):
            progress = _progress(
                current, iteration, time.perf_counter() - started,
                intervalo_minutos, operator_name, False, False
            )
            history.append(progress)
            event_handler({
                "tipo": "solution",
                "progreso": progress,
                "ordenes": _rows(current, context, origin, intervalo_minutos),
            })

        temperature *= enfriamiento

    result_rows = _rows(best, context, origin, intervalo_minutos)
    final_progress = _progress(
        best,
        iteration,
        time.perf_counter() - started,
        intervalo_minutos,
        "best_final",
        True,
        True,
    )
    if not history or history[-1]["objetivo"] != best.objective:
        history.append(final_progress)
        if event_handler:
            event_handler({
                "tipo": "solution",
                "progreso": final_progress,
                "ordenes": result_rows,
            })

    if guardar_resultado:
        _save(best, context, origin, intervalo_minutos)
    result = {
        "estado": "LNS_FINALIZADO",
        "detalle": "Programación generada mediante Large Neighborhood Search.",
        "algoritmo": "LNS",
        "ordenes_programadas": len(result_rows),
        "iteraciones": iteration,
        "mejoras_visualizadas": len(history),
        "atraso_total_horas": float(_hours(best.total_lateness, intervalo_minutos)),
        "atraso_ponderado_slots": best.weighted_lateness,
        "personal_extra_slots": best.extra_slots,
        "makespan_horas": float(_hours(best.makespan, intervalo_minutos)),
        "setup_total_horas": float(_hours(best.total_setup, intervalo_minutos)),
        "objetivo": best.objective,
        "mejor_cota": None,
        "progreso": history,
        "resultado": result_rows,
        "operadores": {name: {"score": round(scores[name], 2), "usos": uses[name]} for name in operators},
    }
    if event_handler:
        event_handler({"tipo": "complete", "resultado": result})
    return result
