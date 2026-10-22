import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import NOT_PROVIDED

from apps.accounts.models import User
from apps.organizations.models import Workspace
from apps.organizations.services import create_workspace
from apps.ranges.models import Range

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("mode", Workspace.WorkspaceMode.values)
@pytest.mark.parametrize("purchaser", Workspace.CustomerType.values)
def test_product_mode_independent_of_purchaser(mode, purchaser):
    row = Workspace(name="مجموعه", slug="independent", customer_type=purchaser, workspace_mode=mode)
    row.full_clean()
    row.save()
    row.refresh_from_db()
    assert (row.workspace_mode, row.customer_type) == (mode, purchaser)


def test_unresolved_legacy_state_has_no_product_default():
    field = Workspace._meta.get_field("workspace_mode")
    assert field.default is NOT_PROVIDED
    assert set(Workspace.WorkspaceMode.values) == {"personal", "team"}
    row = Workspace(name="مجموعه", slug="unresolved", customer_type="consultant")
    row.full_clean()
    row.save()
    row.refresh_from_db()
    assert row.workspace_mode is None


@pytest.mark.parametrize("mode", ["", "solo", "invalid"])
def test_invalid_mode_rejected_by_model_and_database(mode):
    row = Workspace(name="مجموعه", slug="invalid", customer_type="consultant", workspace_mode=mode)
    with pytest.raises(ValidationError):
        row.full_clean()
    with pytest.raises(IntegrityError), transaction.atomic():
        row.save()


@pytest.mark.parametrize("mode", Workspace.WorkspaceMode.values)
def test_explicit_provisioning_without_topology_side_effects(mode):
    before = (User.objects.count(), Range.objects.count())
    row = create_workspace(name="مجموعه", slug="explicit", customer_type="consultant", workspace_mode=mode)
    row.refresh_from_db()
    assert row.workspace_mode == mode
    assert row.customer_type == "consultant"
    assert row.region_mode is None
    assert (User.objects.count(), Range.objects.count()) == before


def test_provisioning_requires_mode_argument():
    with pytest.raises(TypeError):
        create_workspace(name="مجموعه", slug="missing", customer_type="consultant")
    assert not Workspace.objects.exists()


@pytest.mark.parametrize("mode", [None, "", "agency_manager"])
def test_provisioning_rejects_unresolved_or_invalid_mode(mode):
    with pytest.raises(ValidationError):
        create_workspace(name="مجموعه", slug="invalid", customer_type="consultant", workspace_mode=mode)
    assert not Workspace.objects.exists()


def test_provisioning_validates_purchaser_independently():
    with pytest.raises(TypeError):
        create_workspace(name="مجموعه", slug="missing", workspace_mode="team")
    with pytest.raises(ValidationError):
        create_workspace(name="مجموعه", slug="invalid", customer_type="team", workspace_mode="team")
    assert not Workspace.objects.exists()


def test_unrelated_updates_preserve_explicit_and_unresolved_modes():
    for index, mode in enumerate((None, "personal", "team")):
        row = Workspace.objects.create(
            name="مجموعه", slug=f"update-{index}", customer_type="agency_manager", workspace_mode=mode,
        )
        row.name = "نام جدید"
        row.customer_type = "consultant"
        row.region_mode = "divar"
        row.save(update_fields=["name", "customer_type", "region_mode"])
        row.refresh_from_db()
        assert row.workspace_mode == mode
        assert row.customer_type == "consultant"
        assert row.region_mode == "divar"
