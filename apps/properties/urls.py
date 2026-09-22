from django.urls import path

from .views import PropertyFileImageView, PropertyFileImagesView, PropertyFileViewSet

app_name = "properties"
urlpatterns = [
    path("property-files/", PropertyFileViewSet.as_view({"get": "list", "post": "create"}), name="list"),
    path("property-files/<uuid:pk>/", PropertyFileViewSet.as_view({"get": "retrieve", "patch": "partial_update"}), name="detail"),
    path("property-files/<uuid:pk>/images/", PropertyFileImagesView.as_view(), name="images"),
    path("property-files/<uuid:pk>/images/<uuid:image_id>/", PropertyFileImageView.as_view(), name="image"),
]
