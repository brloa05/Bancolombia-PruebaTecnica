from django.urls import path

from . import views

app_name = "portafolios"

urlpatterns = [
    path("", views.cliente, name="cliente"),
    path("cartera/", views.cartera, name="cartera"),
    path("modelo/", views.modelo, name="modelo"),
    path("ia/propuesta/", views.generar_propuesta, name="generar_propuesta"),
    path("calidad/", views.calidad, name="calidad"),
    path("plotly.js", views.plotly_js, name="plotly_js"),
]
