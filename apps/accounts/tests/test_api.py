from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

from apps.accounts.models import User
from apps.organizations.models import Workspace

pytestmark = pytest.mark.django_db


@pytest.fixture
def client(settings):
    settings.CUSTOMER_ALLOW_WORKSPACE_HEADER = True
    return APIClient(HTTP_X_WORKSPACE_SLUG="auth-test")


@pytest.fixture
def customer():
    workspace = Workspace.objects.create(
        name="املاک نمونه", slug="auth-test", customer_type="agency_manager"
    )
    return User.objects.create_user(
        username="customer", password="Test-password-123!", workspace=workspace,
        role="consultant", first_name="علی", last_name="احمدی",
        phone_number="09120000000", is_workspace_owner=True,
    )


def login(client, customer):
    return client.post(reverse("accounts:login"), {
        "username": customer.username, "password": "Test-password-123!",
    }, format="json")


def test_login_returns_identity_tokens(client, customer, monkeypatch):
    # Refresh/access creation must share a clock for exact lifetime assertions.
    issued_at = timezone.now()
    monkeypatch.setattr("rest_framework_simplejwt.tokens.aware_utcnow", lambda: issued_at)
    response = login(client, customer)
    assert response.status_code == 200
    assert set(response.data) == {"access", "refresh"}
    for token_class, name, lifetime in (
        (AccessToken, "access", 300), (RefreshToken, "refresh", 86400),
    ):
        token = token_class(response.data[name])
        assert token["user_id"] == str(customer.pk)
        assert set(token.payload) == {"token_type", "exp", "iat", "jti", "user_id"}
        assert token["exp"] - token["iat"] == lifetime


@pytest.mark.parametrize("username,password", [
    ("customer", "wrong"), ("unknown", "Test-password-123!"),
])
def test_bad_credentials(client, customer, username, password):
    response = client.post(reverse("accounts:login"), {
        "username": username, "password": password,
    })
    assert response.status_code == 401
    assert "access" not in response.data
    assert not OutstandingToken.objects.exists()


def disable_customer(customer, state):
    if state == "inactive_user":
        User.objects.filter(pk=customer.pk).update(is_active=False)
    elif state == "inactive_workspace":
        Workspace.objects.filter(pk=customer.workspace_id).update(is_active=False)
    elif state == "no_workspace":
        User.objects.filter(pk=customer.pk).update(workspace=None)
    else:
        customer.delete()


@pytest.mark.parametrize("state", ["inactive_user", "inactive_workspace", "no_workspace"])
def test_login_requires_active_customer_membership(client, customer, state):
    disable_customer(customer, state)
    assert login(client, customer).status_code == 401
    assert not OutstandingToken.objects.exists()


@pytest.mark.parametrize("state", [
    "inactive_user", "inactive_workspace", "no_workspace", "deleted_user",
])
def test_existing_tokens_obey_current_membership(client, customer, state):
    tokens = login(client, customer).data
    disable_customer(customer, state)
    client.credentials(HTTP_AUTHORIZATION="Bearer " + tokens["access"])
    assert client.get(reverse("accounts:me")).status_code == 401
    assert client.post(reverse("accounts:refresh"), {
        "refresh": tokens["refresh"],
    }).status_code == 401


def test_me_returns_only_current_customer_data(client, customer):
    other_workspace = Workspace.objects.create(
        name="مجموعه دیگر", slug="other", customer_type="consultant",
    )
    other = User.objects.create_user(
        username="other", workspace=other_workspace, role="consultant",
    )
    tokens = login(client, customer).data
    client.credentials(HTTP_AUTHORIZATION="Bearer " + tokens["access"])
    response = client.get(reverse("accounts:me"), {"id": str(other.pk), "workspace_id": str(other_workspace.pk)})
    assert response.status_code == 200
    assert response.json() == {
        "id": str(customer.pk), "username": "customer", "first_name": "علی",
        "last_name": "احمدی", "phone_number": "09120000000", "role": "consultant",
        "role_display": "مشاور", "is_workspace_owner": True,
        "workspace_id": str(customer.workspace_id), "workspace_name": "املاک نمونه",
    }
    assert customer.password not in response.content.decode()


def test_me_uses_current_database_role_and_workspace(client, customer):
    tokens = login(client, customer).data
    workspace = Workspace.objects.create(name="مجموعه جدید", slug="new", customer_type="consultant")
    User.objects.filter(pk=customer.pk).update(role="secretary", workspace=workspace, is_workspace_owner=False)
    client.credentials(HTTP_AUTHORIZATION="Bearer " + tokens["access"])
    response = client.get(reverse("accounts:me"))
    assert response.status_code == 200
    assert response.data["role"] == "secretary"
    assert response.data["role_display"] == "منشی"
    assert response.data["workspace_id"] == str(workspace.pk)
    assert response.data["is_workspace_owner"] is False


def test_anonymous_me_is_denied(client):
    assert client.get(reverse("accounts:me")).status_code == 401


def test_refresh_rotates_and_rejects_reuse(client, customer):
    tokens = login(client, customer).data
    # Public token endpoints must ignore an expired/malformed Authorization header.
    client.credentials(HTTP_AUTHORIZATION="Bearer invalid")
    assert login(client, customer).status_code == 200
    response = client.post(reverse("accounts:refresh"), {"refresh": tokens["refresh"]})
    assert response.status_code == 200
    assert set(response.data) == {"access", "refresh"}
    assert response.data["refresh"] != tokens["refresh"]
    assert client.post(reverse("accounts:refresh"), {"refresh": tokens["refresh"]}).status_code == 401
    client.credentials(HTTP_AUTHORIZATION="Bearer " + response.data["access"])
    assert client.get(reverse("accounts:me")).status_code == 200
    assert client.post(reverse("accounts:refresh"), {"refresh": response.data["refresh"]}).status_code == 200


@pytest.mark.parametrize("kind", ["invalid", "expired", "access", "missing_id", "bad_id"])
def test_invalid_refresh_is_denied(client, customer, kind):
    token = RefreshToken.for_user(customer)
    if kind == "expired":
        token.set_exp(lifetime=timedelta(seconds=-1))
    elif kind == "missing_id":
        del token["user_id"]
    elif kind == "bad_id":
        token["user_id"] = "not-a-uuid"
    raw = "invalid" if kind == "invalid" else str(token.access_token if kind == "access" else token)
    assert client.post(reverse("accounts:refresh"), {"refresh": raw}).status_code == 401


@pytest.mark.parametrize("kind", ["invalid", "expired", "refresh", "missing_id", "bad_id"])
def test_invalid_access_is_denied(client, customer, kind):
    token = AccessToken.for_user(customer)
    if kind == "expired":
        token.set_exp(lifetime=timedelta(seconds=-1))
    elif kind == "missing_id":
        del token["user_id"]
    elif kind == "bad_id":
        token["user_id"] = "not-a-uuid"
    raw = "invalid" if kind == "invalid" else str(RefreshToken.for_user(customer) if kind == "refresh" else token)
    client.credentials(HTTP_AUTHORIZATION="Bearer " + raw)
    assert client.get(reverse("accounts:me")).status_code == 401


@pytest.mark.parametrize("endpoint", ["login", "refresh"])
def test_missing_fields_are_rejected(client, endpoint):
    assert client.post(reverse("accounts:" + endpoint), {}, format="json").status_code == 400
