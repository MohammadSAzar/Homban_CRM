from copy import deepcopy
from unittest.mock import patch
import json
import uuid
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from apps.accounts.models import User
from apps.organizations.models import Workspace
from apps.locations.models import City, Region
from apps.properties.api_services import save_file
from apps.customers.api_services import save_customer
from apps.properties.serializers import PropertyFileWriteSerializer
from apps.customers.serializers import CustomerWriteSerializer
from apps.properties.models import PropertyFile
from apps.customers.models import Customer
from apps.staging.services import create_staged_record, reject_staged_record
from apps.staging.conversion import convert_staged_record
from apps.staging.duplicates.service import find_duplicate_candidates
from apps.staging.duplicates import property_file, customer
from apps.staging.duplicates.snapshots import property_snapshot, customer_snapshot
from .test_conversion import world, staged
from .test_review_api import client, url

pytestmark = pytest.mark.django_db


def canonical(w, entity="property_file", **changes):
    data = deepcopy(w.file_data if entity == "property_file" else w.customer_data)
    data.update(changes)
    serializer = (PropertyFileWriteSerializer if entity == "property_file" else CustomerWriteSerializer)(data=data)
    serializer.is_valid(raise_exception=True)
    return (save_file if entity == "property_file" else save_customer)(actor=w.actor, data=serializer.validated_data)


def find(w, row):
    return find_duplicate_candidates(record_id=row.pk, actor=w.actor)


def test_property_phone_and_geometry_high_and_no_mutation(world):
    w = world
    target = canonical(w, owner_phone="+98 (912) 123 4567", description="private notes")
    row = staged(w, ready=False, data={**w.file_data, "owner_phone": "۰۹۱۲۱۲۳۴۵۶۷"})
    before = (row.updated_at, deepcopy(row.normalized_data), deepcopy(row.raw_payload), target.updated_at)
    with CaptureQueriesContext(connection) as queries:
        result = find(w, row)
    assert result["candidate_count"] == 1
    candidate = result["candidates"][0]
    assert candidate["id"] == str(target.pk) and candidate["confidence"] == "high"
    assert candidate["comparisons"]["phone"]
    assert not any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries)
    row.refresh_from_db(); target.refresh_from_db()
    assert before == (row.updated_at, row.normalized_data, row.raw_payload, target.updated_at)
    encoded = json.dumps(result)
    for secret in ("1234567", "private notes", "raw_payload", "normalized_data"):
        assert secret not in encoded


def test_same_phone_different_property_not_high(world):
    canonical(world, owner_phone="09121234567", area="500", bedrooms=5)
    row = staged(world, data={**world.file_data, "owner_phone": "09121234567"})
    assert all(item["confidence"] != "high" for item in find(world, row)["candidates"])


def test_persian_address_and_geometry_without_phone(world):
    target = canonical(world, address="خيابان  علي   كريمي")
    row = staged(world, data={**world.file_data, "address": "خیابان علی کریمی"})
    result = find(world, row)
    assert result["candidates"][0]["id"] == str(target.pk)
    assert result["candidates"][0]["confidence"] == "medium"


@pytest.mark.parametrize("data", [{"region": "same"}, {"area": "100"}, {"bedrooms": 1}, {"total_price": "1000000000"}])
def test_weak_property_signals_not_candidates(world, data):
    canonical(world)
    if "region" in data:
        data = {"region": str(world.region.pk)}
    row = staged(world, ready=False, data={"transaction_type": "sale", **data})
    assert find(world, row)["candidates"] == []


@pytest.mark.parametrize("phone", ["09121234567", "+989121234567", "00989121234567", "989121234567", "۰۹۱۲۱۲۳۴۵۶۷"])
def test_customer_mobile_high(world, phone):
    target = canonical(world, "customer", mobile=phone)
    row = staged(world, "customer", data={"mobile": "09121234567"})
    result = find(world, row)
    assert result["candidates"][0]["id"] == str(target.pk)
    assert result["candidates"][0]["confidence"] == "high"
    assert "09121234567" not in json.dumps(result)


def test_customer_name_preferences_fallback_and_conflicting_phones(world):
    target = canonical(world, "customer", name="علي كريمي")
    data = {**world.customer_data, "name": "علی کریمی"}
    row = staged(world, "customer", data=data)
    assert find(world, row)["candidates"][0]["confidence"] == "medium"
    Customer.objects.filter(pk=target.pk).update(mobile="09121111111")
    row.normalized_data["mobile"] = "09122222222"
    row.save()
    assert find(world, row)["candidates"] == []


def test_customer_weak_preferences_and_unknown_do_not_match(world):
    canonical(world, "customer")
    for data in ({"budget": "1000000000"}, {"all_regions": True}, {"name": "Someone else", **{"budget": "1000000000"}}):
        row = staged(world, "customer", data=data)
        assert find(world, row)["candidates"] == []


def test_inactive_historical_candidates_are_retained(world):
    target = canonical(world, "customer", mobile="09121234567", status="inactive")
    row = staged(world, "customer", data={"mobile": "09121234567"})
    assert find(world, row)["candidates"][0]["id"] == str(target.pk)


def test_scope_excludes_other_consultant_and_foreign_workspace(world):
    w = world
    target = canonical(w, "customer", mobile="09121234567")
    row = staged(w, "customer", data={"mobile": "09121234567"})
    other = User.objects.create_user(username="other-duplicate", role="consultant", workspace=w.ws)
    Customer.objects.filter(pk=target.pk).update(assigned_to=other)
    assert find(w, row)["candidates"] == []
    foreign = Workspace.objects.create(name="Foreign", slug="dup-foreign", workspace_mode="personal")
    Customer.objects.filter(pk=target.pk).update(workspace=foreign)
    assert find(w, row)["candidates"] == []
    foreign_row = create_staged_record(workspace_id=foreign.pk, entity_type="customer", source_type="api", raw_payload={})
    with pytest.raises(NotFound):
        find(w, foreign_row)


def test_advisory_does_not_block_or_reuse_target(world):
    target = canonical(world, owner_phone="09121234567")
    row = staged(world, data={**world.file_data, "owner_phone": "09121234567"})
    assert find(world, row)["candidate_count"] == 1
    converted = convert_staged_record(record_id=row.pk, actor=world.actor)
    assert converted.converted_property_file_id != target.pk
    assert PropertyFile.objects.count() == 2
    with pytest.raises(ValidationError):
        find(world, row)
    another = staged(world)
    reject_staged_record(record_id=another.pk, actor=world.actor)
    with pytest.raises(ValidationError):
        find(world, another)


def test_api_contract_auth_and_no_secrets(world):
    canonical(world, "customer", mobile="09121234567", description="secret")
    row = staged(world, "customer", data={"mobile": "09121234567"})
    assert client().get(url(row, "duplicates")).status_code == 401
    response = client(world.actor).get(url(row, "duplicates"))
    assert response.status_code == 200
    assert set(response.data) == {"entity_type", "normalization_version", "candidate_count", "candidates", "truncated", "candidate_limit", "result_limit"}
    assert "secret" not in json.dumps(response.data) and "09121234567" not in json.dumps(response.data)
    assert client(world.actor).post(url(row, "duplicates"), {}, format="json").status_code == 405


def test_ordering_bounded_prefilter_and_query_growth(world):
    for _ in range(3):
        canonical(world, "customer", mobile="09121234567")
    row = staged(world, "customer", data={"mobile": "09121234567"})
    with CaptureQueriesContext(connection) as queries:
        first = find(world, row)
    ids = [item["id"] for item in first["candidates"]]
    assert ids == sorted(ids)
    assert find(world, row) == first
    sql = [q["sql"] for q in queries if "FROM `customers_customer`" in q["sql"]]
    assert sql and any("LIMIT 201" in q and "workspace_id" in q and "mobile" in q for q in sql)
    count = len(queries)
    canonical(world, "customer", mobile="09121234567")
    with CaptureQueriesContext(connection) as more:
        find(world, row)
    assert len(more) == count
    with patch("apps.staging.duplicates.customer.CANDIDATE_LIMIT", 1), patch("apps.staging.duplicates.service.CANDIDATE_LIMIT", 1):
        limited = find(world, row)
    assert limited["truncated"] and limited["candidate_count"] == 1


@pytest.mark.parametrize("failure", ["null", "workspace", "actor"])
def test_current_state_gates(world, failure):
    row = staged(world)
    if failure == "null":
        Workspace.objects.filter(pk=world.ws.pk).update(workspace_mode=None)
    elif failure == "workspace":
        Workspace.objects.filter(pk=world.ws.pk).update(is_active=False)
    else:
        User.objects.filter(pk=world.actor.pk).update(is_active=False)
    with pytest.raises(PermissionDenied):
        find(world, row)


def test_team_review_authority_creator_and_no_flag_bypass(world):
    from apps.ranges.models import Range, RangeMembership
    w = world
    Workspace.objects.filter(pk=w.ws.pk).update(workspace_mode="team", customer_type="agency_manager")
    authority = User.objects.create_user(username="dup-authority", role="range_manager", workspace=w.ws)
    team = Range.objects.create(workspace=w.ws, manager=authority, name="Team")
    RangeMembership.objects.create(workspace=w.ws, range=team, user=w.actor)
    target = canonical(w, "customer", mobile="09121234567")
    own = staged(w, "customer", data={"mobile": "09121234567"})
    system = create_staged_record(workspace_id=w.ws.pk, entity_type="customer", source_type="crawler",
                                  raw_payload={}, normalized_data={"mobile": "09121234567"})
    ordinary = User.objects.create_user(username="dup-flags", role="consultant", workspace=w.ws,
                                       is_workspace_owner=True, is_staff=True, is_superuser=True)
    assert find(w, own)["candidate_count"] == 1
    for row in (own, system):
        response = client(authority).get(url(row, "duplicates"))
        assert response.status_code == 200
        assert response.data["candidates"][0]["id"] == str(target.pk)
        assert client(ordinary).get(url(row, "duplicates")).status_code == 404
    assert client(w.actor).get(url(system, "duplicates")).status_code == 404


def test_customer_explicit_regions_overlap_and_readonly(world):
    canonical(world, "customer", all_regions=False, preferred_regions=[str(world.region.pk)])
    data = {**world.customer_data, "all_regions": False, "preferred_regions": [str(world.region.pk)]}
    row = staged(world, "customer", data=data)
    with CaptureQueriesContext(connection) as queries:
        result = find(world, row)
    assert result["candidates"][0]["confidence"] == "medium"
    assert not any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries)
    assert row.normalized_data == data


def test_customer_equally_missing_phone_alone_is_not_evidence(world):
    canonical(world, "customer", name="Entirely different", mobile="")
    row = staged(world, "customer", data={"mobile": "not-a-phone"})
    assert find(world, row)["candidates"] == []
