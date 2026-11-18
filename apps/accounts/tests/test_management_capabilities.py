"""Product eligibility is independent of C1 authority and D topology."""
import pytest
from rest_framework.exceptions import PermissionDenied
from apps.accounts.models import User
from apps.accounts.services import create_organizational_user
from apps.organizations.models import Workspace
from apps.ranges.services import save_range, assign_consultant
from apps.ranges.tests.test_api import world, client_for, detail, membership, URL

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize('mode', ['personal', None])
@pytest.mark.parametrize('actor_name', ['agency', 'manager', 'consultant'])
def test_management_capabilities_fail_closed(world, mode, actor_name):
    actor = getattr(world, actor_name)
    User.objects.filter(pk=actor.pk).update(is_workspace_owner=True, is_staff=True, is_superuser=True)
    Workspace.objects.filter(pk=world.workspace.pk).update(workspace_mode=mode, customer_type='agency_manager')
    client = client_for(actor)
    user_path = f'/api/v1/users/{world.consultant.pk}/'
    for path in ['/api/v1/users/', user_path, URL, detail(world.team)]:
        assert client.get(path).status_code == 403
    assert client.post('/api/v1/users/', {'username': 'blocked', 'role': 'consultant',
        'password': 'Strong-Password-879!'}, format='json').status_code == 403
    assert client.patch(user_path, {'is_active': False}, format='json').status_code == 403
    assert client.post(URL, {'name': 'blocked', 'is_active': False}, format='json').status_code == 403
    assert client.patch(detail(world.team), {'name': 'blocked'}, format='json').status_code == 403
    assert client.put(membership(world.team, world.unassigned), {}, format='json').status_code == 403
    assert client.post(URL + 'expand/', {}, format='json').status_code == 403
    assert client.post(URL + 'replace-agency-manager/', {}, format='json').status_code == 403
    # Direct customer service calls also enforce entitlement from persisted mode.
    with pytest.raises(PermissionDenied):
        save_range(actor=actor, data={'name': 'blocked', 'is_active': False})
    with pytest.raises(PermissionDenied):
        create_organizational_user(actor=actor, data={})
    with pytest.raises(PermissionDenied):
        assign_consultant(actor=actor, range_id=world.team.pk, user_id=world.unassigned.pk, data={})
    assert not User.objects.filter(username='blocked').exists()
    world.consultant.refresh_from_db()
    assert world.consultant.is_active
    world.team.refresh_from_db()
    assert world.team.name != 'blocked'


@pytest.mark.parametrize('topology', ['single', 'multi', 'invalid'])
def test_team_eligibility_does_not_replace_authority_or_local_scope(world, topology):
    from apps.ranges.models import Range
    if topology == 'single':
        Range.objects.filter(pk=world.other.pk).update(is_active=False)
        authority = world.manager
    else:
        authority = world.agency
    if topology == 'invalid':
        User.objects.filter(pk=world.agency.pk).update(is_active=False)
        assert client_for(world.manager).put(membership(world.team, world.unassigned), {}, format='json').status_code == 204
        assert client_for(world.manager).patch(detail(world.team), {'name': 'denied'}, format='json').status_code == 403
    else:
        assert client_for(authority).post(URL, {'name': 'prepared', 'is_active': False}, format='json').status_code == 201
    assert client_for(world.consultant).post(URL, {'name': 'denied', 'is_active': False}, format='json').status_code == 403
