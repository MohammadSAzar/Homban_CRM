import copy
import uuid
from dataclasses import asdict
from decimal import Decimal as D, localcontext

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.customers.models import Customer
from apps.locations.models import Region
from apps.matching.engine import evaluate_match
from apps.matching.models import MatchingProfile, WEIGHT_FIELDS
from apps.properties.models import PropertyFile


@pytest.fixture
def pair():
    ws = uuid.uuid4()
    region = Region(workspace_id=ws, is_active=True)
    file = PropertyFile(workspace_id=ws, region=region, status="active", transaction_type="sale",
                        area=D("100"), bedrooms=2, building_age=5, total_price=D("1000"),
                        parking=True, elevator=True, storage=True, balcony=True,
                        owner_name="SECRET_OWNER", owner_phone="SECRET_PHONE", address="SECRET_ADDRESS")
    customer = Customer(workspace_id=ws, customer_type="buyer", status="active", min_area=D("100"),
                        max_area=D("100"), bedrooms=2, budget=D("1000"), all_regions=False,
                        name="SECRET_CUSTOMER", mobile="SECRET_MOBILE", description="SECRET_NOTES")
    customer._prefetched_objects_cache = {"preferred_regions": [region]}
    return file, customer, MatchingProfile()


def criterion(result, name):
    return next(item for item in result.criteria if item.code == name)


def rent_pair(pair, *, deposit=0, rent=3000000, customer_deposit=0, customer_rent=3000000):
    file, customer, profile = pair
    file.transaction_type, customer.customer_type = "rent", "tenant"
    file.total_price = customer.budget = None
    file.deposit_amount, file.monthly_rent = D(deposit), D(rent)
    customer.deposit_budget, customer.monthly_rent_budget = D(customer_deposit), D(customer_rent)
    return file, customer, profile


@pytest.mark.parametrize("target,field,value,code", [
    (0,"workspace_id",uuid.uuid4(),"workspace_mismatch"),
    (0,"transaction_type","rent","type_mismatch"),
    (1,"customer_type","tenant","type_mismatch"),
    *[(0,"status",status,"file_not_active") for status in ("inactive","sold","rented","archived")],
    *[(1,"status",status,"customer_not_active") for status in ("inactive","completed","archived")],
])
def test_hard_eligibility(pair, target, field, value, code):
    setattr(pair[target], field, value)
    result = evaluate_match(*pair)
    assert not result.eligible and not result.recommended
    assert result.final_score is result.normalized_score is None
    assert result.rejection_code == code and result.explanations
    assert result.criteria == ()


@pytest.mark.parametrize("price,passed", [("800",True),("1200",True),("799.99",False),("1200.01",False)])
def test_sale_gate_boundaries(pair, price, passed):
    pair[0].total_price = D(price)
    result = evaluate_match(*pair)
    gate = result.budget_gate
    assert gate.passed is passed and result.eligible is passed
    assert (gate.customer_amount,gate.file_amount,gate.lower,gate.upper) == (D(1000),D(price),D(800),D(1200))
    assert gate.conversion_rate is None
    if not passed:
        assert result.rejection_code == "budget_outside_range"


def test_decimal_sale_precision_and_no_budget_points(pair):
    pair[1].budget = D("1000.01")
    pair[0].total_price = D("800.01")
    low = evaluate_match(*pair)
    assert low.budget_gate.lower == D("800.008")
    pair[0].total_price = D("1200.01")
    high = evaluate_match(*pair)
    assert high.final_score == low.final_score == 100
    assert {item.code for item in high.criteria} == set(WEIGHT_FIELDS)


@pytest.mark.parametrize("deposit,rent,passed", [(0,2400000,True),(0,3600000,True),(0,2399999,False),(0,3600001,False),(80000000,0,True),(120000000,0,True)])
def test_rent_gate_boundaries_and_zero_fields(pair, deposit, rent, passed):
    result = evaluate_match(*rent_pair(pair,deposit=deposit,rent=rent))
    assert result.eligible is passed
    assert result.budget_gate.customer_amount == 100000000
    assert result.budget_gate.lower == 80000000 and result.budget_gate.upper == 120000000
    assert result.budget_gate.conversion_rate == 3000000


def test_custom_rent_conversion_and_repeating_boundary(pair):
    args = rent_pair(pair,deposit=100000000,rent=0)
    assert evaluate_match(*args).eligible
    pair[2].rent_per_100m_deposit = D("6000000")
    assert not evaluate_match(*args).eligible
    assert evaluate_match(*args).budget_gate.customer_amount == 50000000
    rent_pair(pair,deposit=0,rent="0.80",customer_rent=1)
    pair[2].rent_per_100m_deposit = D(3)
    assert evaluate_match(*pair).eligible  # Exact boundary despite repeating 1/3.


@pytest.mark.parametrize("distance,factor", [(0,"1"),(5,".88"),(10,".76"),(15,".56"),(20,".32"),(25,".16"),(26,"0"),(D("5.01"),".76"),(D("25.01"),"0")])
@pytest.mark.parametrize("direction", [-1,1])
def test_area_curve_and_custom_weight(pair,distance,factor,direction):
    pair[0].area = D(100) + D(distance)*direction
    pair[2].area = D("37.5")
    item = criterion(evaluate_match(*pair),"area")
    assert item.earned == D("37.5") * D(factor)
    if distance:
        assert dict(item.references)["distance_percent"] == distance


def test_area_zero_maximum(pair):
    pair[1].min_area = pair[1].max_area = D(0)
    item = criterion(evaluate_match(*pair),"area")
    assert item.earned == 0 and item.reason_code == "area_zero_boundary"
    assert dict(item.references)["distance_percent"] is None


@pytest.mark.parametrize("bedrooms,earned", [(0,0),(1,16),(2,30),(3,16),(4,0)])
def test_bedroom_curve(pair,bedrooms,earned):
    pair[0].bedrooms, pair[2].bedrooms = bedrooms,D(30)
    assert criterion(evaluate_match(*pair),"bedrooms").earned == earned


@pytest.mark.parametrize("age,factor", [(10,"1"),(8,".8"),(12,".8"),(5,".5"),(15,".5"),(0,".2"),(20,".2"),(21,"0"),(None,"0")])
def test_age_curve(pair,age,factor):
    pair[0].building_age = age
    pair[1].min_building_age = pair[1].max_building_age = 10
    item = criterion(evaluate_match(*pair),"building_age")
    assert item.applicable and item.earned == 10*D(factor)
    if age is None:
        assert item.reason_code == "age_unknown"


@pytest.mark.parametrize("lower,upper,age,earned", [(None,10,0,10),(10,None,100,10),(None,10,13,5),(10,None,7,5)])
def test_one_sided_age_bounds(pair,lower,upper,age,earned):
    pair[1].min_building_age,pair[1].max_building_age = lower,upper
    pair[0].building_age = age
    assert criterion(evaluate_match(*pair),"building_age").earned == earned


@pytest.mark.parametrize("all_regions,preferred,active,earned,penalty", [
    (False,True,True,10,False),(False,False,True,0,True),
    (False,True,False,10,False),(False,False,False,0,True),
    (True,False,True,10,False),(True,False,False,0,False),
])
def test_region_history_semantics(pair,all_regions,preferred,active,earned,penalty):
    pair[1].all_regions = all_regions
    if not preferred:
        pair[1]._prefetched_objects_cache["preferred_regions"] = []
    pair[0].region.is_active = active
    result = evaluate_match(*pair)
    assert result.eligible
    assert criterion(result,"region").earned == earned
    assert result.region_penalty.applied is penalty
    with localcontext() as ctx:
        ctx.prec = 50
        assert result.final_score == result.normalized_score * (D(".90") if penalty else 1)


def test_foreign_region_fails_closed(pair):
    pair[0].region.workspace_id = uuid.uuid4()
    assert evaluate_match(*pair).rejection_code == "region_workspace_mismatch"


@pytest.mark.parametrize("name", ["parking","elevator","storage","balcony"])
@pytest.mark.parametrize("value", [True,False,None])
def test_facilities_quality_bonus(pair,name,value):
    setattr(pair[0],name,value)
    setattr(pair[2],name,D("6.25"))
    item = criterion(evaluate_match(*pair),name)
    assert item.applicable and item.earned == (D("6.25") if value is True else 0)
    assert item.reason_code == ("facility_available" if value is True else "facility_unknown" if value is None else "facility_unavailable")


def test_normalization_applicability_and_unscorable(pair):
    result = evaluate_match(*pair)
    assert result.available_points == result.earned_points == 60
    assert not criterion(result,"building_age").applicable
    pair[1].max_building_age = 5
    result = evaluate_match(*pair)
    assert result.available_points == result.earned_points == 70
    pair[1].max_building_age = None
    for name in WEIGHT_FIELDS:
        setattr(pair[2],name,D(0) if name != "building_age" else D(10))
    result = evaluate_match(*pair)
    assert result.eligible and not result.recommended
    assert result.available_points == 0 and result.final_score is result.normalized_score is None
    assert result.reason_code == "no_applicable_weight" and result.rejection_code is None


def test_threshold_inclusive_and_penalty_after_normalization(pair):
    for name in WEIGHT_FIELDS:
        setattr(pair[2],name,D(0))
    pair[2].area = pair[2].parking = D(10)
    pair[0].parking = False
    result = evaluate_match(*pair)
    assert result.normalized_score == result.final_score == 50 and result.recommended
    pair[2].minimum_score = D("50.01")
    result = evaluate_match(*pair)
    assert result.eligible and not result.recommended and result.reason_code == "below_threshold"
    pair[1]._prefetched_objects_cache["preferred_regions"] = []
    result = evaluate_match(*pair)
    assert result.normalized_score == 50 and result.final_score == 45


def test_purity_determinism_explanations_and_no_contacts(pair):
    pair[0].bedrooms = 3  # Repeating normalized Decimal result.
    def snapshot():
        return copy.deepcopy([{**obj.__dict__, "_state": vars(obj._state)} for obj in (*pair, pair[0].region)])
    snapshots = snapshot()
    first = evaluate_match(*pair)
    with localcontext() as ctx:
        ctx.prec = 6
        second = evaluate_match(*pair)
    assert first == second
    assert snapshots == snapshot()
    assert first.formula_version == "matching-v1"
    assert sum(item.earned for item in first.criteria) == first.earned_points
    assert all(any("\u0600" <= letter <= "\u06ff" for letter in item.explanation) for item in first.criteria)
    assert "SECRET" not in str(asdict(first))


@pytest.mark.django_db
def test_query_bound_prefetch_and_no_writes(django_assert_num_queries):
    from apps.accounts.models import User
    from apps.organizations.models import Workspace
    from apps.locations.models import City
    from apps.customers.services import create_customer
    workspace = Workspace.objects.create(name="مجموعه",slug="engine",customer_type="consultant")
    user = User.objects.create_user(username="engine",workspace=workspace,role="consultant")
    city = City.objects.create(workspace=workspace,name="شهر")
    region = Region.objects.create(workspace=workspace,city=city,name="منطقه")
    file = PropertyFile.objects.create(workspace=workspace,assigned_to=user,city=city,region=region,
                                      area=100,bedrooms=2,transaction_type="sale",total_price=1000)
    customer = create_customer(workspace=workspace,assigned_to=user,customer_type="buyer",name="مشتری",
                               min_area=100,max_area=100,bedrooms=2,budget=1000,preferred_regions=[region])
    profile = MatchingProfile(user=user)  # Evaluator must not persist a profile.
    file = PropertyFile.objects.get(pk=file.pk)
    customer = Customer.objects.get(pk=customer.pk)
    with CaptureQueriesContext(connection) as queries:
        result = evaluate_match(file,customer,profile)
    assert len(queries) == 2 and all(q["sql"].lstrip().upper().startswith("SELECT") for q in queries)
    assert "region" not in file._state.fields_cache
    assert not hasattr(customer,"_prefetched_objects_cache")
    file = PropertyFile.objects.select_related("region").get(pk=file.pk)
    customer = Customer.objects.prefetch_related("preferred_regions").get(pk=customer.pk)
    with django_assert_num_queries(0):
        assert evaluate_match(file,customer,profile) == result
    assert not MatchingProfile.objects.exists()


def test_rent_mixed_amounts_exact_equivalent(pair):
    args = rent_pair(pair,deposit=50000000,rent=1500000,customer_deposit=25000000,customer_rent=2250000)
    gate = evaluate_match(*args).budget_gate
    assert gate.passed and gate.file_amount == gate.customer_amount == 100000000


def test_zero_budget_gate(pair):
    pair[0].total_price = pair[1].budget = D(0)
    assert evaluate_match(*pair).eligible
    pair[0].total_price = D("0.01")
    assert not evaluate_match(*pair).eligible
    rent_pair(pair,deposit=0,rent=0,customer_deposit=0,customer_rent=0)
    assert evaluate_match(*pair).eligible
