import uuid
import pytest
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.organizations.models import Workspace
from apps.ranges.models import Range, RangeMembership
from apps.staging.models import StagedRecord
from apps.staging.services import create_staged_record
from .test_conversion import world, staged

pytestmark = pytest.mark.django_db
BASE = "/api/v1/staging/"


def client(actor=None):
    api = APIClient()
    if actor is not None:
        api.force_authenticate(actor)
    return api


def url(row, action=""):
    return BASE + str(row.pk) + "/" + (action + "/" if action else "")


def test_personal_review_system_rows_and_safe_list(world):
    w = world
    row = staged(w)
    system = create_staged_record(workspace_id=w.ws.pk, entity_type="customer", source_type="api", raw_payload={"phone": "secret"})
    api = client(w.actor)
    response = api.get(BASE)
    assert response.status_code == 200
    assert response.data["count"] == 2
    assert response.data["results"][0]["id"] == str(system.pk)
    for item in response.data["results"]:
        assert "raw_payload" not in item and "normalized_data" not in item and "validation_issues" not in item
    assert api.get(url(system)).data["raw_payload"] == {"phone": "secret"}
    assert api.patch(url(system), {"normalized_data": w.customer_data}, format="json").status_code == 200
    assert api.post(url(system, "ready"), {}, format="json").status_code == 200
    assert api.post(url(system, "validate"), {}, format="json").data["valid"] is True
    result = api.post(url(system, "convert"), {}, format="json")
    assert result.status_code == 200, result.data
    assert result.data["target"]["type"] == "customer"
    assert "mobile" not in result.data
    assert api.post(url(system, "convert"), {}, format="json").status_code == 400
    assert api.patch(url(system), {"normalized_data": {}}, format="json").status_code == 400
    assert api.post(url(system, "reject"), {}, format="json").status_code == 400
    assert api.get(url(system)).status_code == 200
    assert client().get(BASE).status_code == 401
    assert api.post(BASE, {}, format="json").status_code == 405


@pytest.mark.parametrize("multi", [False, True])
def test_team_creator_and_canonical_authority_scope(world, multi):
    w = world
    Workspace.objects.filter(pk=w.ws.pk).update(workspace_mode="team")
    manager = User.objects.create_user(username="manager", role="range_manager", workspace=w.ws)
    own_range = Range.objects.create(workspace=w.ws, manager=manager, name="One")
    RangeMembership.objects.create(workspace=w.ws, range=own_range, user=w.actor)
    authority = manager
    if multi:
        second = User.objects.create_user(username="manager-two", role="range_manager", workspace=w.ws)
        Range.objects.create(workspace=w.ws, manager=second, name="Two")
        authority = User.objects.create_user(username="agency", role="agency_manager", workspace=w.ws)
    row = staged(w)
    other = User.objects.create_user(username="other", role="consultant", workspace=w.ws,
                                   is_staff=True, is_superuser=True, is_workspace_owner=True)
    Workspace.objects.filter(pk=w.ws.pk).update(customer_type="agency_manager")
    system = create_staged_record(workspace_id=w.ws.pk, entity_type="customer", source_type="crawler", raw_payload={})
    assert client(w.actor).get(url(row)).status_code == 200
    assert client(w.actor).get(url(system)).status_code == 404
    assert client(other).get(BASE).data["count"] == 0
    for obj in (row, system):
        assert client(other).get(url(obj)).status_code == 404
        assert client(other).patch(url(obj), {"normalized_data": {}}, format="json").status_code == 404
        for action in ("ready", "reject", "validate", "convert"):
            assert client(other).post(url(obj, action), {}, format="json").status_code == 404
        assert client(authority).get(url(obj)).status_code == 200
    if multi:
        assert client(manager).get(url(system)).status_code == 404  # no Range review hierarchy
    # Review authority does not synthesize a canonical consultant assignment.
    result = client(authority).post(url(row, "validate"), {}, format="json")
    assert result.status_code == 200 and not result.data["valid"]
    data = {**w.file_data, "assigned_to": str(w.actor.pk)}
    assert client(authority).patch(url(row), {"normalized_data": data}, format="json").status_code == 200
    assert client(authority).post(url(row, "ready"), {}, format="json").status_code == 200
    assert client(authority).post(url(row, "convert"), {}, format="json").status_code == 200
    row.refresh_from_db()
    assert row.converted_by == authority and row.converted_property_file.assigned_to == w.actor
    assert client(authority).post(url(system, "reject"), {}, format="json").status_code == 200


@pytest.mark.parametrize("field", ["workspace", "workspace_id", "created_by", "entity_type", "source_type", "raw_payload", "status", "converted_at", "converted_by", "converted_customer", "updated_at"])
def test_patch_closed_fields(world, field):
    row = staged(world)
    response = client(world.actor).patch(url(row), {field: "untrusted"}, format="json")
    assert response.status_code == 400
    row.refresh_from_db()
    assert row.status == "ready" and row.raw_payload == {"contact": "private"}


def test_edit_ready_and_rejected_immutable(world):
    row = staged(world)
    api = client(world.actor)
    assert api.patch(url(row), {"normalized_data": {}}, format="json").data["status"] == "draft"
    assert api.post(url(row, "reject"), {}, format="json").data["status"] == "rejected"
    for action in ("ready", "reject", "validate", "convert"):
        assert api.post(url(row, action), {}, format="json").status_code == 400
    assert api.patch(url(row), {}, format="json").status_code == 400
    assert StagedRecord.objects.filter(pk=row.pk).exists()


@pytest.mark.parametrize("change", ["null", "inactive_workspace", "inactive_actor"])
def test_current_access_fails_closed(world, change):
    row = staged(world)
    if change == "null":
        Workspace.objects.filter(pk=world.ws.pk).update(workspace_mode=None)
    elif change == "inactive_workspace":
        Workspace.objects.filter(pk=world.ws.pk).update(is_active=False)
    else:
        User.objects.filter(pk=world.actor.pk).update(is_active=False)
    assert client(world.actor).get(BASE).status_code == 403
    assert client(world.actor).post(url(row, "convert"), {}, format="json").status_code == 403


def test_foreign_guesses_and_no_assignment_override(world):
    w = world
    other = Workspace.objects.create(name="Foreign", slug="foreign-api", workspace_mode="personal")
    foreign = create_staged_record(workspace_id=other.pk, entity_type="customer", source_type="api", raw_payload={})
    api = client(w.actor)
    assert api.get(url(foreign)).status_code == api.get(BASE + str(uuid.uuid4()) + "/").status_code == 404
    assert api.post(url(foreign, "convert"), {}, format="json").status_code == 404
    row = staged(w)
    assert api.post(url(row, "convert"), {"assigned_to": str(uuid.uuid4())}, format="json").status_code == 400
