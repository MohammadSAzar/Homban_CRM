import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.organizations.models import Workspace


@pytest.mark.django_db
def test_workspace_can_be_created():
    workspace = Workspace.objects.create(
        name="املاک تست",
        slug="test-agency",
        customer_type="agency_manager",
    )

    assert workspace.name == "املاک تست"
    assert workspace.slug == "test-agency"
    assert workspace.is_active is True
    assert workspace.region_mode is None


@pytest.mark.django_db
@pytest.mark.parametrize("mode", [None, "custom", "divar"])
def test_region_setup_states(mode):
    workspace = Workspace(name="مجموعه", slug="mode-test", customer_type="consultant", region_mode=mode)
    workspace.full_clean()
    workspace.save()
    workspace.refresh_from_db()
    assert workspace.region_mode == mode


@pytest.mark.django_db
@pytest.mark.parametrize("mode", ["none", "", "invalid"])
def test_invalid_region_mode_rejected_by_validation_and_database(mode):
    workspace = Workspace(name="مجموعه", slug="invalid-mode", customer_type="consultant", region_mode=mode)
    with pytest.raises(ValidationError):
        workspace.full_clean()
    with pytest.raises(IntegrityError), transaction.atomic():
        workspace.save()

