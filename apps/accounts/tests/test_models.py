import pytest

from apps.accounts.models import User
from apps.organizations.models import Workspace


@pytest.mark.django_db
def test_user_can_belong_to_workspace():
    workspace = Workspace.objects.create(
        name="املاک تست",
        slug="test-agency",
        customer_type="agency_manager",
    )

    user = User.objects.create_user(
        username="consultant1",
        password="StrongPass123!",
        workspace=workspace,
        role="consultant",
    )

    assert user.workspace == workspace
    assert user.role == "consultant"

