from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("", include("portafolios.urls")),
    path("consultas/", include("pipeline.urls")),
    path("admin/", admin.site.urls),
]
