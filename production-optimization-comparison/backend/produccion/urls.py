from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    IndicadoresView,
    LineaViewSet,
    OperarioViewSet,
    OptimizarComparativoStreamView,
    OptimizarStreamView,
    OptimizarView,
    OrdenViewSet,
    ReiniciarView,
    SKULineaViewSet,
    SKUViewSet,
    TurnoViewSet,
)

router = DefaultRouter()
router.register("skus", SKUViewSet)
router.register("lineas", LineaViewSet)
router.register("sku-linea", SKULineaViewSet)
router.register("turnos", TurnoViewSet, basename="turno")
router.register("operarios", OperarioViewSet)
router.register("ordenes", OrdenViewSet)

urlpatterns = [
    path("", include(router.urls)),
    path(
        "optimizacion/ejecutar/",
        OptimizarView.as_view(),
        name="optimizar",
    ),
    path(
        "optimizacion/stream/",
        OptimizarStreamView.as_view(),
        name="optimizar-stream",
    ),
    path(
        "optimizacion/comparar/stream/",
        OptimizarComparativoStreamView.as_view(),
        name="optimizar-comparar-stream",
    ),
    path(
        "indicadores/",
        IndicadoresView.as_view(),
        name="indicadores",
    ),
    path(
        "datos/reiniciar/",
        ReiniciarView.as_view(),
        name="reiniciar",
    ),
]
