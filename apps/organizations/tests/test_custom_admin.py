import uuid
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError as ModelValidationError
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.organizations.models import Workspace, CustomAdminGrant
from apps.organizations.custom_admin_catalog import AdminPermission
from apps.organizations.custom_admin_policy import has_custom_admin_permission
from apps.organizations.tests.test_authority import setup

pytestmark = pytest.mark.django_db
BASE = '/api/v1/custom-admin/'


def client(actor):
    api = APIClient()
    api.force_authenticate(actor)
    return api


@pytest.fixture
def world(setup):
    ws, user, team = setup
    authority = user()
    team(authority)
    target = user('consultant')
    return ws, user, team, authority, target


def grant(actor, target, permission='manage_users', **extra):
    return client(actor).post(BASE+'grants/', {'user_id': str(target.pk), 'permission': permission, **extra}, format='json')


@pytest.mark.parametrize('multi', [False, True])
def test_canonical_authority_grant_list_revoke(world, multi):
    ws, user, team, authority, target = world
    if multi:
        team(user())
        authority = user('agency_manager')
    response = grant(authority, target)
    assert response.status_code == 201, response.data
    row = CustomAdminGrant.objects.get()
    assert row.granted_by_id == authority.pk and row.workspace_id == ws.pk
    assert set(response.data) == {'id', 'user', 'permission', 'permission_display', 'created_at'}
    assert set(response.data['user']) == {'id', 'username', 'first_name', 'last_name'}
    assert len(client(authority).get(BASE+'grants/').data) == 1
    assert has_custom_admin_permission(authority, 'manage_ranges')
    assert has_custom_admin_permission(target, 'manage_users')
    assert not has_custom_admin_permission(target, 'manage_ranges')
    assert grant(authority, target).status_code == 400
    assert CustomAdminGrant.objects.count() == 1
    assert client(authority).delete(BASE+f'grants/{row.pk}/').status_code == 204
    assert not has_custom_admin_permission(target, 'manage_users')


@pytest.mark.parametrize('mode', ['personal', None])
def test_mode_and_privilege_flags_fail_closed(world, mode):
    ws, user, team, authority, target = world
    ws.workspace_mode = mode
    ws.customer_type = 'agency_manager'
    ws.save()
    authority.is_workspace_owner = authority.is_staff = authority.is_superuser = True
    authority.save()
    api = client(authority)
    assert api.get(BASE+'permissions/').status_code == 403
    assert api.get(BASE+'grants/').status_code == 403
    assert grant(authority, target).status_code == 403
    assert api.delete(BASE+f'grants/{uuid.uuid4()}/').status_code == 403
    assert not has_custom_admin_permission(authority, 'manage_users')


@pytest.mark.parametrize('role', User.Role.values)
def test_non_authority_cannot_manage_even_with_flags_or_grant(world, role):
    ws, user, team, authority, target = world
    target.role = role
    target.is_workspace_owner = target.is_staff = target.is_superuser = True
    target.save()
    assert grant(authority, target).status_code == 201
    assert has_custom_admin_permission(target, 'manage_users')
    assert client(target).get(BASE+'grants/').status_code == 403
    assert grant(target, authority).status_code == 403
    row = CustomAdminGrant.objects.get()
    assert client(target).delete(BASE+f'grants/{row.pk}/').status_code == 403


def test_multi_local_manager_and_invalid_topology(world):
    ws, user, team, authority, target = world
    team(user())
    assert grant(authority, target).status_code == 403  # no Agency authority
    agency = user('agency_manager')
    assert grant(authority, target).status_code == 403  # local RM, not global authority
    assert grant(agency, target).status_code == 201
    agency.is_active = False
    agency.save()
    assert not has_custom_admin_permission(target, 'manage_users')


@pytest.mark.parametrize('field', ['workspace_id', 'granted_by', 'role', 'customer_type', 'is_workspace_owner'])
def test_server_fields_rejected(world, field):
    *_, authority, target = world
    assert grant(authority, target, **{field: str(uuid.uuid4())}).status_code == 400
    assert not CustomAdminGrant.objects.exists()


def test_catalog_unknown_and_exact_capability(world):
    ws, user, team, authority, target = world
    with patch('apps.organizations.custom_admin_policy.resolve_workspace_capability') as resolve:
        resolve.return_value.allowed = True
        data = client(target).get(BASE+'permissions/').data
        assert {r['permission'] for r in data} == set(AdminPermission.values)
        resolve.assert_called_once_with(ws, 'custom_admin_permissions')
    assert grant(authority, target, 'delegate_permissions').status_code == 400
    assert not has_custom_admin_permission(authority, 'unknown')
    assert not has_custom_admin_permission(None, 'manage_users')


def test_self_inactive_and_foreign_missing_targets(world):
    ws, user, team, authority, target = world
    assert grant(authority, authority).status_code == 400
    target.is_active = False
    target.save()
    assert grant(authority, target).status_code == 400
    other = Workspace.objects.create(name='other', slug='other', workspace_mode='team', customer_type='consultant')
    foreign = User.objects.create_user(username='foreign', workspace=other, role='consultant')
    foreign_result = grant(authority, foreign)
    foreign.pk = uuid.uuid4()
    missing_result = grant(authority, foreign)
    assert foreign_result.status_code == missing_result.status_code == 400
    assert foreign_result.data == missing_result.data


def test_foreign_grant_visibility_and_model_integrity(world):
    ws, user, team, authority, target = world
    other = Workspace.objects.create(name='other', slug='other', workspace_mode='team', customer_type='consultant')
    foreign = User.objects.create_user(username='foreign', workspace=other, role='consultant')
    row = CustomAdminGrant.objects.create(workspace=other, user=foreign, granted_by=foreign, permission='manage_users')
    assert client(authority).get(BASE+'grants/').data == []
    assert client(authority).delete(BASE+f'grants/{row.pk}/').status_code == 404
    assert not has_custom_admin_permission(target, 'manage_users')
    with pytest.raises(ModelValidationError):
        CustomAdminGrant.objects.create(workspace=ws, user=foreign, granted_by=authority, permission='manage_users')
    with pytest.raises(ModelValidationError):
        CustomAdminGrant.objects.create(workspace=ws, user=target, granted_by=foreign, permission='manage_users')


def test_helper_revalidates_activity_mode_membership_and_no_consumer_integration(world):
    ws, user, team, authority, target = world
    assert grant(authority, target).status_code == 201
    assert has_custom_admin_permission(target, 'manage_users')
    # The delegated consultant still cannot use F5 management in CA1.
    assert client(target).get('/api/v1/users/').status_code == 403
    User.objects.filter(pk=target.pk).update(is_active=False)
    assert not has_custom_admin_permission(target, 'manage_users')
    User.objects.filter(pk=target.pk).update(is_active=True)
    Workspace.objects.filter(pk=ws.pk).update(workspace_mode='personal')
    assert not has_custom_admin_permission(target, 'manage_users')
    Workspace.objects.filter(pk=ws.pk).update(workspace_mode='team', is_active=False)
    assert not has_custom_admin_permission(target, 'manage_users')
    Workspace.objects.filter(pk=ws.pk).update(is_active=True)
    User.objects.filter(pk=target.pk).update(workspace=None)
    assert not has_custom_admin_permission(target, 'manage_users')
    assert CustomAdminGrant.objects.count() == 1


def test_database_constraints_and_rollback(world):
    ws, user, team, authority, target = world
    assert grant(authority, target).status_code == 201
    for permission in ('manage_users', 'unknown'):
        with pytest.raises(IntegrityError), transaction.atomic():
            CustomAdminGrant.objects.bulk_create([CustomAdminGrant(workspace=ws, user=target,
                granted_by=authority, permission=permission)])
    with pytest.raises(RuntimeError), transaction.atomic():
        assert grant(authority, target, 'manage_ranges').status_code == 201
        raise RuntimeError('rollback')
    assert CustomAdminGrant.objects.count() == 1


def test_no_update_and_auth_required(world):
    *_, authority, target = world
    assert APIClient().get(BASE+'permissions/').status_code == 401
    assert grant(authority, target).status_code == 201
    row = CustomAdminGrant.objects.get()
    assert client(authority).patch(BASE+f'grants/{row.pk}/', {}, format='json').status_code == 405


def test_authority_handoff_preserves_grant_but_not_management_power(world):
    from apps.organizations.topology_transitions import replace_range_manager
    from apps.ranges.models import Range
    ws, user, team, authority, target = world
    assert grant(authority, target).status_code == 201
    incoming = user()
    replace_range_manager(workspace_id=ws.pk, range_id=Range.objects.get(workspace=ws).pk,
                          incoming_manager_id=incoming.pk)
    assert has_custom_admin_permission(target, 'manage_users')
    assert has_custom_admin_permission(incoming, 'manage_ranges')
    assert not has_custom_admin_permission(authority, 'manage_users')
    assert client(authority).get(BASE+'grants/').status_code == 403
    assert client(incoming).get(BASE+'grants/').status_code == 200


def test_foreign_grant_cannot_apply_even_if_inserted_outside_service(world):
    ws, user, team, authority, target = world
    other = Workspace.objects.create(name='other', slug='other', workspace_mode='team', customer_type='consultant')
    CustomAdminGrant.objects.bulk_create([CustomAdminGrant(workspace=other, user=target,
        granted_by=authority, permission='manage_users')])
    assert not has_custom_admin_permission(target, 'manage_users')
    assert client(authority).get(BASE+'grants/').data == []
