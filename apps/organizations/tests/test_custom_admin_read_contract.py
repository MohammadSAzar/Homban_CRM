"""CA3 additive read contract; consumer authorization remains CA2's concern."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.organizations.models import Workspace, CustomAdminGrant
from apps.organizations.custom_admin_catalog import AdminPermission, permission_metadata
from apps.organizations.custom_admin_policy import custom_admin_current_state
from apps.organizations.tests.test_custom_admin import world, client, grant, BASE
from apps.organizations.tests.test_authority import setup

pytestmark = pytest.mark.django_db


def me(actor):
    return client(actor).get(BASE + "me/")


def test_catalog_additive_closed_metadata(world):
    *_, authority, target = world
    response = client(target).get(BASE + "permissions/")
    assert response.status_code == 200
    assert [row["id"] for row in response.data] == AdminPermission.values
    for row in response.data:
        assert set(row) == {"permission", "display", "id", "label", "status", "operational"}
        assert row["permission"] == row["id"]
        assert row["display"] == row["label"]
        assert row["label"]
        expected = row["id"] != "manage_users"
        assert row["operational"] is expected
        assert row["status"] == ("active" if expected else "reserved")
    assert permission_metadata("unknown") is None
    assert grant(authority, target, "unknown").status_code == 400


def test_authority_and_empty_delegation_are_distinct(world):
    *_, authority, target = world
    assert me(authority).data == {"is_authority": True, "granted_permissions": [], "operational_permissions": []}
    assert me(target).data == {"is_authority": False, "granted_permissions": [], "operational_permissions": []}


@pytest.mark.parametrize("permission", AdminPermission.values)
def test_actual_grants_and_reserved_status(world, permission):
    *_, authority, target = world
    assert grant(authority, target, permission).status_code == 201
    assert me(target).data == {
        "is_authority": False, "granted_permissions": [permission],
        "operational_permissions": [] if permission == "manage_users" else [permission],
    }
    assert client(target).get(BASE + "grants/").status_code == 403
    assert grant(target, authority, permission).status_code == 403
    if permission == "manage_users":
        assert client(target).get("/api/v1/users/").status_code == 403


def test_stable_order_and_immediate_revocation(world):
    *_, authority, target = world
    for permission in reversed(AdminPermission.values):
        assert grant(authority, target, permission).status_code == 201
    assert me(target).data["granted_permissions"] == AdminPermission.values
    assert me(target).data["operational_permissions"] == ["manage_ranges", "manage_workspace_settings"]
    row = CustomAdminGrant.objects.get(permission="manage_ranges")
    assert client(authority).delete(BASE + f"grants/{row.pk}/").status_code == 204
    assert me(target).data["granted_permissions"] == ["manage_users", "manage_workspace_settings"]


@pytest.mark.parametrize("mode", ["personal", None])
def test_mode_denial_despite_flags_and_stored_grants(world, mode):
    ws, _, _, authority, target = world
    grant(authority, target)
    User.objects.filter(pk=target.pk).update(is_staff=True, is_superuser=True, is_workspace_owner=True)
    Workspace.objects.filter(pk=ws.pk).update(workspace_mode=mode, customer_type="agency_manager")
    assert me(target).status_code == 403
    assert CustomAdminGrant.objects.count() == 1


@pytest.mark.parametrize("inactive", ["actor", "workspace"])
def test_reloads_activity(world, inactive):
    ws, _, _, authority, target = world
    grant(authority, target)
    if inactive == "actor":
        User.objects.filter(pk=target.pk).update(is_active=False)
    else:
        Workspace.objects.filter(pk=ws.pk).update(is_active=False)
    assert me(target).status_code == 403


def test_invalid_authority_fails_closed(world):
    ws, user, team, authority, target = world
    grant(authority, target)
    team(user())  # multi-team without Agency authority
    assert me(authority).status_code == 403
    assert me(target).status_code == 403


def test_capability_denial_and_exact_identifier(world):
    *_, target = world
    with patch("apps.organizations.custom_admin_policy.resolve_workspace_capability", return_value=SimpleNamespace(allowed=False)) as resolve:
        assert me(target).status_code == 403
        resolve.assert_called_once()
        assert resolve.call_args.args[1] == "custom_admin_permissions"


def test_moved_user_and_malformed_links_are_excluded(world):
    ws, user, team, authority, target = world
    grant(authority, target)
    other = Workspace.objects.create(name="Other", slug="other-read", workspace_mode="team")
    other_authority = User.objects.create_user(username="other-authority", workspace=other, role="range_manager")
    from apps.ranges.models import Range
    Range.objects.create(workspace=other, manager=other_authority, name="Other range")
    User.objects.filter(pk=target.pk).update(workspace=other)
    assert me(target).data == {"is_authority": False, "granted_permissions": [], "operational_permissions": []}
    assert CustomAdminGrant.objects.count() == 1
    # Return to original tenant, but corrupt grantor tenancy independently.
    User.objects.filter(pk=target.pk).update(workspace=ws)
    CustomAdminGrant.objects.update(granted_by=other_authority)
    assert me(target).data["granted_permissions"] == []
    CustomAdminGrant.objects.update(granted_by=authority, workspace=other)
    assert me(target).data["granted_permissions"] == []


def test_unknown_legacy_permission_is_not_reported(world):
    *_, target = world
    # DB rejects unknown values; simulate the read result without disabling constraints.
    with patch("apps.organizations.custom_admin_policy.CustomAdminGrant.objects.filter") as rows:
        rows.return_value.values_list.return_value = ["unknown", "manage_users"]
        assert custom_admin_current_state(target)["granted_permissions"] == ["manage_users"]
        assert custom_admin_current_state(target)["operational_permissions"] == []


def test_grant_read_shape_labels_and_tenant_scope(world):
    ws, _, _, authority, target = world
    grant(authority, target, "manage_ranges")
    other = Workspace.objects.create(name="Other", slug="foreign-grants", workspace_mode="team")
    foreign = User.objects.create_user(username="foreign-grants", workspace=other, role="consultant")
    CustomAdminGrant.objects.create(workspace=other, user=foreign, granted_by=foreign, permission="manage_users")
    response = client(authority).get(BASE + "grants/")
    assert response.status_code == 200
    assert len(response.data) == 1
    row = response.data[0]
    assert set(row) == {"id", "user", "permission", "permission_display", "created_at"}
    assert set(row["user"]) == {"id", "username", "first_name", "last_name"}
    assert row["user"]["id"] == str(target.pk)
    assert row["permission_display"] == permission_metadata(row["permission"])["label"]


def test_me_read_only_bounded_and_no_write_methods(world):
    *_, authority, target = world
    with CaptureQueriesContext(connection) as before:
        assert me(target).status_code == 200
    for permission in AdminPermission.values:
        grant(authority, target, permission)
    with CaptureQueriesContext(connection) as after:
        assert me(target).status_code == 200
    assert len(after) == len(before)
    for query in after:
        assert not query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    for method in ("post", "patch", "put", "delete"):
        assert getattr(client(target), method)(BASE + "me/", {}, format="json").status_code == 405
    assert APIClient().get(BASE + "me/").status_code == 401
