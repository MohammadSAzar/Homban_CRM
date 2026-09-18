from django.urls import path

from .views import CityViewSet, LocationSettingsView, RegionViewSet

app_name = "locations"
list_actions = {"get": "list", "post": "create"}
detail_actions = {"get": "retrieve", "patch": "partial_update"}

urlpatterns = [
    path("cities/", CityViewSet.as_view(list_actions), name="city-list"),
    path("cities/<uuid:pk>/", CityViewSet.as_view(detail_actions), name="city-detail"),
    path("regions/", RegionViewSet.as_view(list_actions), name="region-list"),
    path("regions/<uuid:pk>/", RegionViewSet.as_view(detail_actions), name="region-detail"),
    path("location-settings/", LocationSettingsView.as_view(), name="settings"),
]
