from django.urls import path

from .views import CustomerViewSet

app_name = "customers"
urlpatterns = [
    path("customers/", CustomerViewSet.as_view({"get": "list", "post": "create"}), name="list"),
    path("customers/<uuid:pk>/", CustomerViewSet.as_view({"get": "retrieve", "patch": "partial_update"}), name="detail"),
]
