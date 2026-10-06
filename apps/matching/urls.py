from django.urls import path
from .live_views import CustomerMatchesView, PropertyFileMatchesView, WeakCustomerMatchesView, WeakPropertyFileMatchesView
from .daily_views import DailyTasksView, DailyMatchingDetailView, DailyMatchingStatusView, DailyCollaborationDetailView, DailyCollaborationStatusView
from .views import MatchingProfileView, MatchingProfileResetView

from .collaboration_views import RecommendationCollaborationView, LiveCollaborationView, CollaborationDetailView, CollaborationStatusView

urlpatterns = [
    path("daily-tasks/", DailyTasksView.as_view(), name="daily-tasks"),
    path("daily-tasks/matching/<uuid:pk>/", DailyMatchingDetailView.as_view(), name="daily-matching-detail"),
    path("daily-tasks/matching/<uuid:pk>/status/", DailyMatchingStatusView.as_view(), name="daily-matching-status"),
    path("daily-tasks/collaboration/<uuid:pk>/", DailyCollaborationDetailView.as_view(), name="daily-collaboration-detail"),
    path("daily-tasks/collaboration/<uuid:pk>/status/", DailyCollaborationStatusView.as_view(), name="daily-collaboration-status"),
    path("customers/<uuid:pk>/matches/weak/", WeakCustomerMatchesView.as_view(), name="weak-customer-matches"),
    path("property-files/<uuid:pk>/matches/weak/", WeakPropertyFileMatchesView.as_view(), name="weak-property-file-matches"),
    path("match-recommendations/<uuid:pk>/collaboration-request/", RecommendationCollaborationView.as_view(), name="recommendation-collaboration"),
    path("collaboration-requests/from-live-match/", LiveCollaborationView.as_view(), name="live-collaboration"),
    path("collaboration-requests/<uuid:pk>/", CollaborationDetailView.as_view(), name="collaboration-detail"),
    path("collaboration-requests/<uuid:pk>/status/", CollaborationStatusView.as_view(), name="collaboration-status"),
    path("customers/<uuid:pk>/matches/", CustomerMatchesView.as_view(), name="customer-matches"),
    path("property-files/<uuid:pk>/matches/", PropertyFileMatchesView.as_view(), name="property-file-matches"),
    path("matching-profile/", MatchingProfileView.as_view(), name="matching-profile"),
    path("matching-profile/reset/", MatchingProfileResetView.as_view(), name="matching-profile-reset"),
]
