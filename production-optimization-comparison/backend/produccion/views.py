import json
import queue
import threading

from django.db.models import Avg, Count, F, Max, Min, Q, Sum
from django.http import StreamingHttpResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.renderers import BaseRenderer, JSONRenderer

from .models import Linea, Operario, OrdenFabricacion, SKU, SKULinea, Turno
from .serializers import (
    LineaSerializer,
    OperarioSerializer,
    OptimizarComparativoSerializer,
    OptimizarSerializer,
    OrdenFabricacionSerializer,
    SKULineaSerializer,
    SKUSerializer,
    TurnoSerializer,
)


class SKUViewSet(viewsets.ModelViewSet):
    queryset = SKU.objects.all()
    serializer_class = SKUSerializer
    filterset_fields = ("familia", "formato", "contiene_alergeno")
    search_fields = ("codigo", "nombre", "sabor")
    ordering_fields = "__all__"


class LineaViewSet(viewsets.ModelViewSet):
    queryset = Linea.objects.all()
    serializer_class = LineaSerializer
    filterset_fields = ("activa",)
    search_fields = ("codigo", "nombre")
    ordering_fields = "__all__"


class SKULineaViewSet(viewsets.ModelViewSet):
    queryset = SKULinea.objects.select_related("sku", "linea")
    serializer_class = SKULineaSerializer
    filterset_fields = ("sku", "linea")


class TurnoViewSet(viewsets.ModelViewSet):
    serializer_class = TurnoSerializer

    def get_queryset(self):
        return Turno.objects.annotate(
            operarios_activos=Count(
                "operarios",
                filter=Q(operarios__activo=True),
            )
        )


class OperarioViewSet(viewsets.ModelViewSet):
    queryset = Operario.objects.select_related("turno")
    serializer_class = OperarioSerializer
    filterset_fields = ("turno", "activo")
    search_fields = ("codigo", "nombres")


class OrdenViewSet(viewsets.ModelViewSet):
    queryset = OrdenFabricacion.objects.select_related(
        "sku",
        "linea_opt",
        "linea_real",
    )
    serializer_class = OrdenFabricacionSerializer
    filterset_fields = ("estado", "sku", "linea_opt", "prioridad")
    search_fields = ("numero", "sku__codigo", "sku__nombre")
    ordering_fields = "__all__"

    @action(
        detail=True,
        methods=["get"],
        url_path="lineas-compatibles",
    )
    def lineas_compatibles(self, request, pk=None):
        order = self.get_object()
        links = SKULinea.objects.select_related("linea").filter(
            sku=order.sku,
            linea__activa=True,
        )
        return Response([
            {
                "id": link.linea_id,
                "nombre": link.linea.nombre,
                "velocidad": float(link.velocidad_unidades_hora),
            }
            for link in links
        ])

class ServerSentEventRenderer(BaseRenderer):
    media_type = "text/event-stream"
    format = "event-stream"
    charset = "utf-8"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


class OptimizarView(APIView):
    """Endpoint tradicional: responde cuando el solver termina."""

    def post(self, request):
        serializer = OptimizarSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        from .optimizer import optimizar

        return Response(optimizar(**serializer.validated_data))


class OptimizarStreamView(APIView):
    renderer_classes = [ServerSentEventRenderer, JSONRenderer]

    def post(self, request):
        serializer = OptimizarSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        events = queue.Queue()

        def send_event(payload):
            events.put(payload)

        def run_optimizer():
            try:
                from .optimizer import optimizar

                result = optimizar(
                    **serializer.validated_data,
                    event_handler=send_event,
                )

            except Exception as exc:
                events.put({
                    "tipo": "error",
                    "mensaje": str(exc),
                })

            finally:
                events.put(None)

        threading.Thread(
            target=run_optimizer,
            daemon=True,
        ).start()

        def event_stream():
            yield (
                "data: "
                + json.dumps({
                    "tipo": "started",
                    "mensaje": "El modelo CP-SAT inició la optimización.",
                })
                + "\n\n"
            )

            while True:
                event = events.get()

                if event is None:
                    break

                yield (
                    "data: "
                    + json.dumps(
                        event,
                        ensure_ascii=False,
                        default=str,
                    )
                    + "\n\n"
                )

        response = StreamingHttpResponse(
            event_stream(),
            content_type="text/event-stream",
        )

        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"

        return response


class OptimizarComparativoStreamView(APIView):
    renderer_classes = [ServerSentEventRenderer, JSONRenderer]

    def post(self, request):
        serializer = OptimizarComparativoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        events = queue.Queue()
        finished = {"CP-SAT": False, "LNS": False}
        payload = serializer.validated_data

        base_keys = {
            "ordenes",
            "intervalo_minutos",
            "tiempo_limite_segundos",
            "fecha_inicio_optimizacion",
            "horizonte_dias",
            "setup_formato_horas",
            "setup_sabor_horas",
            "limpieza_alergeno_horas",
        }
        lns_keys = base_keys | {
            "porcentaje_destruccion",
            "temperatura_inicial",
            "enfriamiento",
            "semilla",
            "max_iteraciones",
            "frecuencia_eventos",
        }
        cpsat_payload = {key: payload[key] for key in base_keys if key in payload}
        lns_payload = {key: payload[key] for key in lns_keys if key in payload}

        def send_event(algorithm, event):
            tagged = dict(event)
            tagged["algoritmo"] = algorithm
            if tagged.get("tipo") == "solution":
                tagged["progreso"] = {
                    **tagged.get("progreso", {}),
                    "algoritmo": algorithm,
                }
            if tagged.get("tipo") == "complete":
                tagged["resultado"] = {
                    **tagged.get("resultado", {}),
                    "algoritmo": algorithm,
                }
            events.put(tagged)

        def run_cpsat():
            try:
                from .optimizer import optimizar

                optimizar(
                    **cpsat_payload,
                    event_handler=lambda event: send_event("CP-SAT", event),
                )
            except Exception as exc:
                events.put({
                    "tipo": "error",
                    "algoritmo": "CP-SAT",
                    "mensaje": str(exc),
                })
            finally:
                events.put({"tipo": "finished", "algoritmo": "CP-SAT"})

        def run_lns():
            try:
                from .lns_optimizer import optimizar_lns

                optimizar_lns(
                    **lns_payload,
                    guardar_resultado=False,
                    event_handler=lambda event: send_event("LNS", event),
                )
            except Exception as exc:
                events.put({
                    "tipo": "error",
                    "algoritmo": "LNS",
                    "mensaje": str(exc),
                })
            finally:
                events.put({"tipo": "finished", "algoritmo": "LNS"})

        threading.Thread(target=run_cpsat, daemon=True).start()
        threading.Thread(target=run_lns, daemon=True).start()

        def event_stream():
            yield (
                "data: "
                + json.dumps({
                    "tipo": "started",
                    "algoritmo": "COMPARATIVO",
                    "mensaje": "CP-SAT y LNS iniciaron la optimización.",
                })
                + "\n\n"
            )

            while True:
                event = events.get()
                if event.get("tipo") == "finished":
                    finished[event["algoritmo"]] = True
                    if all(finished.values()):
                        break
                    continue

                yield (
                    "data: "
                    + json.dumps(event, ensure_ascii=False, default=str)
                    + "\n\n"
                )

            yield (
                "data: "
                + json.dumps({
                    "tipo": "complete_all",
                    "algoritmo": "COMPARATIVO",
                })
                + "\n\n"
            )

        response = StreamingHttpResponse(
            event_stream(),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response


class IndicadoresView(APIView):
    def get(self, request):
        all_orders = OrdenFabricacion.objects.select_related("linea_opt")
        scheduled = all_orders.filter(
            inicio_opt__isnull=False,
            fin_opt__isnull=False,
        )
        total = all_orders.count()
        scheduled_count = scheduled.count()
        on_time = scheduled.filter(atraso_horas=0).count()
        late = scheduled.filter(atraso_horas__gt=0).count()

        dates = scheduled.aggregate(
            start=Min("inicio_opt"),
            end=Max("fin_opt"),
        )
        makespan = 0
        if dates["start"] and dates["end"]:
            makespan = round(
                (
                    dates["end"] - dates["start"]
                ).total_seconds() / 3600,
                2,
            )

        processing = sum(
            (order.fin_opt - order.inicio_opt).total_seconds() / 3600
            for order in scheduled
        )
        active_lines = Linea.objects.filter(activa=True).count()
        utilization = (
            round(
                processing / (makespan * active_lines) * 100,
                1,
            )
            if makespan and active_lines
            else 0
        )

        by_line = list(
            scheduled.values(
                nombre=F("linea_opt__nombre")
            )
            .annotate(
                ordenes=Count("id"),
                horas_atraso=Sum("atraso_horas"),
            )
            .order_by("nombre")
        )
        by_status = list(
            all_orders.values("estado")
            .annotate(total=Count("id"))
            .order_by("estado")
        )

        return Response({
            "total_ordenes": total,
            "ordenes_programadas": scheduled_count,
            "cumplimiento_pct": (
                round(on_time / scheduled_count * 100, 1)
                if scheduled_count
                else 0
            ),
            "ordenes_retrasadas": late,
            "atraso_total_horas": float(
                scheduled.aggregate(
                    value=Sum("atraso_horas")
                )["value"]
                or 0
            ),
            "atraso_promedio_horas": round(
                float(
                    scheduled.aggregate(
                        value=Avg("atraso_horas")
                    )["value"]
                    or 0
                ),
                2,
            ),
            "makespan_horas": makespan,
            "utilizacion_lineas_pct": utilization,
            "por_linea": by_line,
            "por_estado": by_status,
        })


class ReiniciarView(APIView):
    def post(self, request):
        OrdenFabricacion.objects.update(
            linea_opt=None,
            inicio_opt=None,
            fin_opt=None,
            atraso_horas=0,
            estado="PENDIENTE",
            motivo="",
        )
        return Response(
            {"detalle": "Programación reiniciada."},
            status=status.HTTP_200_OK,
        )
