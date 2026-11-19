from unittest.mock import patch
import pytest
from rest_framework.exceptions import PermissionDenied
from apps.accounts.models import User
from apps.organizations.models import Workspace
from apps.locations.services import set_region_mode
from apps.locations.tests.test_api import world, client_for, MODE

pytestmark = pytest.mark.django_db

@pytest.mark.parametrize('mode', ['personal', None])
def test_configuration_product_denial(world, mode):
    Workspace.objects.filter(pk=world.workspace.pk).update(workspace_mode=mode, customer_type='agency_manager')
    User.objects.filter(pk=world.actor.pk).update(is_workspace_owner=True, is_staff=True, is_superuser=True)
    assert client_for(world.actor).patch(MODE, {'region_mode': 'custom'}, format='json').status_code == 403
    # Isolate product entitlement from C1 denial.
    with patch('apps.locations.services.require_location_manager'):
        with pytest.raises(PermissionDenied):
            set_region_mode(actor=world.actor, data={'region_mode': 'custom'})
    world.workspace.refresh_from_db()
    assert world.workspace.region_mode is None
    assert client_for(world.actor).get(MODE).status_code == 200

def test_exact_capability_and_authority(world):
    with patch('apps.locations.policies.resolve_workspace_capability') as resolve:
        resolve.return_value.allowed = True
        set_region_mode(actor=world.actor, data={'region_mode': 'custom'})
        resolve.assert_called_once()
        assert resolve.call_args.args[1] == 'workspace_administration'
        assert resolve.call_args.args[0].pk == world.workspace.pk
    User.objects.filter(pk=world.actor.pk).update(role='consultant')
    assert client_for(world.actor).patch(MODE, {'region_mode': 'divar'}, format='json').status_code == 403
    world.workspace.refresh_from_db()
    world.foreign_workspace.refresh_from_db()
    assert world.workspace.region_mode == 'custom'
    assert world.foreign_workspace.region_mode is None
