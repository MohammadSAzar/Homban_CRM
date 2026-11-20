import uuid
import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from rest_framework.exceptions import NotFound, PermissionDenied
from apps.accounts.models import User
from apps.organizations.models import Workspace
from apps.organizations.capabilities import resolve_workspace_capability
from apps.staging.models import StagedRecord
from apps.staging.services import (
    create_staged_record, update_staged_record, mark_staged_record_ready, reject_staged_record,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    ws = Workspace.objects.create(name="Staging", slug="staging", workspace_mode="personal")
    actor = User.objects.create_user(username="staging-user", workspace=ws, role="consultant")
    return ws, actor


def create(world, **extra):
    data = dict(actor=world[1], entity_type="property_file", source_type="voice", raw_payload={"address": "unresolved"})
    data.update(extra)
    return create_staged_record(**data)


def test_user_system_creation_and_independent_defaults(world):
    ws, actor = world
    row = create(world)
    system = create_staged_record(workspace_id=ws.pk, entity_type="customer", source_type="api", raw_payload=[])
    assert row.created_by == actor and row.workspace == ws and row.status == "draft"
    assert system.created_by is None
    row.normalized_data["free_text"] = "pending"
    row.validation_issues.append({"field": "region", "code": "required", "message": "نیازمند بررسی"})
    assert system.normalized_data == {} and system.validation_issues == []


@pytest.mark.parametrize("field", ["entity_type", "source_type", "status"])
def test_closed_model_and_database_choices(world, field):
    ws, actor = world
    data = dict(workspace=ws, entity_type="customer", source_type="import")
    data[field] = "unknown"
    with pytest.raises(ValidationError):
        StagedRecord.objects.create(**data)
    with pytest.raises(IntegrityError), transaction.atomic():
        StagedRecord.objects.bulk_create([StagedRecord(**data)])


@pytest.mark.parametrize("value", [{}, "opaque", [{"field": "region"}], [{"field": "region", "code": "required", "message": 3}]])
def test_structured_issues_required(world, value):
    with pytest.raises(ValidationError):
        create(world, validation_issues=value)


def test_normalized_object_required(world):
    with pytest.raises(ValidationError):
        create(world, normalized_data=[])


@pytest.mark.parametrize("mode,allowed", [("personal", True), ("team", True), (None, False)])
def test_core_capability_and_service_gate(world, mode, allowed):
    ws, actor = world
    Workspace.objects.filter(pk=ws.pk).update(workspace_mode=mode, customer_type="agency_manager")
    User.objects.filter(pk=actor.pk).update(is_workspace_owner=True, is_staff=True, is_superuser=True)
    assert resolve_workspace_capability(ws, "staging").allowed is allowed
    if allowed:
        assert create(world).pk
    else:
        with pytest.raises(PermissionDenied):
            create(world)
        with pytest.raises(PermissionDenied):
            create_staged_record(workspace_id=ws.pk, entity_type="customer", source_type="crawler", raw_payload={})


def test_tenant_creator_validation_and_server_fields(world):
    ws, actor = world
    foreign = Workspace.objects.create(name="Other", slug="other", workspace_mode="team")
    user = User.objects.create_user(username="other", workspace=foreign)
    with pytest.raises(ValidationError):
        StagedRecord.objects.create(workspace=ws, created_by=user, entity_type="customer", source_type="api")
    with pytest.raises(ValidationError):
        create_staged_record(workspace_id=ws.pk, created_by_id=user.pk, entity_type="customer", source_type="api", raw_payload={})
    for extra in ({"workspace_id": foreign.pk}, {"created_by_id": user.pk}):
        with pytest.raises(PermissionDenied):
            create(world, **extra)


@pytest.mark.parametrize("inactive", ["workspace", "actor"])
def test_activity_rechecked(world, inactive):
    ws, actor = world
    if inactive == "workspace":
        Workspace.objects.filter(pk=ws.pk).update(is_active=False)
    else:
        User.objects.filter(pk=actor.pk).update(is_active=False)
    with pytest.raises(PermissionDenied):
        create(world)


def test_readiness_and_all_supported_transitions(world):
    row = create(world, validation_issues=[{"field": "region", "code": "unknown", "message": "نامشخص"}])
    kwargs = dict(actor=world[1], record_id=row.pk)
    with pytest.raises(ValidationError):
        mark_staged_record_ready(**kwargs)
    row.refresh_from_db()
    assert row.status == "draft"
    update_staged_record(**kwargs, data={"validation_issues": [], "normalized_data": {"address": "text"}})
    assert mark_staged_record_ready(**kwargs).status == "ready"
    with pytest.raises(ValidationError):
        mark_staged_record_ready(**kwargs)
    assert update_staged_record(**kwargs, data={}).status == "draft"
    mark_staged_record_ready(**kwargs)
    assert reject_staged_record(**kwargs).status == "rejected"
    for operation in (lambda: update_staged_record(**kwargs, data={}),
                      lambda: mark_staged_record_ready(**kwargs), lambda: reject_staged_record(**kwargs)):
        with pytest.raises(ValidationError):
            operation()
    assert reject_staged_record(actor=world[1], record_id=create(world).pk).status == "rejected"


@pytest.mark.parametrize("field,value", [("status", "ready"), ("raw_payload", {}), ("workspace_id", "foreign"), ("created_by_id", None)])
def test_protected_updates(world, field, value):
    row = create(world)
    with pytest.raises(ValidationError):
        update_staged_record(actor=world[1], record_id=row.pk, data={field: value})
    row.refresh_from_db()
    assert row.status == "draft" and row.raw_payload == {"address": "unresolved"}


def test_foreign_and_missing_ids_indistinguishable(world):
    ws, actor = world
    foreign = Workspace.objects.create(name="Other", slug="other", workspace_mode="team")
    row = create_staged_record(workspace_id=foreign.pk, entity_type="customer", source_type="api", raw_payload={})
    for identifier in (row.pk, uuid.uuid4()):
        with pytest.raises(NotFound):
            update_staged_record(actor=actor, record_id=identifier, data={})
        with pytest.raises(NotFound):
            reject_staged_record(workspace_id=ws.pk, record_id=identifier)
    # DS2 Personal review is workspace-wide; creator-only isolation applies in Team.
    Workspace.objects.filter(pk=ws.pk).update(workspace_mode="team")
    other = User.objects.create_user(username="sibling", workspace=ws)
    local = create(world)
    with pytest.raises(NotFound):
        update_staged_record(actor=other, record_id=local.pk, data={})


def test_no_canonical_side_effects_and_rollback(world):
    from apps.properties.models import PropertyFile
    from apps.customers.models import Customer
    before = (PropertyFile.objects.count(), Customer.objects.count())
    row = create(world)
    mark_staged_record_ready(actor=world[1], record_id=row.pk)
    assert (PropertyFile.objects.count(), Customer.objects.count()) == before
    with pytest.raises(RuntimeError), transaction.atomic():
        create(world)
        reject_staged_record(actor=world[1], record_id=row.pk)
        raise RuntimeError("rollback")
    row.refresh_from_db()
    assert row.status == "ready" and StagedRecord.objects.count() == 1
    assert not PropertyFile._meta.get_field("region").null


def test_internal_path_requires_explicit_workspace(world):
    with pytest.raises(PermissionDenied):
        create_staged_record(entity_type="customer", source_type="api", raw_payload={})
