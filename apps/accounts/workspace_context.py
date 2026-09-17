from django.conf import settings
from django.http.request import split_domain_port
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import AuthenticationFailed

from apps.organizations.models import Workspace


def resolve_customer_workspace(request):
    """Resolve a tenant from a trusted host mapping or the explicit dev fallback."""
    host, _port = split_domain_port(request.get_host().lower())
    mapping = settings.CUSTOMER_WORKSPACE_HOSTS
    header_slug = request.headers.get("X-Workspace-Slug")
    slug = mapping.get(host)
    if slug:
        if header_slug and header_slug != slug:
            raise AuthenticationFailed(_("اطلاعات مجموعه با آدرس درخواست مطابقت ندارد."))
    elif not mapping and settings.CUSTOMER_ALLOW_WORKSPACE_HEADER:
        slug = header_slug

    if not slug or len(slug) > 100:
        raise AuthenticationFailed(_("مجموعه معتبر مشخص نشده است."))
    try:
        return Workspace.objects.get(slug=slug, is_active=True)
    except Workspace.DoesNotExist:
        raise AuthenticationFailed(_("مجموعه معتبر مشخص نشده است.")) from None
