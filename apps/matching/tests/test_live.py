import uuid
from dataclasses import asdict
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import User
from apps.customers.models import Customer
from apps.customers.services import create_customer
from apps.locations.models import City, Region
from apps.organizations.models import Workspace
from apps.properties.models import PropertyFile
from apps.ranges.models import Range, RangeMembership
from apps.matching.models import MatchingProfile, SETTING_FIELDS, WEIGHT_FIELDS
from apps.matching.engine import evaluate_match
from apps.matching.live_services import live_matches
from apps.matching.live_serializers import matching_result_data

pytestmark = pytest.mark.django_db


def client_for(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + str(AccessToken.for_user(user)))
    return client


def endpoint(source):
    group = "customers" if isinstance(source, Customer) else "property-files"
    return f"/api/v1/{group}/{source.pk}/matches/"


@pytest.fixture
def world():
    ws = Workspace.objects.create(name="اول",slug="live-first",customer_type="agency_manager")
    foreign_ws = Workspace.objects.create(name="دوم",slug="live-foreign",customer_type="consultant")
    def user(name, role="consultant", workspace=ws):
        return User.objects.create_user(username=name, role=role, workspace=workspace)
    own, other = user("own"), user("other")
    agency, manager = user("agency","agency_manager"), user("manager","range_manager")
    foreign_user = user("foreign",workspace=foreign_ws)
    team = Range.objects.create(workspace=ws, name="مدیریت", manager=manager)
    outside = Range.objects.create(workspace=ws, name="دیگر")
    RangeMembership.objects.create(workspace=ws, range=team, user=own)
    RangeMembership.objects.create(workspace=ws, range=outside, user=other)
    city = City.objects.create(workspace=ws,name="شهر")
    region = Region.objects.create(workspace=ws,city=city,name="منطقه")
    second_region = Region.objects.create(workspace=ws,city=city,name="دیگر")
    foreign_city = City.objects.create(workspace=foreign_ws,name="شهر")
    foreign_region = Region.objects.create(workspace=foreign_ws,city=foreign_city,name="منطقه")
    def file(actor=own, location=region, **changes):
        values = dict(workspace=actor.workspace, assigned_to=actor, city=location.city, region=location,
            transaction_type="sale",total_price=1000,area=100,bedrooms=2,building_age=5,
            parking=True,elevator=True,storage=True,balcony=True,
            owner_name="SECRET_OWNER",owner_phone="SECRET_PHONE",visit_contact_phone="SECRET_VISIT",
            address="SECRET_ADDRESS",description="SECRET_FILE_NOTES")
        return PropertyFile.objects.create(**{**values,**changes})
    def customer(actor=own, location=region, **changes):
        values = dict(workspace=actor.workspace, assigned_to=actor, customer_type="buyer",budget=1000,
            min_area=100,max_area=100,bedrooms=2,min_building_age=0,max_building_age=10,
            name="SECRET_CUSTOMER",mobile="SECRET_MOBILE",description="SECRET_CUSTOMER_NOTES",
            preferred_regions=[location])
        return create_customer(**{**values,**changes})
    own_file, other_file, foreign_file = file(),file(other),file(foreign_user,foreign_region)
    own_customer,other_customer,foreign_customer = customer(),customer(other),customer(foreign_user,foreign_region)
    return SimpleNamespace(**locals())


def rows(response):
    assert response.status_code == 200, response.data
    return response.data["results"]


@pytest.mark.parametrize("source", ["own_customer","own_file"])
@pytest.mark.parametrize("actor", ["own","manager","agency"])
def test_authorized_sources_workspace_wide_candidates(world, source, actor):
    result = rows(client_for(getattr(world,actor)).post(endpoint(getattr(world,source)),{},format="json"))
    expected = (world.own_file,world.other_file) if source == "own_customer" else (world.own_customer,world.other_customer)
    assert {row["candidate"]["id"] for row in result} == {str(item.pk) for item in expected}
    assert all(row["formula_version"] == "matching-v1" and row["recommended"] for row in result)
    assert "SECRET" not in str(result)
    for row in result:
        assert not {"name","mobile","description","owner_name","owner_phone","visit_contact_phone","address","images","valuable_reasons","assigned_to","workspace"} & set(row["candidate"])


@pytest.mark.parametrize("source", ["other_customer","other_file","foreign_customer","foreign_file"])
@pytest.mark.parametrize("actor", ["own","manager"])
def test_out_of_scope_sources_fail_closed(world, source, actor):
    client = client_for(getattr(world,actor))
    response = client.post(endpoint(getattr(world,source)),{},format="json")
    assert response.status_code == 404
    assert "SECRET" not in str(response.data)
    assert not MatchingProfile.objects.exists()


@pytest.mark.parametrize("role", ["secretary","admin"])
def test_no_owner_staff_bypass(world, role):
    user = world.user(role,role)
    user.is_workspace_owner = user.is_superuser = user.is_staff = True
    user.save()
    for source in (world.own_file,world.own_customer):
        assert client_for(user).post(endpoint(source),{},format="json").status_code == 403


def test_range_scope_revoked_and_agency_can_use_other_sources(world):
    world.team.is_active = False
    world.team.save()
    for source in (world.own_file,world.own_customer):
        assert client_for(world.manager).post(endpoint(source),{},format="json").status_code == 404
        assert client_for(world.agency).post(endpoint(source),{},format="json").status_code == 200
    assert client_for(world.agency).post(endpoint(world.foreign_file),{},format="json").status_code == 404


def test_bidirectional_same_engine_and_viewer_profile(world):
    world.own_file.parking = False
    world.own_file.save()
    MatchingProfile.objects.create(user=world.other,area=99)
    profile = MatchingProfile.objects.create(user=world.agency,area=11)
    client = client_for(world.agency)
    forward = next(row for row in rows(client.post(endpoint(world.other_customer),{},format="json")) if row["candidate"]["id"] == str(world.own_file.pk))
    backward = next(row for row in rows(client.post(endpoint(world.own_file),{},format="json")) if row["candidate"]["id"] == str(world.other_customer.pk))
    assert {k:v for k,v in forward.items() if k != "candidate"} == {k:v for k,v in backward.items() if k != "candidate"}
    profile.refresh_from_db()
    world.own_file.refresh_from_db()
    world.other_customer.refresh_from_db()
    assert {k:v for k,v in forward.items() if k != "candidate"} == matching_result_data(evaluate_match(world.own_file,world.other_customer,profile))
    own_result = next(row for row in rows(client_for(world.own).post(endpoint(world.own_customer),{},format="json")) if row["candidate"]["id"] == str(world.own_file.pk))
    assert own_result["final_score"] != forward["final_score"]


def test_overrides_weight_threshold_budget_and_no_persistence(world):
    profile = MatchingProfile.objects.create(user=world.own)
    before = MatchingProfile.objects.values().get(pk=profile.pk)
    world.other_file.area = D(110)
    world.other_file.save()
    client = client_for(world.own)
    baseline = next(row for row in rows(client.post(endpoint(world.own_customer),{},format="json")) if row["candidate"]["id"] == str(world.other_file.pk))
    changed = next(row for row in rows(client.post(endpoint(world.own_customer),{"overrides":{"area":100}},format="json")) if row["candidate"]["id"] == str(world.other_file.pk))
    assert D(changed["final_score"]) < D(baseline["final_score"])
    assert len(rows(client.post(endpoint(world.own_customer),{"overrides":{"minimum_score":100}},format="json"))) == 1
    world.other_file.total_price = 1300
    world.other_file.save()
    assert len(rows(client.post(endpoint(world.own_customer),{},format="json"))) == 1
    assert len(rows(client.post(endpoint(world.own_customer),{"overrides":{"sale_budget_upper_ratio":"1.3"}},format="json"))) == 2
    assert MatchingProfile.objects.values().get(pk=profile.pk) == before


@pytest.mark.parametrize("direction", ["customer","file"])
def test_rent_override(world,direction):
    customer = world.customer(customer_type="tenant",budget=None,deposit_budget=0,monthly_rent_budget=3000000)
    file = world.file(world.other,transaction_type="rent",total_price=None,deposit_amount=100000000,monthly_rent=0)
    source = customer if direction == "customer" else file
    client = client_for(world.agency)
    assert len(rows(client.post(endpoint(source),{},format="json"))) == 1
    assert rows(client.post(endpoint(source),{"overrides":{"rent_per_100m_deposit":6000000}},format="json")) == []


@pytest.mark.parametrize("payload", [
    {"workspace":"x"},{"user":"x"},{"page":1},{"overrides":{"workspace":"x"}},
    {"overrides":{"budget_weight":1}},{"overrides":{"area":-1}},
    {"overrides":{key:0 for key in WEIGHT_FIELDS}},
    {"overrides":{"minimum_score":101}},{"overrides":{"sale_budget_lower_ratio":0}},
    {"overrides":{"sale_budget_lower_ratio":"1.1"}},{"overrides":{"sale_budget_upper_ratio":"0.9"}},
    {"overrides":{"rent_per_100m_deposit":0}}, {"overrides":{"area":"NaN"}},
    {"hard_constraints":{"unknown":True}},{"hard_constraints":{"area":"true"}},
    {"hard_constraints":{"parking":1}},{"hard_constraints":{"region":None}},
    {"overrides":[]},{"hard_constraints":[]},[],
])
def test_strict_payload_validation(world,payload):
    response = client_for(world.own).post(endpoint(world.own_customer),payload,format="json")
    assert response.status_code == 400
    assert not MatchingProfile.objects.exists()


@pytest.mark.parametrize("name,value", [("area",101),("bedrooms",3),("building_age",11),
    ("parking",False),("parking",None),("elevator",False),("elevator",None),
    ("storage",False),("storage",None),("balcony",False),("balcony",None)])
@pytest.mark.parametrize("direction", ["customer","file"])
def test_hard_exact_constraints_both_directions(world,name,value,direction):
    setattr(world.other_file,name,value)
    world.other_file.save()
    source = world.own_customer if direction == "customer" else world.other_file
    client = client_for(world.agency)
    normal = rows(client.post(endpoint(source),{"overrides":{"minimum_score":0}},format="json"))
    strict = rows(client.post(endpoint(source),{"overrides":{"minimum_score":0},"hard_constraints":{name:True}},format="json"))
    if direction == "customer":
        assert str(world.other_file.pk) in {row["candidate"]["id"] for row in normal}
        assert str(world.other_file.pk) not in {row["candidate"]["id"] for row in strict}
    else:
        assert len(normal) == 2 and strict == []
    off = rows(client.post(endpoint(source),{"overrides":{"minimum_score":0},"hard_constraints":{name:False}},format="json"))
    assert off == normal


@pytest.mark.parametrize("all_regions", [False,True])
def test_region_constraint_active_and_historical(world,all_regions):
    world.own_customer.all_regions = all_regions
    world.own_customer.save()
    world.other_file.region = world.second_region
    world.other_file.save()
    client = client_for(world.own)
    data = {"hard_constraints":{"region":True},"overrides":{"minimum_score":0}}
    assert len(rows(client.post(endpoint(world.own_customer),data,format="json"))) == (2 if all_regions else 1)
    world.region.is_active = False
    world.region.save()
    result = rows(client.post(endpoint(world.own_customer),data,format="json"))
    assert {row["candidate"]["id"] for row in result} == {str(world.other_file.pk) if all_regions else str(world.own_file.pk)}


def test_age_constraint_invalid_customer_source(world):
    world.own_customer.min_building_age = world.own_customer.max_building_age = None
    world.own_customer.save()
    assert client_for(world.own).post(endpoint(world.own_customer),{"hard_constraints":{"building_age":True}},format="json").status_code == 400


def test_status_gate_threshold_and_order(world):
    low = world.file(world.other,parking=False)
    inactive = world.file(world.other,status="inactive")
    expensive = world.file(world.other,total_price=2000)
    client = client_for(world.own)
    result = rows(client.post(endpoint(world.own_customer),{},format="json"))
    expected = sorted([str(world.own_file.pk),str(world.other_file.pk)]) + [str(low.pk)]
    assert [row["candidate"]["id"] for row in result] == expected
    assert len(rows(client.post(endpoint(world.own_customer),{"overrides":{"minimum_score":100}},format="json"))) == 2
    world.own_customer.status = "inactive"
    world.own_customer.save()
    assert rows(client.post(endpoint(world.own_customer),{},format="json")) == []


@pytest.mark.parametrize("source", ["own_customer","own_file"])
def test_query_count_constant_and_no_writes(world,source):
    MatchingProfile.objects.create(user=world.own)
    client = client_for(world.own)
    counts = []
    before_files = list(PropertyFile.objects.order_by("pk").values())
    before_customers = list(Customer.objects.order_by("pk").values())
    with CaptureQueriesContext(connection) as captured:
        response = client.post(endpoint(getattr(world,source)),{},format="json")
    rows(response)
    assert not any(q["sql"].lstrip().upper().startswith(("UPDATE","INSERT","DELETE")) for q in captured)
    counts.append(len(captured))
    assert list(PropertyFile.objects.order_by("pk").values()) == before_files
    assert list(Customer.objects.order_by("pk").values()) == before_customers
    for _ in range(4):
        world.file(world.other) if source == "own_customer" else world.customer(world.other)
    with CaptureQueriesContext(connection) as captured:
        rows(client.post(endpoint(getattr(world,source)),{},format="json"))
    counts.append(len(captured))
    assert counts[0] == counts[1] and counts[0] <= 20


def test_page_size_cap_and_methods(world):
    client = client_for(world.own)
    # Unique UUIDs, canonical fields; bulk creation is confined to test fixtures.
    PropertyFile.objects.bulk_create([PropertyFile(workspace=world.ws,assigned_to=world.other,city=world.city,region=world.region,
        transaction_type="sale",area=100,bedrooms=2,total_price=1000,price_per_square_meter=0,
        code=f"PF-{uuid.uuid4().hex.upper()}") for _ in range(51)])
    first = client.post(endpoint(world.own_customer),{},format="json")
    second = client.post(endpoint(world.own_customer)+"?page=2",{},format="json")
    assert len(rows(first)) == 50 and len(rows(second)) == 3
    assert first.data["count"] == 53 and first.data["next"]
    assert not {r["candidate"]["id"] for r in rows(first)} & {r["candidate"]["id"] for r in rows(second)}
    with patch("apps.matching.live_services.MAX_CANDIDATES",2), patch("apps.matching.live_services.evaluate_match") as evaluate:
        assert client.post(endpoint(world.own_customer),{},format="json").status_code == 400
        evaluate.assert_not_called()
    assert client.get(endpoint(world.own_customer)).status_code == 405
    assert client.delete(endpoint(world.own_customer)).status_code == 405
    assert client.post(endpoint(world.own_customer)+"?workspace=x",{},format="json").status_code == 400


def test_auth_and_fresh_service_actor(world):
    assert APIClient().post(endpoint(world.own_customer),{},format="json").status_code == 401
    stale = world.own
    User.objects.filter(pk=stale.pk).update(role="secretary")
    from rest_framework.exceptions import PermissionDenied
    with pytest.raises(PermissionDenied):
        live_matches(actor=stale,source_id=world.own_customer.pk,direction="customer",data={})
    User.objects.filter(pk=stale.pk).update(is_active=False)
    assert client_for(stale).post(endpoint(world.own_customer),{},format="json").status_code == 401


def test_multi_range_source_scope_and_owner_flag(world):
    world.outside.manager = world.manager
    world.outside.save()
    for source in (world.other_customer,world.other_file):
        assert client_for(world.manager).post(endpoint(source),{},format="json").status_code == 200
    world.own.is_workspace_owner = world.own.is_staff = world.own.is_superuser = True
    world.own.save()
    assert client_for(world.own).post(endpoint(world.other_customer),{},format="json").status_code == 404


@pytest.mark.parametrize("state", ["inactive_workspace","workspace_less"])
def test_workspace_auth_denied(world,state):
    client = client_for(world.own)
    if state == "inactive_workspace":
        Workspace.objects.filter(pk=world.ws.pk).update(is_active=False)
    else:
        User.objects.filter(pk=world.own.pk).update(workspace=None)
    for source in (world.own_customer,world.own_file):
        assert client.post(endpoint(source),{},format="json").status_code == 401


def test_hard_checks_precede_scoring_and_unknown_age_excluded(world):
    PropertyFile.objects.filter(workspace=world.ws).update(total_price=2000)
    with patch("apps.matching.live_services.hard_constraint_failure") as hard, patch("apps.matching.live_services.evaluate_match") as evaluate:
        assert rows(client_for(world.own).post(endpoint(world.own_customer),{},format="json")) == []
        hard.assert_not_called()
        evaluate.assert_not_called()
    PropertyFile.objects.filter(workspace=world.ws).update(total_price=1000,building_age=None)
    with patch("apps.matching.live_services.evaluate_match") as evaluate:
        assert rows(client_for(world.own).post(endpoint(world.own_customer),{"hard_constraints":{"building_age":True}},format="json")) == []
        evaluate.assert_not_called()


def test_exact_lower_age_bound_and_inactive_all_regions_reverse(world):
    world.own_customer.min_building_age = 6
    world.own_customer.max_building_age = None
    world.own_customer.save()
    client = client_for(world.agency)
    strict = rows(client.post(endpoint(world.own_file),{"hard_constraints":{"building_age":True}},format="json"))
    assert {row["candidate"]["id"] for row in strict} == {str(world.other_customer.pk)}
    world.own_customer.all_regions = True
    world.own_customer.save()
    world.region.is_active = False
    world.region.save()
    strict = rows(client.post(endpoint(world.own_file),{"hard_constraints":{"region":True}},format="json"))
    assert {row["candidate"]["id"] for row in strict} == {str(world.other_customer.pk)}


def test_stable_score_sort_does_not_round_50_digit_scores(world):
    # Pure engine scores retain more precision than Python's default Decimal context.
    from dataclasses import replace
    profile = MatchingProfile.objects.create(user=world.own)
    original = evaluate_match(world.own_file,world.own_customer,profile)
    ordered = sorted([world.own_file.pk,world.other_file.pk])
    def evaluated(file,customer,settings):
        score = D("90.000000000000000000000000000001") if file.pk == ordered[1] else D("90.000000000000000000000000000000")
        return replace(original,final_score=score)
    with patch("apps.matching.live_services.evaluate_match",side_effect=evaluated):
        result = rows(client_for(world.own).post(endpoint(world.own_customer),{},format="json"))
    assert [row["candidate"]["id"] for row in result] == [str(ordered[1]),str(ordered[0])]


def test_age_toggle_disabled_for_candidate_without_requirements(world):
    world.other_customer.min_building_age = world.other_customer.max_building_age = None
    world.other_customer.save()
    world.own_file.building_age = None
    world.own_file.save()
    result = rows(client_for(world.own).post(endpoint(world.own_file),{"hard_constraints":{"building_age":True}},format="json"))
    assert {row["candidate"]["id"] for row in result} == {str(world.other_customer.pk)}
    age = next(item for item in result[0]["criteria"] if item["code"] == "building_age")
    assert not age["applicable"] and age["reason_code"] == "age_not_requested"


def test_actual_candidate_cap_boundary(world):
    def candidate():
        return PropertyFile(workspace=world.ws, assigned_to=world.other,
            city=world.city, region=world.region, transaction_type="sale",
            area=100, bedrooms=2, total_price=1000, price_per_square_meter=0,
            code=f"PF-{uuid.uuid4().hex.upper()}")
    PropertyFile.objects.bulk_create([candidate() for _ in range(998)])
    client = client_for(world.own)
    with patch("apps.matching.live_services.evaluate_match", wraps=evaluate_match) as evaluate:
        response = client.post(endpoint(world.own_customer), {}, format="json")
        assert len(rows(response)) == 50
        assert response.data["count"] == 1000
        assert evaluate.call_count == 1000
    PropertyFile.objects.bulk_create([candidate()])
    with patch("apps.matching.live_services.evaluate_match") as evaluate:
        response = client.post(endpoint(world.own_customer), {}, format="json")
        assert response.status_code == 400
        assert response.data["candidates"].code == "candidate_limit_exceeded"
        evaluate.assert_not_called()
