from django.urls import path

from .views import RangeViewSet, RangeConsultantListView, RangeMembershipView

app_name = "ranges"
urlpatterns = [
    path("ranges/", RangeViewSet.as_view({"get": "list", "post": "create"}), name="list"),
    path("ranges/<uuid:pk>/", RangeViewSet.as_view({"get": "retrieve", "patch": "partial_update"}), name="detail"),
    path("ranges/<uuid:pk>/consultants/", RangeConsultantListView.as_view(), name="consultants"),
    path("ranges/<uuid:pk>/consultants/<uuid:user_id>/", RangeMembershipView.as_view(), name="membership"),
]
