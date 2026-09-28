from django.urls import path
from .live_views import CustomerMatchesView, PropertyFileMatchesView
from .views import MatchingProfileView, MatchingProfileResetView

urlpatterns = [
    path("customers/<uuid:pk>/matches/", CustomerMatchesView.as_view(), name="customer-matches"),
    path("property-files/<uuid:pk>/matches/", PropertyFileMatchesView.as_view(), name="property-file-matches"),
    path("matching-profile/", MatchingProfileView.as_view(), name="matching-profile"),
    path("matching-profile/reset/", MatchingProfileResetView.as_view(), name="matching-profile-reset"),
]
