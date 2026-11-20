from django.urls import path
from .custom_admin_views import PermissionCatalogView, GrantListView, GrantDetailView, CustomAdminMeView

urlpatterns = [
    path("custom-admin/me/", CustomAdminMeView.as_view(), name="custom-admin-me"),
    path("custom-admin/permissions/", PermissionCatalogView.as_view(), name="custom-admin-permissions"),
    path("custom-admin/grants/", GrantListView.as_view(), name="custom-admin-grants"),
    path("custom-admin/grants/<uuid:pk>/", GrantDetailView.as_view(), name="custom-admin-grant"),
]
