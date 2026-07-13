# Importa OptimizarLNSView y OptimizarLNSStreamView desde .views
# y agrega a urlpatterns:

path("optimizacion/lns/ejecutar/", OptimizarLNSView.as_view(), name="optimizar-lns"),
path("optimizacion/lns/stream/", OptimizarLNSStreamView.as_view(), name="optimizar-lns-stream"),
