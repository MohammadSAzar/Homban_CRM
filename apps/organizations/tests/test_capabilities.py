from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import User
from apps.organizations.capabilities import (
    CORE, TEAM_ONLY, TOPOLOGY_DEPENDENT_TEAM, CAPABILITY_CLASSES, resolve_workspace_capability,
)
from apps.organizations.models import Workspace

pytestmark = pytest.mark.django_db


@pytest.fixture
def workspace():
    return Workspace.objects.create(name="capabilities", slug="capabilities", customer_type="consultant")


@pytest.mark.parametrize("mode", ["personal", "team", None])
@pytest.mark.parametrize("capability_class,identifiers", [
    ("core", CORE), ("team_only", TEAM_ONLY), ("topology_dependent_team", TOPOLOGY_DEPENDENT_TEAM),
])
def test_mode_matrix(workspace, mode, capability_class, identifiers):
    Workspace.objects.filter(pk=workspace.pk).update(workspace_mode=mode)
    for capability in identifiers:
        result = resolve_workspace_capability(workspace, capability)
        allowed = mode == "team" or (mode == "personal" and capability_class == "core")
        reason = ("workspace_mode_unresolved" if mode is None else
                  "capability_not_available_in_personal" if not allowed else
                  "mode_eligible" if capability_class == "topology_dependent_team" else "allowed")
        assert (result.allowed, result.workspace_mode, result.capability_class, result.reason) == (
            allowed, mode, capability_class, reason)
        assert result.capability == capability


@pytest.mark.parametrize("customer_type", ["consultant", "range_manager", "agency_manager"])
def test_purchaser_metadata_independent(workspace, customer_type):
    Workspace.objects.filter(pk=workspace.pk).update(workspace_mode="personal", customer_type=customer_type)
    assert resolve_workspace_capability(workspace, "customers").allowed
    assert not resolve_workspace_capability(workspace, "chat").allowed


@pytest.mark.parametrize("flag", ["is_workspace_owner", "is_staff", "is_superuser"])
def test_user_flags_do_not_elevate_product_mode(workspace, flag):
    Workspace.objects.filter(pk=workspace.pk).update(workspace_mode="personal")
    before = resolve_workspace_capability(workspace, "user_management")
    User.objects.create_user(username="candidate", workspace=workspace, role="agency_manager", **{flag: True})
    assert resolve_workspace_capability(workspace, "user_management") == before
    assert not before.allowed


def test_unknown_and_missing_workspace_fail_closed(workspace):
    result = resolve_workspace_capability(workspace, "unknown_feature")
    assert not result.allowed and result.reason == "unknown_capability" and result.capability_class is None
    missing = resolve_workspace_capability(Workspace(pk=uuid4()), "customers")
    assert not missing.allowed and missing.reason == "workspace_mode_unresolved"


def test_readonly_frozen_bounded_and_not_topology_or_activity_gate(workspace):
    Workspace.objects.filter(pk=workspace.pk).update(workspace_mode="team", is_active=False)
    with CaptureQueriesContext(connection) as queries:
        result = resolve_workspace_capability(workspace, "user_management")
    assert result.allowed and result.reason == "mode_eligible"  # No Ranges/managers required here.
    assert len(queries) == 1 and queries[0]["sql"].lstrip().upper().startswith("SELECT")
    assert "FOR UPDATE" not in queries[0]["sql"]
    with pytest.raises(FrozenInstanceError):
        result.allowed = False
    with pytest.raises(TypeError):
        CAPABILITY_CLASSES["chat"] = "core"
    assert len(CAPABILITY_CLASSES) == 20
    assert not (CORE & TEAM_ONLY or CORE & TOPOLOGY_DEPENDENT_TEAM or TEAM_ONLY & TOPOLOGY_DEPENDENT_TEAM)
