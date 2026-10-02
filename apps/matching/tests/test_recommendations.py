from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal as D
from threading import Barrier
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, models, transaction
from django.db.models.deletion import ProtectedError
from rest_framework.exceptions import NotFound

from apps.accounts.models import User
from apps.organizations.models import Workspace
from apps.locations.models import City, Region
from apps.properties.models import PropertyFile
from apps.customers.services import create_customer
from apps.matching.models import MatchRecommendation as Recommendation, MatchingProfile
from apps.matching.recommendation_models import _LIFECYCLE_WRITE
from apps.matching.recommendation_services import refresh_recommendation, change_manual_status, ChangeContext
from apps.matching.services import own_profile

pytestmark = pytest.mark.django_db


@pytest.fixture
def pair():
    ws = Workspace.objects.create(name="مجموعه", slug="recommendation", customer_type="consultant")
    viewer = User.objects.create_user(username="first", workspace=ws, role="consultant")
    other = User.objects.create_user(username="other", workspace=ws, role="consultant")
    city = City.objects.create(workspace=ws, name="شهر")
    region = Region.objects.create(workspace=ws, city=city, name="منطقه")
    file = PropertyFile.objects.create(workspace=ws, assigned_to=viewer, city=city, region=region,
        transaction_type="sale", area=100, bedrooms=2, total_price=1000, parking=False,
        elevator=True, storage=True, balcony=True, description="PRIVATE", owner_phone="PRIVATE")
    customer = create_customer(workspace=ws, assigned_to=viewer, name="PRIVATE", mobile="PRIVATE",
        customer_type="buyer", min_area=100, max_area=100, bedrooms=2, budget=1000, all_regions=True)
    return viewer, other, file, customer


def refresh(pair, **kwargs):
    viewer, _, file, customer = pair
    return refresh_recommendation(viewer=viewer, property_file=file, customer=customer, **kwargs)


def manual(pair, row, status):
    return change_manual_status(actor=pair[0], recommendation_id=row.pk, status=status)


def test_initial_repeated_upsert_and_no_source_mutation(pair):
    viewer, _, file, customer = pair
    before = (dict(file.__dict__), dict(customer.__dict__))
    row = refresh(pair)
    assert row.user_status == "new" and row.current_score > 50
    assert row.is_source_valid and row.is_viewer_valid and row.is_currently_recommended
    assert row.score_at_last_view is None and row.formula_version == "matching-v1"
    assert row.last_evaluated_at.tzinfo and row.created_at.tzinfo
    assert refresh(pair).pk == row.pk and Recommendation.objects.count() == 1
    assert (file.__dict__, customer.__dict__) == before
    assert "PRIVATE" not in str(Recommendation.objects.values().get())
    assert not {"workspace", "profile", "notes", "description", "result", "phone"} & {f.name for f in Recommendation._meta.fields}


def test_two_owners_independent_profiles_status_and_done_restriction(pair):
    viewer, other, file, customer = pair
    customer.assigned_to = other
    customer.save()
    own_profile(actor=other, data={"parking": 20})
    first = refresh(pair)
    second = refresh_recommendation(viewer=other, property_file=file, customer=customer)
    assert first.current_score != second.current_score
    manual(pair, first, "rejected")
    second.refresh_from_db()
    assert second.user_status == "new"
    for actor, row in ((viewer, first), (other, second)):
        with pytest.raises(ValidationError):
            change_manual_status(actor=actor, recommendation_id=row.pk, status="done")
    with pytest.raises(NotFound):
        change_manual_status(actor=viewer, recommendation_id=second.pk, status="seen")


@pytest.mark.parametrize("kind", ["unrelated", "foreign", "role", "inactive", "inactive_workspace"])
def test_invalid_viewer_rejected(pair, kind):
    viewer, other, file, customer = pair
    if kind == "unrelated":
        viewer = other
    elif kind == "foreign":
        viewer.workspace = Workspace.objects.create(name="دیگر", slug="foreign", customer_type="consultant")
        viewer.save()
    elif kind == "role":
        viewer.role = "agency_manager"
        viewer.is_staff = viewer.is_superuser = viewer.is_workspace_owner = True
        viewer.save()
    elif kind == "inactive":
        viewer.is_active = False
        viewer.save()
    else:
        viewer.workspace.is_active = False
        viewer.workspace.save()
    with pytest.raises(ValidationError):
        refresh_recommendation(viewer=viewer, property_file=file, customer=customer)
    assert not Recommendation.objects.exists()


def test_cross_workspace_pair_rejected(pair):
    viewer, _, file, customer = pair
    # Deliberately corrupt via raw bulk write to exercise the service boundary.
    ws = Workspace.objects.create(name="دیگر", slug="foreign", customer_type="consultant")
    type(customer).objects.filter(pk=customer.pk).update(workspace=ws)
    with pytest.raises(ValidationError):
        refresh(pair)
    assert not Recommendation.objects.exists()


@pytest.mark.parametrize("status", ["seen", "rejected", "done"])
def test_manual_reversible_no_scoring(pair, status):
    row = refresh(pair)
    evaluated = row.last_evaluated_at
    with patch("apps.matching.recommendation_services.evaluate_match", side_effect=AssertionError("must not score")):
        row = manual(pair, row, status)
        assert row.user_status == status
        row = manual(pair, row, "seen")
        assert row.score_at_last_view == row.current_score
        row = manual(pair, row, "rejected")
        assert row.user_status == "rejected" and row.last_evaluated_at == evaluated


@pytest.mark.parametrize("status", ["new", "expired", "invalid"])
def test_invalid_manual_target(pair, status):
    row = refresh(pair)
    with pytest.raises(ValidationError):
        manual(pair, row, status)


def test_below_threshold_not_expired_and_no_initial_unqualified_row(pair):
    viewer, _, file, _ = pair
    own_profile(actor=viewer, data={"minimum_score": 100})
    assert refresh(pair) is None
    own_profile(actor=viewer, data={"minimum_score": 50})
    row = manual(pair, refresh(pair), "rejected")
    own_profile(actor=viewer, data={"minimum_score": 100})
    row = refresh(pair)
    assert not row.is_currently_recommended and row.is_source_valid and row.user_status == "rejected"
    file.status = "inactive"
    file.save()
    row = refresh(pair)
    assert not row.is_source_valid and not row.is_currently_recommended
    assert row.user_status == "rejected" and row.current_score is None


@pytest.mark.parametrize("status", ["done", "rejected"])
def test_material_reactivation_only_when_recommended(pair, status):
    viewer, _, file, _ = pair
    row = manual(pair, refresh(pair), status)
    assert refresh(pair).user_status == status
    file.description = "different irrelevant metadata"
    file.save()
    assert refresh(pair).user_status == status
    file.parking = True
    file.save()
    # Score variation alone is not an input-change detector.
    assert refresh(pair).user_status == status
    own_profile(actor=viewer, data={"minimum_score": 100, "area": 0, "parking": 0, "bedrooms": 0, "region": 0, "elevator": 0, "storage": 0, "balcony": 0})
    assert refresh(pair, change=ChangeContext(material_inputs_changed=True)).user_status == status
    own_profile(actor=viewer, reset=True)
    assert refresh(pair, change=ChangeContext(material_inputs_changed=True)).user_status == "new"
    assert Recommendation.objects.count() == 1


@pytest.mark.parametrize("observed", [True, False])
def test_expiry_reactivation_preserves_history(pair, observed):
    row = manual(pair, refresh(pair), "seen")
    baseline = row.score_at_last_view
    row = manual(pair, row, "done")
    pair[2].status = "inactive"
    pair[2].save()
    if observed:
        row = refresh(pair)
        assert row.user_status == "done" and not row.is_source_valid
    pair[2].status = "active"
    pair[2].save()
    row = refresh(pair, change=ChangeContext(sources_became_operational=not observed))
    assert row.user_status == "new" and row.score_at_last_view == baseline


@pytest.mark.parametrize("status", ["new", "seen"])
def test_refresh_does_not_reset_open_status(pair, status):
    row = refresh(pair)
    if status == "seen":
        row = manual(pair, row, status)
    pair[2].parking = True
    pair[2].save()
    assert refresh(pair, change=ChangeContext(material_inputs_changed=True)).user_status == status


def test_improvement_baseline_actual_view_and_threshold(pair):
    viewer, _, file, _ = pair
    row = manual(pair, refresh(pair), "seen")
    baseline = row.score_at_last_view
    file.parking = True
    file.save()
    row = refresh(pair)
    assert row.has_improved_score and row.score_at_last_view == baseline
    assert refresh(pair).score_at_last_view == baseline
    own_profile(actor=viewer, data={"minimum_score": 99})
    assert not refresh(pair).has_improved_score  # Viewed score no longer reaches current threshold.
    own_profile(actor=viewer, reset=True)
    row = refresh(pair)
    assert row.has_improved_score
    row = manual(pair, row, "seen")
    assert row.score_at_last_view == row.current_score and not row.has_improved_score
    file.parking = False
    file.save()
    assert not refresh(pair).has_improved_score


def test_reassignment_history_and_immediate_scope_revocation(pair):
    viewer, other, file, customer = pair
    row = manual(pair, refresh(pair), "done")
    history = (row.current_score, row.last_evaluated_at, row.user_status)
    file.assigned_to = other
    file.save()
    customer.assigned_to = other
    customer.save()
    assert not Recommendation.objects.active_for_viewer(viewer).exists()
    assert not Recommendation.objects.for_viewer(viewer).exists()
    with pytest.raises(NotFound):
        manual(pair, row, "seen")
    row = refresh(pair)
    assert not row.is_viewer_valid and not row.is_currently_recommended
    assert (row.current_score, row.last_evaluated_at, row.user_status) == history
    newer = refresh_recommendation(viewer=other, property_file=file, customer=customer)
    assert newer.pk != row.pk and Recommendation.objects.count() == 2


def test_done_history_survives_partial_ownership_loss(pair):
    row = manual(pair, refresh(pair), "done")
    pair[2].assigned_to = pair[1]
    pair[2].save()
    assert refresh(pair).user_status == "done"
    with pytest.raises(ValidationError):
        manual(pair, row, "done")
    assert manual(pair, row, "seen").user_status == "seen"


@pytest.mark.parametrize("operation", ["save", "partial", "update", "bulk_update", "bulk_create", "delete", "query_delete"])
def test_normal_write_bypass_denied(pair, operation):
    row = refresh(pair)
    calls = {
        "save": lambda: row.save(), "partial": lambda: row.save(update_fields=["current_score"]),
        "update": lambda: Recommendation.objects.filter(pk=row.pk).update(current_score=100),
        "bulk_update": lambda: Recommendation.objects.bulk_update([row], ["current_score"]),
        "bulk_create": lambda: Recommendation.objects.bulk_create([row]),
        "delete": lambda: row.delete(), "query_delete": lambda: Recommendation.objects.all().delete(),
    }
    with pytest.raises(ValidationError):
        calls[operation]()
    assert Recommendation.objects.count() == 1


def test_model_identity_partial_validation_and_history_protection(pair):
    row = refresh(pair)
    row.viewer = pair[1]
    with pytest.raises(ValidationError):
        row.save(_token=_LIFECYCLE_WRITE, update_fields=["viewer"])
    row.refresh_from_db()
    row.is_source_valid = False
    row.is_currently_recommended = False
    with pytest.raises(ValidationError):
        row.save(_token=_LIFECYCLE_WRITE, update_fields=["is_source_valid"])
    row.save(_token=_LIFECYCLE_WRITE, update_fields=[])
    row.refresh_from_db()
    assert row.is_source_valid
    for source in (pair[0], pair[2], pair[3], pair[0].workspace):
        with pytest.raises(ProtectedError):
            source.delete()


@pytest.mark.parametrize("values", [{"current_score": -1}, {"current_score": 101}, {"minimum_score": -1},
    {"score_at_last_view": 101}, {"user_status": "expired"}, {"is_source_valid": False}])
def test_database_constraints_even_if_custom_queryset_bypassed(pair, values):
    row = refresh(pair)
    with pytest.raises(IntegrityError), transaction.atomic():
        models.QuerySet(model=Recommendation).filter(pk=row.pk).update(**values)


def test_database_unique_pair(pair):
    row = refresh(pair)
    row.pk = None
    row.id = __import__("uuid").uuid4()
    with pytest.raises(IntegrityError), transaction.atomic():
        models.QuerySet(model=Recommendation).bulk_create([row])


def test_authoritative_unrounded_decision_and_no_result_json(pair):
    from apps.matching.engine import evaluate_match
    base = own_profile(actor=pair[0])
    result = evaluate_match(pair[2], pair[3], base)
    result = replace(result, final_score=D("49.9999999999999999999999999999999999999"), recommended=False)
    with patch("apps.matching.recommendation_services.evaluate_match", return_value=result):
        assert refresh(pair) is None
    row = refresh(pair)
    with patch("apps.matching.recommendation_services.evaluate_match", return_value=result):
        row = refresh(pair)
    assert not row.is_currently_recommended and row.current_score < 50


def test_change_context_strict(pair):
    with pytest.raises(ValidationError):
        ChangeContext(material_inputs_changed="true")
    with pytest.raises(ValidationError):
        refresh(pair, change={})


@pytest.mark.django_db(transaction=True)
def test_concurrent_creation_unique(pair):
    barrier = Barrier(2)
    def run():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return refresh(pair).pk
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: run(), range(2)))
    assert ids[0] == ids[1] and Recommendation.objects.count() == 1


def test_model_validation_for_missing_foreign_and_unrelated_references(pair):
    import uuid
    row = refresh(pair)
    values = {field.attname: getattr(row, field.attname) for field in Recommendation._meta.fields
              if field.name not in ("id", "created_at", "updated_at")}
    for change in ({"viewer_id": pair[1].pk}, {"customer_id": uuid.uuid4()}, {"viewer_id": None}):
        invalid = Recommendation(**{**values, **change})
        with pytest.raises(ValidationError):
            invalid.full_clean()
    pair[3].assigned_to = pair[1]
    pair[3].save()
    invalid = Recommendation(**{**values, "user_status": "done"})
    with pytest.raises(ValidationError):
        invalid.full_clean()


def test_inactive_viewer_reconciliation_and_expired_view_baseline(pair):
    row = refresh(pair)
    pair[2].status = "inactive"
    pair[2].save()
    assert not Recommendation.objects.active_for_viewer(pair[0]).exists()
    row = refresh(pair)
    row = manual(pair, row, "seen")
    assert row.score_at_last_view is None and not row.has_improved_score
    pair[0].is_active = False
    pair[0].save()
    row = refresh(pair)
    assert not row.is_viewer_valid and not row.is_source_valid
    with pytest.raises(NotFound):
        manual(pair, row, "rejected")


def test_invalid_viewer_flags_cannot_be_kept_true_on_model_save(pair):
    row = refresh(pair)
    pair[2].assigned_to = pair[1]
    pair[2].save()
    pair[3].assigned_to = pair[1]
    pair[3].save()
    with pytest.raises(ValidationError):
        row.save(_token=_LIFECYCLE_WRITE)
    assert not refresh(pair).is_viewer_valid


def test_manual_rechecks_ownership_after_initial_lookup(pair):
    from apps.matching.recommendation_services import _lock_pair
    from rest_framework.exceptions import PermissionDenied
    row = refresh(pair)
    def reassigned(*args):
        viewer, file, customer = _lock_pair(*args)
        file.assigned_to_id = customer.assigned_to_id = pair[1].pk
        return viewer, file, customer
    with patch("apps.matching.recommendation_services._lock_pair", side_effect=reassigned):
        with pytest.raises(PermissionDenied):
            manual(pair, row, "seen")
    row.refresh_from_db()
    assert row.user_status == "new"
