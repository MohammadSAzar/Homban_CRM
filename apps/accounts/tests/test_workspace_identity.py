import uuid

import pytest
from django.contrib.auth import authenticate
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import RequestFactory
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from apps.accounts.models import User
from apps.accounts.workspace_context import resolve_customer_workspace
from apps.organizations.models import Workspace

pytestmark = pytest.mark.django_db


@pytest.fixture
def workspaces(settings):
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.CUSTOMER_ALLOW_WORKSPACE_HEADER = True
    settings.CUSTOMER_WORKSPACE_HOSTS = {}
    return [
        Workspace.objects.create(name=slug, slug=slug, customer_type="consultant")
        for slug in ("agency-a", "agency-b")
    ]


@pytest.fixture
def namesakes(workspaces):
    return [
        User.objects.create_user(username="ali", password=password, workspace=workspace, role="consultant")
        for workspace, password in zip(workspaces, ("Password-A!", "Password-B!"))
    ]


def test_same_username_in_two_workspaces(namesakes):
    assert namesakes[0].pk != namesakes[1].pk
    assert User.objects.filter(username="ali").count() == 2


def test_database_rejects_duplicate_in_one_workspace(namesakes):
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create(username="ali", workspace=namesakes[0].workspace, role="consultant")


def test_database_rejects_move_into_duplicate_identity(namesakes):
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=namesakes[1].pk).update(workspace=namesakes[0].workspace)


@pytest.mark.parametrize("index,password", [(0, "Password-A!"), (1, "Password-B!")])
def test_scoped_login_selects_correct_user(namesakes, index, password):
    user = namesakes[index]
    response = APIClient().post("/api/v1/auth/login/", {
        "username": "ali", "password": password,
    }, HTTP_X_WORKSPACE_SLUG=user.workspace.slug)
    assert response.status_code == 200
    assert AccessToken(response.data["access"])["user_id"] == str(user.pk)


@pytest.mark.parametrize("index,password", [(0, "Password-B!"), (1, "Password-A!")])
def test_other_workspace_password_does_not_authenticate(namesakes, index, password):
    response = APIClient().post("/api/v1/auth/login/", {
        "username": "ali", "password": password,
    }, HTTP_X_WORKSPACE_SLUG=namesakes[index].workspace.slug)
    assert response.status_code == 401


def test_username_exists_only_in_other_workspace(workspaces):
    User.objects.create_user(username="ali", password="Password-A!", workspace=workspaces[0])
    response = APIClient().post("/api/v1/auth/login/", {
        "username": "ali", "password": "Password-A!",
    }, HTTP_X_WORKSPACE_SLUG=workspaces[1].slug)
    assert response.status_code == 401


def test_body_workspace_id_cannot_choose_tenant(namesakes):
    response = APIClient().post("/api/v1/auth/login/", {
        "username": "ali", "password": "Password-A!", "workspace_id": str(namesakes[0].workspace_id),
    })
    assert response.status_code == 401


def test_body_workspace_id_cannot_override_context(namesakes):
    response = APIClient().post("/api/v1/auth/login/", {
        "username": "ali", "password": "Password-A!", "workspace_id": str(namesakes[0].workspace_id),
    }, HTTP_X_WORKSPACE_SLUG=namesakes[1].workspace.slug)
    assert response.status_code == 401


def test_null_workspace_names_may_repeat_but_customer_access_is_denied(workspaces):
    users = [User.objects.create_user(username="ali", password="Internal!", is_staff=True) for _ in range(2)]
    assert users[0].pk != users[1].pk
    client = APIClient()
    assert client.post("/api/v1/auth/login/", {"username": "ali", "password": "Internal!"},
                       HTTP_X_WORKSPACE_SLUG=workspaces[0].slug).status_code == 401
    refresh = RefreshToken.for_user(users[0])
    client.credentials(HTTP_AUTHORIZATION="Bearer " + str(refresh.access_token))
    assert client.get("/api/v1/auth/me/").status_code == 401
    assert client.post("/api/v1/auth/refresh/", {"refresh": str(refresh)}).status_code == 401
    assert authenticate(username="ali", password="Internal!") is None
    assert authenticate(username=str(users[0].pk), password="Internal!") == users[0]


def test_customer_uuid_cannot_bypass_workspace_login(namesakes):
    assert authenticate(username=str(namesakes[0].pk), password="Password-A!") is None


@pytest.mark.parametrize("active,staff,password", [(False, True, "Internal!"), (True, False, "Internal!"), (True, True, "wrong")])
def test_internal_uuid_auth_requires_active_staff_and_password(workspaces, active, staff, password):
    user = User.objects.create_user(username="staff", password="Internal!", is_staff=staff, is_active=active)
    assert authenticate(id=str(user.pk), password=password) is None


def test_createsuperuser_and_admin_uuid_login(workspaces, monkeypatch):
    identity = uuid.uuid4()
    monkeypatch.setenv("DJANGO_SUPERUSER_PASSWORD", "Internal!")
    call_command("createsuperuser", id=str(identity), username="operator", email="test@example.invalid", interactive=False, verbosity=0)
    user = User.objects.get(pk=identity)
    assert user.is_superuser and user.workspace_id is None
    client = APIClient()
    response = client.post("/admin/login/", {"username": identity.hex, "password": "Internal!", "next": "/admin/"})
    assert response.status_code == 302
    assert client.get("/admin/").status_code == 200


def test_development_header_resolves_workspace(workspaces):
    request = RequestFactory().post("/", HTTP_X_WORKSPACE_SLUG=workspaces[0].slug)
    assert resolve_customer_workspace(request) == workspaces[0]


@pytest.mark.parametrize("slug", [None, "", "missing", "x" * 101])
def test_missing_or_unknown_workspace_fails_closed(workspaces, slug):
    headers = {} if slug is None else {"HTTP_X_WORKSPACE_SLUG": slug}
    with pytest.raises(AuthenticationFailed):
        resolve_customer_workspace(RequestFactory().post("/", **headers))


def test_inactive_workspace_resolution_rejected(workspaces):
    Workspace.objects.filter(pk=workspaces[0].pk).update(is_active=False)
    with pytest.raises(AuthenticationFailed):
        resolve_customer_workspace(RequestFactory().post("/", HTTP_X_WORKSPACE_SLUG=workspaces[0].slug))


def test_header_disabled_by_deployment_setting(workspaces, settings):
    settings.CUSTOMER_ALLOW_WORKSPACE_HEADER = False
    with pytest.raises(AuthenticationFailed):
        resolve_customer_workspace(RequestFactory().post("/", HTTP_X_WORKSPACE_SLUG=workspaces[0].slug))


def test_configured_host_resolves_without_header(workspaces, settings):
    settings.ALLOWED_HOSTS = ["agency.example.test"]
    settings.CUSTOMER_WORKSPACE_HOSTS = {"agency.example.test": workspaces[0].slug}
    settings.CUSTOMER_ALLOW_WORKSPACE_HEADER = False
    request = RequestFactory().post("/", HTTP_HOST="AGENCY.EXAMPLE.TEST:8000")
    assert resolve_customer_workspace(request) == workspaces[0]


def test_host_context_login(namesakes, settings):
    settings.ALLOWED_HOSTS = ["agency.example.test"]
    settings.CUSTOMER_WORKSPACE_HOSTS = {"agency.example.test": namesakes[0].workspace.slug}
    settings.CUSTOMER_ALLOW_WORKSPACE_HEADER = False
    response = APIClient().post("/api/v1/auth/login/", {"username": "ali", "password": "Password-A!"}, HTTP_HOST="agency.example.test")
    assert response.status_code == 200
    assert AccessToken(response.data["access"])["user_id"] == str(namesakes[0].pk)


def test_host_header_conflict_rejected(workspaces, settings):
    settings.CUSTOMER_WORKSPACE_HOSTS = {"testserver": workspaces[0].slug}
    with pytest.raises(AuthenticationFailed):
        resolve_customer_workspace(RequestFactory().post("/", HTTP_X_WORKSPACE_SLUG=workspaces[1].slug))


def test_unknown_host_cannot_fall_back_to_header(workspaces, settings):
    settings.CUSTOMER_WORKSPACE_HOSTS = {"agency.example.test": workspaces[0].slug}
    with pytest.raises(AuthenticationFailed):
        resolve_customer_workspace(RequestFactory().post("/", HTTP_X_WORKSPACE_SLUG=workspaces[0].slug))


def test_untrusted_host_rejected(workspaces, settings):
    settings.ALLOWED_HOSTS = ["localhost"]
    response = APIClient().post("/api/v1/auth/login/", {"username": "ali", "password": "x"},
                               HTTP_HOST="untrusted.example", HTTP_X_WORKSPACE_SLUG=workspaces[0].slug)
    assert response.status_code == 400
