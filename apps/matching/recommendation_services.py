"""Single-pair lifecycle commands, not generation or candidate discovery."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, localcontext

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Prefetch
from apps.locations.models import Region
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotFound, PermissionDenied

from apps.accounts.models import User
from apps.accounts.authentication import customer_can_authenticate
from apps.organizations.models import Workspace
from apps.properties.models import PropertyFile
from apps.customers.models import Customer
from .engine import evaluate_match
from .models import MatchRecommendation
from .recommendation_models import _LIFECYCLE_WRITE
from .services import own_profile
from .generation_events import suppress_events


@dataclass(frozen=True)
class ChangeContext:
    # Generation will determine this from real input changes, never score/updated_at.
    material_inputs_changed: bool = False
    sources_became_operational: bool = False

    def __post_init__(self):
        if type(self.material_inputs_changed) is not bool or type(self.sources_became_operational) is not bool:
            raise ValidationError(_("زمینه تغییر باید مقدار منطقی معتبر داشته باشد."))


def _stored_score(value):
    if value is None:
        return None
    # MySQL's maximum Decimal scale is 30. Eligibility is taken from the unrounded engine result.
    with localcontext() as context:
        context.prec = 65
        return value.quantize(Decimal("1e-30"), rounding=ROUND_DOWN)


def _lock_pair(viewer_id, file_id, customer_id):
    # Workspace -> User -> PropertyFile -> Customer, then projection/profile locks.
    workspace_id = PropertyFile.objects.values_list("workspace_id", flat=True).get(pk=file_id)
    Workspace.objects.select_for_update().get(pk=workspace_id)
    viewer = User.objects.select_for_update().select_related("workspace").get(pk=viewer_id)
    file = PropertyFile.objects.select_for_update().select_related("city", "region__city").get(pk=file_id)
    customer = Customer.objects.select_for_update().prefetch_related(Prefetch("preferred_regions", queryset=Region.objects.select_related("city"))).get(pk=customer_id)
    return viewer, file, customer


def _association_valid(viewer, file, customer):
    return bool(customer_can_authenticate(viewer) and viewer.role == User.Role.CONSULTANT
                and viewer.workspace_id == file.workspace_id == customer.workspace_id
                and viewer.pk in (file.assigned_to_id, customer.assigned_to_id))


def _apply_evaluation(row, result, *, source_valid, change, persist=True, reactivate=True):
    """Private sink: caller has evaluated fresh locked sources with the viewer's base profile."""
    was_new, previous_status = row._state.adding, row.user_status
    was_expired = not row.is_source_valid
    if reactivate and result.recommended and row.user_status in (row.Status.REJECTED, row.Status.DONE):
        if change.material_inputs_changed or change.sources_became_operational or was_expired:
            row.user_status = row.Status.NEW
    row.is_viewer_valid = True
    row.is_source_valid = source_valid
    row.is_currently_recommended = result.recommended
    row.current_score = _stored_score(result.final_score)
    row.minimum_score = result.minimum_score
    row.formula_version = result.formula_version
    row.last_evaluated_at = timezone.now()
    row._notification_event = ('created' if was_new else 'reactivated'
        if previous_status in (row.Status.REJECTED, row.Status.DONE) and row.user_status == row.Status.NEW else None)
    if persist:
        row.save(_token=_LIFECYCLE_WRITE)
        from apps.notifications.producers import recommendation_notification
        recommendation_notification(row, row._notification_event)
    return row


@transaction.atomic
def refresh_recommendation(*, viewer, property_file, customer, change=ChangeContext()):
    """Establish/reconcile one identity. Internal command, never a user payload interface.

    Evaluation is deliberately verified here against current base settings. Callers cannot
    submit scores or live runtime overrides. None means no row has ever qualified.
    """
    if not isinstance(change, ChangeContext):
        raise ValidationError(_("زمینه تغییر معتبر نیست."))
    viewer, file, customer = _lock_pair(viewer.pk, property_file.pk, customer.pk)
    row = MatchRecommendation.objects.select_for_update().filter(
        viewer=viewer, property_file=file, customer=customer).first()
    source_valid = file.status == "active" and customer.status == "active"
    if not _association_valid(viewer, file, customer):
        if row is None:
            raise ValidationError(_("مشاور مالک هیچ‌یک از منابع معتبر این مجموعه نیست."))
        # Preserve manual status, last evaluation and viewing history; do not expose sources.
        row.is_viewer_valid = row.is_currently_recommended = False
        row.is_source_valid = source_valid
        row.save(_token=_LIFECYCLE_WRITE)
        return row
    profile = own_profile(actor=viewer)
    result = evaluate_match(file, customer, profile)
    if row is None:
        if not result.recommended:
            return None
        row = MatchRecommendation(viewer=viewer, property_file=file, customer=customer)
    return _apply_evaluation(row, result, source_valid=source_valid, change=change)


@transaction.atomic
def change_manual_status(*, actor, recommendation_id, status):
    """SEEN represents an actual detail-open action and records its score baseline."""
    if status not in (MatchRecommendation.Status.SEEN, MatchRecommendation.Status.REJECTED, MatchRecommendation.Status.DONE):
        raise ValidationError(_("وضعیت دستی مجاز نیست."))
    # Fail closed before acquiring any foreign source locks or creating a profile.
    reference = MatchRecommendation.objects.for_viewer(actor).filter(pk=recommendation_id).first()
    if reference is None:
        raise NotFound(_("پیشنهاد یافت نشد."))
    viewer, file, customer = _lock_pair(actor.pk, reference.property_file_id, reference.customer_id)
    if not _association_valid(viewer, file, customer):
        raise PermissionDenied(_("دسترسی به پیشنهاد مجاز نیست."))
    row = MatchRecommendation.objects.select_for_update().get(pk=reference.pk)
    if status == row.Status.DONE and (file.assigned_to_id != viewer.pk or customer.assigned_to_id != viewer.pk):
        raise ValidationError(_("انجام‌شده فقط برای فایل و مشتری متعلق به خود مشاور مجاز است."))
    row.user_status = status
    if status == row.Status.SEEN:
        _record_view(row)
    row.save(_token=_LIFECYCLE_WRITE)
    return row


def _record_view(row):
    """Actual viewing acknowledges the current score without restoring rejected/done."""
    if row.user_status == row.Status.NEW:
        row.user_status = row.Status.SEEN
    row.score_at_last_view = row.current_score


@transaction.atomic
def open_recommendation(*, actor, recommendation_id):
    """Refresh exactly one authorized pair, preserve explicit decisions and record viewing."""
    reference = MatchRecommendation.objects.for_viewer(actor).filter(pk=recommendation_id).first()
    if reference is None:
        raise NotFound(_("پیشنهاد یافت نشد."))
    viewer, file, customer = _lock_pair(actor.pk, reference.property_file_id, reference.customer_id)
    if not _association_valid(viewer, file, customer):
        raise PermissionDenied(_("دسترسی به پیشنهاد مجاز نیست."))
    if (file.city.workspace_id != viewer.workspace_id or file.region.workspace_id != viewer.workspace_id
            or file.region.city_id != file.city_id
            or any(region.workspace_id != viewer.workspace_id or region.city.workspace_id != viewer.workspace_id
                   for region in customer.preferred_regions.all())):
        raise NotFound(_("پیشنهاد یافت نشد."))
    row = MatchRecommendation.objects.select_for_update().get(pk=reference.pk)
    # A lazy default profile here must not turn a one-pair read into generation.
    with suppress_events():
        profile = own_profile(actor=viewer)
    result = evaluate_match(file, customer, profile)
    _apply_evaluation(row, result, source_valid=file.status == customer.status == "active",
                      change=ChangeContext(), persist=False, reactivate=False)
    _record_view(row)
    row.save(_token=_LIFECYCLE_WRITE)
    return row, file, customer, result
