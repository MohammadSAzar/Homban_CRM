from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from django.core.exceptions import ValidationError as ModelValidationError
from django.db import connection, IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import ValidationError
from apps.accounts.models import User
from apps.organizations.models import Workspace
from apps.locations.models import City, Region
from apps.properties.models import PropertyFile
from apps.customers.models import Customer
from apps.matching.models import RecommendationWork
from apps.staging.models import StagedRecord
from apps.staging.services import create_staged_record, mark_staged_record_ready, update_staged_record, reject_staged_record
from apps.staging.conversion import validate_for_conversion, convert_staged_record

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    ws = Workspace.objects.create(name="DS2", slug="ds2", workspace_mode="personal")
    actor = User.objects.create_user(username="ds2", workspace=ws, role="consultant")
    city = City.objects.create(workspace=ws, name="City")
    region = Region.objects.create(workspace=ws, city=city, name="Region")
    file_data = dict(transaction_type="sale", city=str(city.pk), region=str(region.pk),
                     area="100.00", bedrooms=1, total_price="1000000000.00")
    customer_data = dict(customer_type="buyer", name="Customer", min_area="10.00", max_area="100.00",
                         bedrooms=1, budget="1000000000.00", all_regions=True)
    return SimpleNamespace(**locals())


def staged(w, entity="property_file", ready=True, data=None):
    row = create_staged_record(actor=w.actor, entity_type=entity, source_type="voice",
        raw_payload={"contact": "private"}, normalized_data=deepcopy(data if data is not None else
        w.file_data if entity == "property_file" else w.customer_data))
    if ready:
        row = mark_staged_record_ready(actor=w.actor, record_id=row.pk)
    return row


@pytest.mark.parametrize("entity", ["property_file", "customer"])
def test_preview_no_writes_then_conversion_and_terminal(world, entity):
    w = world
    row = staged(w, entity)
    original = (row.raw_payload, row.normalized_data, row.validation_issues, row.updated_at)
    counts = (PropertyFile.objects.count(), Customer.objects.count(), RecommendationWork.objects.count())
    with CaptureQueriesContext(connection) as queries:
        result = validate_for_conversion(record_id=row.pk, actor=w.actor)
    assert result == {"valid": True, "entity_type": entity, "issues": [], "staging_issues": []}
    assert not any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries)
    assert counts == (PropertyFile.objects.count(), Customer.objects.count(), RecommendationWork.objects.count())
    row.refresh_from_db()
    assert original == (row.raw_payload, row.normalized_data, row.validation_issues, row.updated_at)
    row = convert_staged_record(record_id=row.pk, actor=w.actor)
    target = row.converted_property_file if entity == "property_file" else row.converted_customer
    assert row.status == "converted" and row.converted_by == w.actor and row.converted_at
    assert target.workspace == w.ws and target.assigned_to == w.actor
    assert (row.raw_payload, row.normalized_data, row.validation_issues) == original[:3]
    assert RecommendationWork.objects.filter(target_id=target.pk).exists()
    for operation in (convert_staged_record, mark_staged_record_ready, reject_staged_record):
        with pytest.raises((ValidationError, ModelValidationError)):
            operation(record_id=row.pk, actor=w.actor)
    with pytest.raises(ModelValidationError):
        update_staged_record(record_id=row.pk, actor=w.actor, data={})
    with pytest.raises(ModelValidationError):
        row.save()
    with pytest.raises(ProtectedError):
        target.delete()


@pytest.mark.parametrize("entity,data", [
    ("property_file", {}), ("property_file", {"region": None}),
    ("property_file", {"area": "-1"}), ("property_file", {"transaction_type": "unknown"}),
    ("property_file", {"total_price": None}), ("customer", {"min_area": "200"}),
    ("customer", {"all_regions": False}), ("customer", {"budget": None}),
    ("customer", {"customer_type": "unknown"}),
])
def test_invalid_canonical_input_is_structured_and_keeps_ready(world, entity, data):
    base = deepcopy(world.file_data if entity == "property_file" else world.customer_data)
    if data:
        base.update(data)
    else:
        base.pop("region")
    row = staged(world, entity, data=base)
    result = validate_for_conversion(record_id=row.pk, actor=world.actor)
    assert not result["valid"] and result["issues"]
    assert all(set(issue) == {"field", "code", "message"} for issue in result["issues"])
    with pytest.raises(ValidationError):
        convert_staged_record(record_id=row.pk, actor=world.actor)
    row.refresh_from_db()
    assert row.status == "ready" and row.converted_at is None
    assert not PropertyFile.objects.exists() and not Customer.objects.exists()


def test_preferences_and_foreign_regions(world):
    w = world
    data = {**w.customer_data, "all_regions": False, "preferred_regions": [str(w.region.pk)]}
    row = staged(w, "customer", data=data)
    converted = convert_staged_record(record_id=row.pk, actor=w.actor)
    assert list(converted.converted_customer.preferred_regions.all()) == [w.region]
    foreign = Workspace.objects.create(name="Other", slug="other-ds2", workspace_mode="personal")
    city = City.objects.create(workspace=foreign, name="Foreign")
    region = Region.objects.create(workspace=foreign, city=city, name="Foreign")
    for entity, bad in [("property_file", {**w.file_data, "region": str(region.pk)}),
                        ("customer", {**data, "preferred_regions": [str(region.pk)]}),
                        ("customer", {**data, "all_regions": True})]:
        row = staged(w, entity, data=bad)
        assert not validate_for_conversion(record_id=row.pk, actor=w.actor)["valid"]


def test_draft_preview_keeps_staging_issues_and_ready_is_not_canonical_valid(world):
    row = staged(world, ready=False, data={})
    issue = {"field": "region", "code": "review", "message": "بررسی شود"}
    update_staged_record(record_id=row.pk, actor=world.actor, data={"validation_issues": [issue]})
    result = validate_for_conversion(record_id=row.pk, actor=world.actor)
    assert not result["valid"] and result["issues"] and result["staging_issues"] == [issue]
    with pytest.raises(ValidationError):
        convert_staged_record(record_id=row.pk, actor=world.actor)
    update_staged_record(record_id=row.pk, actor=world.actor, data={"validation_issues": []})
    assert mark_staged_record_ready(record_id=row.pk, actor=world.actor).status == "ready"
    assert not validate_for_conversion(record_id=row.pk, actor=world.actor)["valid"]


@pytest.mark.parametrize("failure", ["canonical", "linkage"])
def test_failure_rolls_back_canonical_matching_and_callbacks(world, failure, django_capture_on_commit_callbacks):
    row = staged(world)
    baseline = RecommendationWork.objects.count()
    original = StagedRecord.save
    def fail_linkage(instance, *args, **kwargs):
        if instance.status == "converted":
            raise RuntimeError("linkage failure")
        return original(instance, *args, **kwargs)
    def fail_after_create(actor, data):
        from apps.properties.api_services import save_file
        save_file(actor=actor, data=data)
        raise RuntimeError("canonical failure")
    target = "apps.staging.converters.property_file.create" if failure == "canonical" else "apps.staging.models.StagedRecord.save"
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        with patch(target, side_effect=fail_after_create) if failure == "canonical" else patch(target, new=fail_linkage):
            with pytest.raises(RuntimeError):
                convert_staged_record(record_id=row.pk, actor=world.actor)
    assert callbacks == []
    assert not PropertyFile.objects.exists()
    assert RecommendationWork.objects.count() == baseline
    row.refresh_from_db()
    assert row.status == "ready" and row.converted_property_file_id is None


def test_existing_constraint_failure_never_merges(world):
    row = staged(world)
    with patch("apps.staging.converters.property_file.create", side_effect=IntegrityError("private database details")):
        with pytest.raises(ValidationError) as error:
            convert_staged_record(record_id=row.pk, actor=world.actor)
    assert "private database details" not in str(error.value)
    row.refresh_from_db()
    assert row.status == "ready"


def test_linkage_db_constraints_and_model_tenant_validation(world):
    row = staged(world)
    with pytest.raises(IntegrityError), transaction.atomic():
        StagedRecord.objects.filter(pk=row.pk).update(status="converted")
    converted = convert_staged_record(record_id=row.pk, actor=world.actor)
    for changes in ({"status": "ready"}, {"entity_type": "customer"}, {"converted_by": None}, {"converted_at": None}):
        with pytest.raises(IntegrityError), transaction.atomic():
            StagedRecord.objects.filter(pk=row.pk).update(**changes)
    customer = convert_staged_record(record_id=staged(world, "customer").pk, actor=world.actor).converted_customer
    with pytest.raises(IntegrityError), transaction.atomic():
        StagedRecord.objects.filter(pk=row.pk).update(converted_customer=customer)
    fresh = staged(world)
    with pytest.raises(IntegrityError), transaction.atomic():
        StagedRecord.objects.filter(pk=fresh.pk).update(converted_property_file=converted.converted_property_file)
    other = Workspace.objects.create(name="Other", slug="linkage-foreign", workspace_mode="team")
    foreign = User.objects.create_user(username="foreign-linkage", workspace=other)
    fresh.status = "converted"
    fresh.converted_property_file = converted.converted_property_file
    fresh.converted_by = foreign
    fresh.converted_at = converted.converted_at
    with pytest.raises(ModelValidationError):
        fresh.full_clean()
    fresh.converted_by = world.actor
    fresh.workspace = other
    with pytest.raises(ModelValidationError):
        fresh.full_clean()
    assert not PropertyFile._meta.get_field("region").null


def test_success_preserves_normal_post_commit_work(world, django_capture_on_commit_callbacks):
    row = staged(world, "customer")
    with patch("apps.matching.generation_events.publish_work") as publish:
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            converted = convert_staged_record(record_id=row.pk, actor=world.actor)
            assert not publish.called
        assert callbacks and publish.called
        assert RecommendationWork.objects.filter(target_id=converted.converted_customer_id).exists()


def test_rejected_cannot_convert_and_no_actorless_conversion(world):
    row = staged(world)
    reject_staged_record(record_id=row.pk, actor=world.actor)
    with pytest.raises(ValidationError):
        convert_staged_record(record_id=row.pk, actor=world.actor)
    from rest_framework.exceptions import PermissionDenied
    with pytest.raises(PermissionDenied):
        convert_staged_record(record_id=row.pk, actor=None)
