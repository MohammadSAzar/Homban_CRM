from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest
from django.core.exceptions import ValidationError as ModelValidationError
from django.db import IntegrityError, close_old_connections, transaction
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import User
from apps.organizations.models import Workspace
from apps.matching.models import MatchingProfile, SETTING_FIELDS, WEIGHT_FIELDS
from apps.matching.services import own_profile

pytestmark = pytest.mark.django_db
BASE = "/api/v1/matching-profile/"
DEFAULTS = dict(area=25, bedrooms=15, building_age=10, region=10, parking=4,
                elevator=3, storage=2, balcony=1, minimum_score=50,
                sale_budget_lower_ratio=Decimal("0.80"), sale_budget_upper_ratio=Decimal("1.20"),
                rent_per_100m_deposit=3000000)
INVALID = [{field: -1} for field in WEIGHT_FIELDS] + [
    {field: 0 for field in WEIGHT_FIELDS}, {"minimum_score": -1}, {"minimum_score": 101},
    {"sale_budget_lower_ratio": 0}, {"sale_budget_lower_ratio": -1}, {"sale_budget_lower_ratio": "1.01"},
    {"sale_budget_upper_ratio": "0.99"}, {"sale_budget_lower_ratio": "1.1", "sale_budget_upper_ratio": "1.05"},
    {"rent_per_100m_deposit": 0}, {"rent_per_100m_deposit": -1},
]


@pytest.fixture
def user():
    workspace = Workspace.objects.create(name="مجموعه", slug="matching", customer_type="consultant")
    return User.objects.create_user(username="ali", workspace=workspace, role="consultant")


def client_for(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + str(AccessToken.for_user(user)))
    return client


def assert_defaults(data):
    assert set(data) == set(DEFAULTS)
    assert {name: Decimal(str(value)) for name, value in data.items()} == DEFAULTS


def test_lazy_defaults_repeated_get_preserves_profile(user):
    client = client_for(user)
    assert not MatchingProfile.objects.exists()
    response = client.get(BASE)
    assert response.status_code == 200
    assert_defaults(response.data)
    profile = MatchingProfile.objects.get(user=user)
    assert profile.created_at.tzinfo and profile.updated_at.tzinfo
    response = client.patch(BASE, {"area": "12.25"}, format="json")
    assert response.status_code == 200 and response.data["area"] == "12.25"
    assert response.data["bedrooms"] == "15.00"
    assert client.get(BASE).data == response.data
    assert MatchingProfile.objects.count() == 1
    assert MatchingProfile.objects.get(user=user).pk == profile.pk


@pytest.mark.parametrize("role", User.Role.values)
def test_all_customer_roles_have_only_personal_settings(user, role):
    user.role = role
    user.is_workspace_owner = user.is_staff = user.is_superuser = True
    user.save()
    other = User.objects.create_user(username="other", workspace=user.workspace, role="consultant")
    foreign_workspace = Workspace.objects.create(name="دیگر", slug="matching-other", customer_type="consultant")
    foreign = User.objects.create_user(username="ali", workspace=foreign_workspace, role="consultant")
    for target in (other, foreign):
        profile = own_profile(actor=target, data={"area": 99})
        client = client_for(user)
        assert client.get(BASE, {"user": str(target.pk), "workspace": str(foreign_workspace.pk)}).data["area"] == "25.00"
        assert client.patch(BASE, {"area": 26}, format="json").status_code == 200
        assert client.post(BASE + "reset/", {}, format="json").status_code == 200
        for suffix in (str(target.pk), str(profile.pk)):
            assert client.get(BASE + suffix + "/").status_code == 404
            assert client.patch(BASE + suffix + "/", {"area": 1}, format="json").status_code == 404
        profile.refresh_from_db()
        assert profile.area == 99
    assert MatchingProfile.objects.count() == 3


@pytest.mark.parametrize("fields", INVALID)
def test_invalid_settings_model_database_api_rollback(user, fields):
    with pytest.raises(ModelValidationError):
        MatchingProfile.objects.create(user=user, **fields)
    profile = MatchingProfile.objects.create(user=user)
    with pytest.raises(IntegrityError), transaction.atomic():
        MatchingProfile.objects.filter(pk=profile.pk).update(**fields)
    response = client_for(user).patch(BASE, fields, format="json")
    assert response.status_code == 400, response.data
    profile.refresh_from_db()
    assert_defaults({name: getattr(profile, name) for name in SETTING_FIELDS})


def test_valid_custom_and_reset_exact_defaults(user):
    client = client_for(user)
    custom = {name: 0 for name in WEIGHT_FIELDS}
    custom.update(area="0.25", minimum_score=0, sale_budget_lower_ratio="1", sale_budget_upper_ratio="2.5", rent_per_100m_deposit="0.01")
    response = client.patch(BASE, custom, format="json")
    assert response.status_code == 200, response.data
    assert response.data["area"] == "0.25"  # No normalization or sum-to-100 requirement.
    assert client.patch(BASE, {"minimum_score": 100}, format="json").status_code == 200
    pk = MatchingProfile.objects.get(user=user).pk
    response = client.post(BASE + "reset/", {}, format="json")
    assert response.status_code == 200
    assert_defaults(response.data)
    assert MatchingProfile.objects.get(user=user).pk == pk


@pytest.mark.parametrize("field", ["user", "user_id", "owner", "workspace", "workspace_id", "id", "created_at", "updated_at", "is_superuser", "budget_weight", "hard_parking", "veto_area"])
def test_protected_and_unimplemented_fields_rejected(user, field):
    client = client_for(user)
    for endpoint, method in ((BASE, client.patch), (BASE + "reset/", client.post)):
        assert method(endpoint, {field: "forbidden"}, format="json").status_code == 400
    assert not MatchingProfile.objects.exists()


@pytest.mark.parametrize("state", ["inactive_user", "inactive_workspace", "workspace_less"])
def test_current_auth_state_rejected_without_staff_bypass(user, state):
    user.is_staff = user.is_superuser = user.is_workspace_owner = True
    user.save()
    client = client_for(user)
    if state == "inactive_user":
        User.objects.filter(pk=user.pk).update(is_active=False)
    elif state == "inactive_workspace":
        Workspace.objects.filter(pk=user.workspace_id).update(is_active=False)
    else:
        User.objects.filter(pk=user.pk).update(workspace=None)
    for response in (client.get(BASE), client.patch(BASE, {"area": 2}, format="json"), client.post(BASE + "reset/", {}, format="json")):
        assert response.status_code == 401
    with pytest.raises(PermissionDenied):
        own_profile(actor=user, data={"area": 2})
    assert not MatchingProfile.objects.exists()


def test_anonymous_no_delete_list_or_create_endpoint(user):
    client = APIClient()
    assert client.get(BASE).status_code == 401
    assert client.patch(BASE, {}, format="json").status_code == 401
    assert client.post(BASE + "reset/", {}, format="json").status_code == 401
    client = client_for(user)
    assert client.delete(BASE).status_code == 405
    assert client.post(BASE, {}, format="json").status_code == 405
    assert client.put(BASE, {}, format="json").status_code == 405
    assert client.get(BASE + "reset/").status_code == 405
    assert "results" not in client.get(BASE).data
    assert client.post(BASE + "reset/", [], format="json").status_code == 400


def test_ownership_unique_immutable_and_partial_validation(user):
    profile = MatchingProfile.objects.create(user=user)
    with pytest.raises(ModelValidationError):
        MatchingProfile.objects.create(user=user)
    with pytest.raises(IntegrityError), transaction.atomic():
        MatchingProfile.objects.bulk_create([MatchingProfile(user=user)])
    other = User.objects.create_user(username="other", workspace=user.workspace, role="consultant")
    profile.user = other
    with pytest.raises(ModelValidationError):
        profile.save()
    profile.refresh_from_db()
    profile.sale_budget_lower_ratio = Decimal("2")
    profile.sale_budget_upper_ratio = Decimal("3")
    with pytest.raises(ModelValidationError):
        profile.save(update_fields=["sale_budget_lower_ratio"])
    profile.refresh_from_db()
    assert profile.sale_budget_lower_ratio == Decimal("0.8")
    internal = User.objects.create_user(username="internal", role="admin")
    with pytest.raises(ModelValidationError):
        MatchingProfile.objects.create(user=internal)


def test_invalid_first_patch_does_not_leave_default_profile(user):
    with pytest.raises(ValidationError):
        own_profile(actor=user, data={name: 0 for name in WEIGHT_FIELDS})
    assert not MatchingProfile.objects.exists()
    with pytest.raises(ValidationError):
        own_profile(actor=user, data={"user": user.pk})
    assert not MatchingProfile.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_lazy_creation_is_unique(user):
    barrier = Barrier(2)
    def read_profile():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return own_profile(actor=user).pk
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: read_profile(), range(2)))
    assert ids[0] == ids[1]
    assert MatchingProfile.objects.filter(user=user).count() == 1
