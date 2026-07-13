# Agrega OptimizarLNSSerializer al import de serializers de views.py.

class OptimizarLNSView(APIView):
    def post(self, request):
        serializer = OptimizarLNSSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        from .lns_optimizer import optimizar_lns
        return Response(optimizar_lns(**serializer.validated_data))


class OptimizarLNSStreamView(APIView):
    renderer_classes = [ServerSentEventRenderer, JSONRenderer]

    def post(self, request):
        serializer = OptimizarLNSSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        events = queue.Queue()

        def send_event(payload):
            events.put(payload)

        def run_optimizer():
            try:
                from .lns_optimizer import optimizar_lns
                optimizar_lns(**serializer.validated_data, event_handler=send_event)
            except Exception as exc:
                events.put({"tipo": "error", "mensaje": str(exc)})
            finally:
                events.put(None)

        threading.Thread(target=run_optimizer, daemon=True).start()

        def event_stream():
            yield "data: " + json.dumps({
                "tipo": "started",
                "algoritmo": "LNS",
                "mensaje": "Large Neighborhood Search inició la búsqueda.",
            }) + "\n\n"
            while True:
                event = events.get()
                if event is None:
                    break
                yield "data: " + json.dumps(event, ensure_ascii=False, default=str) + "\n\n"

        response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response
