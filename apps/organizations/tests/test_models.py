import pytest

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

