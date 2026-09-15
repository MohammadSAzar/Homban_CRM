from django.urls import path

from .views import CurrentUserView, CustomerLoginView, CustomerRefreshView

app_name = "accounts"

urlpatterns = [
    path("login/", CustomerLoginView.as_view(), name="login"),
    path("refresh/", CustomerRefreshView.as_view(), name="refresh"),
    path("me/", CurrentUserView.as_view(), name="me"),
]
