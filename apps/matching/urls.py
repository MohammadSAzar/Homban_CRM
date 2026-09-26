from django.urls import path
from .views import MatchingProfileView, MatchingProfileResetView

urlpatterns = [
    path("matching-profile/", MatchingProfileView.as_view(), name="matching-profile"),
    path("matching-profile/reset/", MatchingProfileResetView.as_view(), name="matching-profile-reset"),
]
