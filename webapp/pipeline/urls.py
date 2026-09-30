from django.urls import path

from . import views

app_name = "pipeline"

urlpatterns = [
    path("", views.lista, name="lista"),
    path("<int:pk>/", views.detalle, name="detalle"),
    path("ejecutar/", views.ejecutar, name="ejecutar"),
    path("ejecuciones/<int:pk>/", views.ejecucion, name="ejecucion"),
    path("explorar/", views.explorar, name="explorar"),
]
