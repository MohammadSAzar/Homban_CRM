from decimal import Decimal as D
from unittest.mock import patch

import pytest
from django.db import connection, transaction, OperationalError
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import User
from apps.organizations.models import Workspace
from apps.locations.models import City, Region
from apps.customers.models import Customer, CustomerRegionPreference
from apps.customers.services import create_customer, set_preferred_regions
from apps.properties.models import PropertyFile
from apps.matching.models import MatchingProfile, MatchRecommendation as Recommendation, RecommendationWork as Work, SETTING_FIELDS
from apps.matching.services import own_profile
from apps.matching.recommendation_services import change_manual_status
from apps.matching.generation_events import record_work, publish_work, suppress_events, FILE_FIELDS, CUSTOMER_FIELDS
from apps.matching.generation import process_work, reconcile_workspace, recover_pending
from apps.matching.engine import evaluate_match

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    with suppress_events():
        ws = Workspace.objects.create(name="مجموعه", slug="generation", customer_type="agency_manager")
        a = User.objects.create_user(username="a", workspace=ws, role="consultant")
        b = User.objects.create_user(username="b", workspace=ws, role="consultant")
        third = User.objects.create_user(username="third", workspace=ws, role="consultant")
        city = City.objects.create(workspace=ws, name="شهر")
        region = Region.objects.create(workspace=ws, city=city, name="منطقه")
        file = PropertyFile.objects.create(workspace=ws, assigned_to=a, city=city, region=region,
            transaction_type="sale", area=100, bedrooms=2, total_price=1000,
            parking=True, elevator=True, storage=True, balcony=True)
        customer = create_customer(workspace=ws, assigned_to=b, name="private", mobile="private",
            customer_type="buyer", min_area=100, max_area=100, bedrooms=2, budget=1000, all_regions=True)
        own_profile(actor=a)
        own_profile(actor=b)
    return ws, a, b, third, file, customer


def drain(work):
    steps = 0
    while True:
        work.refresh_from_db()
        if work.completed:
            return steps
        assert steps < 200
        process_work(work.pk, work.step)
        steps += 1


def event(world, kind="file", target=None, material=True):
    return record_work(world[0].pk, kind, target or world[4].pk, material=material)


def generated(world):
    drain(event(world))
    return Recommendation.objects.get(viewer=world[1]), Recommendation.objects.get(viewer=world[2])


def test_both_viewers_use_independent_profiles_and_no_third_party(world):
    ws, a, b, third, file, customer = world
    file.parking = False
    file.save()
    own_profile(actor=b, data={"parking": 20})
    first, second = generated(world)
    assert first.current_score != second.current_score
    for viewer, row in ((a, first), (b, second)):
        profile = MatchingProfile.objects.get(user=viewer)
        file.refresh_from_db()
        customer.refresh_from_db()
        expected = evaluate_match(file, customer, profile)
        assert abs(row.current_score - expected.final_score) < D("1e-28")
    assert not MatchingProfile.objects.filter(user=third).exists()
    assert Recommendation.objects.count() == 2


def test_own_own_only_one_viewer(world):
    customer = world[5]
    customer.assigned_to = world[1]
    customer.save()
    drain(event(world))
    assert list(Recommendation.objects.values_list("viewer_id", flat=True)) == [world[1].pk]


@pytest.mark.parametrize("kind", ["file", "customer"])
def test_creation_captures_and_discovers(world, kind):
    item = world[4] if kind == "file" else world[5]
    # Delete only a source with no saved recommendations; recreate a canonical copy.
    item.delete()
    item.pk = __import__("uuid").uuid4()
    item.code = ""
    item._state.adding = True
    item.save()
    work = Work.objects.latest("pk")
    assert work.kind == kind and work.target_id == item.pk
    drain(work)
    assert Recommendation.objects.count() == 2


@pytest.mark.parametrize("field", [f for f in FILE_FIELDS if f not in ("city_id", "region_id", "transaction_type", "deposit_amount", "monthly_rent")])
def test_every_file_input_field_detected_with_partial_save(world, field):
    item = world[4]
    original = getattr(item, field)
    alternate = {"transaction_type": "rent", "status": "inactive", "assigned_to_id": world[2].pk,
                 "city_id": __import__("uuid").uuid4(), "region_id": __import__("uuid").uuid4()}.get(field)
    if alternate is None:
        alternate = not original if isinstance(original, bool) else 1 if original is None else original + 1
    setattr(item, field, alternate)
    item.save(update_fields=[field])
    assert Work.objects.filter(kind="file", target_id=item.pk).count() == 1


@pytest.mark.parametrize("field", [f for f in CUSTOMER_FIELDS if f not in ("customer_type", "deposit_budget", "monthly_rent_budget", "all_regions", "min_area")])
def test_customer_input_categories(world, field):
    item = world[5]
    original = getattr(item, field)
    value = {"status": "inactive", "assigned_to_id": world[1].pk}.get(field)
    if value is None:
        value = 1 if original is None else original + 1
    setattr(item, field, value)
    item.save(update_fields=[field])
    assert Work.objects.filter(kind="customer", target_id=item.pk).count() == 1


def test_irrelevant_metadata_and_unsaved_inputs_do_not_trigger(world):
    file, customer = world[4:]
    file.total_price = 2000  # Not included in update_fields.
    file.description = "notes"
    file.owner_phone = file.visit_contact_phone = "private"
    file.save(update_fields=["description", "owner_phone", "visit_contact_phone", "updated_at"])
    customer.mobile = "new"
    customer.save(update_fields=["mobile", "updated_at"])
    file.images.create(reference="private")
    file.valuable_reasons.create(reason="private")
    customer.valuable_reasons.create(reason="private")
    assert not Work.objects.exists()


def test_profile_only_viewer_new_discovery_and_no_other_score_write(world):
    ws, a, b, _, file, customer = world
    own_profile(actor=a, data={"sale_budget_upper_ratio": "1"})
    file.total_price = 1100
    file.save()
    drain(event(world))
    assert not Recommendation.objects.filter(viewer=a).exists()
    other = Recommendation.objects.values().get(viewer=b)
    own_profile(actor=a, data={"sale_budget_upper_ratio": "1.2"})
    drain(Work.objects.latest("pk"))
    assert Recommendation.objects.filter(viewer=a).exists()
    assert Recommendation.objects.values().get(viewer=b) == other


@pytest.mark.parametrize("field", SETTING_FIELDS)
def test_profile_fields_trigger_only_profile_work(world, field):
    profile = MatchingProfile.objects.get(user=world[1])
    value = getattr(profile, field)
    value = D("0.9") if field == "sale_budget_lower_ratio" else value + 1
    own_profile(actor=world[1], data={field: value})
    assert list(Work.objects.values_list("kind", "target_id")) == [("profile", world[1].pk)]


def test_threshold_discovery_rejection_reactivation_and_seen_baseline(world):
    a, file = world[1], world[4]
    file.parking = False
    file.save()
    own_profile(actor=a, data={"minimum_score": 100})
    drain(event(world))
    assert not Recommendation.objects.filter(viewer=a).exists()
    own_profile(actor=a, data={"minimum_score": 50})
    drain(Work.objects.latest("pk"))
    row = Recommendation.objects.get(viewer=a)
    change_manual_status(actor=a, recommendation_id=row.pk, status="seen")
    row.refresh_from_db()
    baseline = row.score_at_last_view
    file.parking = True
    file.save()
    drain(Work.objects.latest("pk"))
    row.refresh_from_db()
    assert row.user_status == "seen" and row.score_at_last_view == baseline and row.has_improved_score
    change_manual_status(actor=a, recommendation_id=row.pk, status="rejected")
    drain(event(world, material=False))
    row.refresh_from_db()
    assert row.user_status == "rejected"
    file.area = 101
    file.save()
    drain(Work.objects.latest("pk"))
    row.refresh_from_db()
    assert row.user_status == "new"


def test_below_threshold_expiry_restoration_and_assignment(world):
    ws, a, b, third, file, customer = world
    first, _ = generated(world)
    change_manual_status(actor=a, recommendation_id=first.pk, status="rejected")
    file.total_price = 2000
    file.save()
    drain(Work.objects.latest("pk"))
    first.refresh_from_db()
    assert first.is_source_valid and not first.is_currently_recommended and first.user_status == "rejected"
    file.status = "inactive"
    file.save()
    drain(Work.objects.latest("pk"))
    first.refresh_from_db()
    assert not first.is_source_valid and first.user_status == "rejected"
    file.status, file.total_price = "active", 1000
    file.save()
    drain(Work.objects.latest("pk"))
    first.refresh_from_db()
    assert first.user_status == "new"
    file.assigned_to = third
    file.save()
    assert not Recommendation.objects.active_for_viewer(a).exists()
    drain(Work.objects.latest("pk"))
    first.refresh_from_db()
    assert not first.is_viewer_valid
    assert set(Recommendation.objects.filter(is_viewer_valid=True).values_list("viewer_id", flat=True)) == {b.pk, third.pk}


def test_preferences_all_regions_and_region_activation_scoped(world):
    ws, a, b, _, file, customer = world
    region = Region.objects.create(workspace=ws, city=file.city, name="other")
    customer.all_regions = False
    customer.save(preferred_regions=[region])
    assert Work.objects.filter(kind="customer").count() == 1
    drain(Work.objects.latest("pk"))
    first = Recommendation.objects.get(viewer=a)
    score = first.current_score
    customer.preferred_regions.add(file.region)
    drain(Work.objects.latest("pk"))
    first.refresh_from_db()
    assert first.current_score > score
    count = Work.objects.count()
    customer.preferred_regions.add(file.region)
    assert Work.objects.count() == count
    customer.preferred_regions.remove(region)
    assert Work.objects.count() == count + 1
    customer.all_regions = True
    customer.save()
    drain(Work.objects.latest("pk"))
    file.region.is_active = False
    file.region.save()
    work = Work.objects.latest("pk")
    assert work.kind == "region" and work.workspace_id == ws.pk
    drain(work)
    first.refresh_from_db()
    assert first.current_score < 100 and first.is_source_valid


def test_on_commit_rollback_and_queue_failure_durable(world, django_capture_on_commit_callbacks, isolated_matching_broker):
    with django_capture_on_commit_callbacks(execute=True):
        with transaction.atomic():
            work = event(world)
            isolated_matching_broker.assert_not_called()
    isolated_matching_broker.assert_called_once_with(work.pk, 0)
    isolated_matching_broker.reset_mock()
    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError), transaction.atomic():
            world[4].area = 105
            world[4].save()
            raise RuntimeError()
    isolated_matching_broker.assert_not_called()
    assert Work.objects.count() == 1
    with patch("apps.matching.tasks.generate_recommendations.delay", side_effect=ConnectionError):
        publish_work(work.pk)
    assert Work.objects.filter(pk=work.pk, completed=False).exists()


def test_duplicate_retry_stale_order_and_manual_status(world):
    older = event(world)
    world[4].parking = False
    world[4].save()
    newer = Work.objects.latest("pk")
    drain(newer)
    row = Recommendation.objects.get(viewer=world[1])
    change_manual_status(actor=world[1], recommendation_id=row.pk, status="rejected")
    before = Recommendation.objects.values().get(pk=row.pk)
    drain(older)
    assert Recommendation.objects.values().get(pk=row.pk) == before
    assert not process_work(newer.pk, 0)
    work = event(world)
    with patch("apps.matching.generation.evaluate_match", side_effect=OperationalError):
        with pytest.raises(OperationalError):
            process_work(work.pk, 0)
    work.refresh_from_db()
    assert work.step == 0
    drain(work)


def test_bounded_continuation_recovery_and_constant_relation_queries(world):
    ws, a, b, _, file, customer = world
    with suppress_events():
        for i in range(5):
            create_customer(workspace=ws, assigned_to=b, name=str(i), customer_type="buyer", budget=1000,
                min_area=100, max_area=100, bedrooms=2, all_regions=True)
    work = event(world)
    with patch("apps.matching.generation.BATCH_SIZE", 2), patch("apps.matching.generation.evaluate_match", wraps=evaluate_match) as scoring:
        assert process_work(work.pk, 0)  # empty existing phase
        assert process_work(work.pk, 1)
        assert scoring.call_count == 4
        assert not process_work(work.pk, 1)  # duplicate cursor
        drain(work)
    assert Recommendation.objects.count() == 12
    recovered = reconcile_workspace(ws.pk, viewer_id=a.pk)
    with patch("apps.matching.generation.publish_work") as publish:
        cursor = recover_pending(limit=1)
        assert cursor == recovered.pk and publish.call_count == 1
    with pytest.raises(ValueError):
        recover_pending(limit=101)
    with pytest.raises(ValueError):
        reconcile_workspace(ws.pk, viewer_id=__import__("uuid").uuid4())
    # Compare the same existing phase with one versus six rows; relation access is batched.
    counts = []
    for limit in (1, 6):
        work = event(world, material=False)
        with patch("apps.matching.generation.BATCH_SIZE", limit), CaptureQueriesContext(connection) as captured:
            process_work(work.pk, 0)
        counts.append(len(captured))
    assert counts[0] == counts[1]


def test_type_location_and_financial_changes_use_canonical_writes(world):
    ws, a, b, _, file, customer = world
    generated(world)
    file.transaction_type, file.total_price = "rent", None
    file.deposit_amount, file.monthly_rent = 100000000, 0
    file.save()
    drain(Work.objects.latest("pk"))
    assert not Recommendation.objects.filter(is_currently_recommended=True).exists()
    customer.customer_type, customer.budget = "tenant", None
    customer.deposit_budget, customer.monthly_rent_budget = 0, 3000000
    customer.min_area = 90
    customer.save()
    drain(Work.objects.latest("pk"))
    assert Recommendation.objects.filter(is_currently_recommended=True).count() == 2
    for item, field, value in ((file, "deposit_amount", 99000000), (file, "monthly_rent", 10000),
                              (customer, "deposit_budget", 1000000), (customer, "monthly_rent_budget", 2900000)):
        count = Work.objects.count()
        setattr(item, field, value)
        item.save(update_fields=[field])
        assert Work.objects.count() == count + 1
        drain(Work.objects.latest("pk"))
    own_profile(actor=a, data={"rent_per_100m_deposit": 6000000})
    drain(Work.objects.latest("pk"))
    assert not Recommendation.objects.get(viewer=a).is_currently_recommended
    assert Recommendation.objects.get(viewer=b).is_currently_recommended
    city = City.objects.create(workspace=ws, name="new")
    region = Region.objects.create(workspace=ws, city=city, name="new")
    count = Work.objects.count()
    file.city, file.region = city, region
    file.save(update_fields=["city", "region"])
    assert Work.objects.count() == count + 1


def test_customer_assignment_invalidation_and_new_budget_pair(world):
    ws, a, b, third, file, customer = world
    customer.budget = 500
    customer.save()
    drain(Work.objects.latest("pk"))
    assert not Recommendation.objects.exists()
    customer.budget = 1000
    customer.save()
    drain(Work.objects.latest("pk"))
    old = Recommendation.objects.get(viewer=b)
    customer.assigned_to = third
    customer.save()
    drain(Work.objects.latest("pk"))
    old.refresh_from_db()
    assert not old.is_viewer_valid
    assert Recommendation.objects.get(viewer=third).is_currently_recommended
    customer.status = "inactive"
    customer.save()
    drain(Work.objects.latest("pk"))
    assert not Recommendation.objects.filter(is_source_valid=True).exists()


def test_foreign_workspace_is_never_evaluated_or_updated(world):
    ws, a, b, _, file, customer = world
    with suppress_events():
        foreign_ws = Workspace.objects.create(name="foreign", slug="foreign", customer_type="consultant")
        foreign = User.objects.create_user(username="other", role="consultant", workspace=foreign_ws)
        foreign_customer = create_customer(workspace=foreign_ws, assigned_to=foreign, name="SECRET",
            customer_type="buyer", budget=1000, min_area=100, max_area=100, bedrooms=2, all_regions=True)
    with patch("apps.matching.generation.evaluate_match", wraps=evaluate_match) as calculate:
        drain(event(world))
    assert all(call.args[0].workspace_id == call.args[1].workspace_id == ws.pk for call in calculate.call_args_list)
    assert not MatchingProfile.objects.filter(user=foreign).exists()
    assert not Recommendation.objects.filter(customer=foreign_customer).exists()
    # Cross-workspace reassignment is still rejected by the original canonical domain.
    from django.core.exceptions import ValidationError
    file.assigned_to = foreign
    with pytest.raises(ValidationError):
        file.save()


def test_preference_direct_through_save_and_rollback(world):
    ws, _, _, _, file, customer = world
    region = Region.objects.create(workspace=ws, city=file.city, name="second")
    customer.all_regions = False
    customer.save(preferred_regions=[file.region])
    count = Work.objects.count()
    preference = CustomerRegionPreference.objects.create(customer=customer, region=region)
    assert Work.objects.count() == count + 1
    preference.delete()
    assert Work.objects.count() == count + 2
    count = Work.objects.count()
    with pytest.raises(RuntimeError), transaction.atomic():
        set_preferred_regions(customer=customer, regions=[region])
        raise RuntimeError()
    assert Work.objects.count() == count
    assert list(customer.preferred_regions.values_list("pk", flat=True)) == [file.region_id]


def test_done_reactivation_and_no_reset_from_metadata(world):
    customer = world[5]
    customer.assigned_to = world[1]
    customer.save()
    drain(Work.objects.latest("pk"))
    row = Recommendation.objects.get(viewer=world[1])
    change_manual_status(actor=world[1], recommendation_id=row.pk, status="done")
    customer.description = "no event"
    count = Work.objects.count()
    customer.save()
    assert Work.objects.count() == count
    drain(reconcile_workspace(world[0].pk))
    row.refresh_from_db()
    assert row.user_status == "done"
    customer.max_area = 110
    customer.save()
    drain(Work.objects.latest("pk"))
    row.refresh_from_db()
    assert row.user_status == "new"


def test_task_wrapper_retry_configuration_and_recovery_continuation(world):
    from apps.matching.tasks import generate_recommendations, recover_recommendations
    work = event(world)
    assert generate_recommendations.run(work.pk, 0)
    with patch("apps.matching.tasks.recover_pending", return_value=work.pk), patch.object(recover_recommendations, "delay") as next_page:
        recover_recommendations.run()
        next_page.assert_called_once_with(work.pk)
    with patch("apps.matching.tasks.process_work", side_effect=OperationalError), patch.object(generate_recommendations, "retry", side_effect=RuntimeError("retry requested")) as retry:
        with pytest.raises(RuntimeError, match="retry requested"):
            generate_recommendations.run(work.pk, 1)
        retry.assert_called_once()


@pytest.mark.parametrize("scope", ["viewer", "workspace"])
def test_entitlement_events_invalidate_without_material_reactivation(world, scope):
    first, _ = generated(world)
    change_manual_status(actor=world[1], recommendation_id=first.pk, status="rejected")
    target = world[1] if scope == "viewer" else world[0]
    target.is_active = False
    target.save()
    work = Work.objects.latest("pk")
    assert not work.material
    drain(work)
    first.refresh_from_db()
    assert not first.is_viewer_valid
    target.is_active = True
    target.save()
    drain(Work.objects.latest("pk"))
    first.refresh_from_db()
    assert first.is_viewer_valid and first.user_status == "rejected"


def test_real_concurrent_duplicate_task_serialized(world, transactional_db):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import close_old_connections
    work = event(world)
    barrier = Barrier(2)
    def run():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return process_work(work.pk, 0)
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert sorted(results) == [False, True]
    drain(work)
    assert Recommendation.objects.count() == 2


def test_recovery_overtaking_material_event_preserves_reactivation(world):
    row, _ = generated(world)
    change_manual_status(actor=world[1], recommendation_id=row.pk, status="rejected")
    world[4].area = 101
    world[4].save()
    older = Work.objects.latest("pk")
    newer = reconcile_workspace(world[0].pk)
    drain(newer)
    row.refresh_from_db()
    assert row.user_status == "new"
    change_manual_status(actor=world[1], recommendation_id=row.pk, status="rejected")
    before = Recommendation.objects.values().get(pk=row.pk)
    drain(older)
    assert Recommendation.objects.values().get(pk=row.pk) == before
