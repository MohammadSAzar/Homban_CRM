"""Bounded, workspace-locked generation using the existing pair engine and lifecycle."""
from django.db import models, transaction
from django.db.models import F, Q, Max
from django.utils import timezone

from apps.accounts.models import User
from apps.organizations.models import Workspace
from apps.properties.models import PropertyFile
from apps.customers.models import Customer
from .models import MatchingProfile, MatchRecommendation, RecommendationWork
from .engine import evaluate_match
from .recommendation_services import _apply_evaluation, _association_valid, ChangeContext
from .services import own_profile
from .generation_events import publish_work, record_work, suppress_events

BATCH_SIZE = 25
WRITE_FIELDS = ("user_status", "current_score", "minimum_score", "formula_version", "is_viewer_valid",
                "is_source_valid", "is_currently_recommended", "last_evaluated_at", "updated_at", "last_generation_event")


def _existing(work):
    rows = MatchRecommendation.objects.filter(property_file__workspace_id=work.workspace_id)
    if work.kind == "file":
        rows = rows.filter(property_file_id=work.target_id)
    elif work.kind == "customer":
        rows = rows.filter(customer_id=work.target_id)
    elif work.kind == "profile":
        rows = rows.filter(viewer_id=work.target_id)
    elif work.kind == "region":
        rows = rows.filter(property_file__region_id=work.target_id, customer__all_regions=True)
    if work.after_id:
        rows = rows.filter(pk__gt=work.after_id)
    return rows.order_by("pk")


def _files(work):
    files = PropertyFile.objects.filter(workspace_id=work.workspace_id, status="active",
        assigned_to__workspace_id=work.workspace_id, assigned_to__role="consultant",
        city__workspace_id=work.workspace_id, region__workspace_id=work.workspace_id,
        region__city__workspace_id=work.workspace_id, region__city_id=F("city_id"))
    if work.kind == "file":
        files = files.filter(pk=work.target_id)
    elif work.kind == "region":
        files = files.filter(region_id=work.target_id)
    if work.file_id:
        files = files.filter(pk=work.file_id)
    elif work.after_id:
        files = files.filter(pk__gt=work.after_id)
    return files.order_by("pk")


def _discover(work):
    # At most one file and BATCH_SIZE customers per continuation. No Cartesian load.
    if not work.workspace.is_active:
        work.completed = True
        return []
    if work.kind == "profile" and not User.objects.filter(pk=work.target_id, workspace_id=work.workspace_id,
                                                         role="consultant", is_active=True).exists():
        work.completed = True
        return []
    file = _files(work).first()
    if file is None:
        work.completed = True
        return []
    customers = Customer.objects.filter(workspace_id=work.workspace_id, status="active",
        assigned_to__workspace_id=work.workspace_id, assigned_to__role="consultant",
        customer_type="buyer" if file.transaction_type == "sale" else "tenant")
    if work.kind == "customer":
        customers = customers.filter(pk=work.target_id)
    elif work.kind == "profile" and file.assigned_to_id != work.target_id:
        customers = customers.filter(assigned_to_id=work.target_id)
    elif work.kind == "region":
        customers = customers.filter(all_regions=True)
    if work.customer_after:
        customers = customers.filter(pk__gt=work.customer_after)
    candidates = list(customers.order_by("pk").values_list("pk", "assigned_to_id")[:BATCH_SIZE + 1])
    identities = []
    for customer_id, owner_id in candidates[:BATCH_SIZE]:
        viewers = {work.target_id} if work.kind == "profile" else {file.assigned_to_id, owner_id}
        identities.extend((viewer_id, file.pk, customer_id) for viewer_id in viewers)
    if len(candidates) > BATCH_SIZE:
        work.file_id = file.pk
        work.customer_after = candidates[BATCH_SIZE - 1][0]
    else:
        work.after_id = file.pk
        work.file_id = work.customer_after = None
    return identities


def _refresh_batch(work, identities):
    if not identities:
        return
    identities = set(identities)
    viewers = {u.pk: u for u in User.objects.select_for_update().select_related("workspace").filter(pk__in={v for v, f, c in identities})}
    files = {f.pk: f for f in PropertyFile.objects.select_for_update().select_related("region").filter(pk__in={f for v, f, c in identities}, workspace_id=work.workspace_id)}
    customers = {c.pk: c for c in Customer.objects.select_for_update().prefetch_related("preferred_regions").filter(pk__in={c for v, f, c in identities}, workspace_id=work.workspace_id)}
    query = Q(pk__in=[])
    for viewer_id, file_id, customer_id in identities:
        query |= Q(viewer_id=viewer_id, property_file_id=file_id, customer_id=customer_id)
    stored = {(r.viewer_id, r.property_file_id, r.customer_id): r for r in MatchRecommendation.objects.select_for_update().filter(query)}
    profiles = {p.user_id: p for p in MatchingProfile.objects.filter(user_id__in=viewers)}
    # A newer ordinary recovery must not erase an earlier, still-unconsumed material
    # event. Aggregate a bounded set of target keys rather than loading event history
    # or doing per-pair event queries.
    relevant = (Q(kind="file", target_id__in=files) | Q(kind="customer", target_id__in=customers)
                | Q(kind="profile", target_id__in=viewers)
                | Q(kind="region", target_id__in={f.region_id for f in files.values()})
                | Q(kind="workspace"))
    material_events = {(e["kind"], e["target_id"]): e["latest"] for e in
        RecommendationWork.objects.filter(relevant, workspace_id=work.workspace_id, material=True,
                                          pk__lte=work.pk).values("kind", "target_id").annotate(latest=Max("pk"))}
    inserts, updates = [], []
    for identity in sorted(identities):
        viewer_id, file_id, customer_id = identity
        row = stored.get(identity)
        if row is not None and row.last_generation_event >= work.pk:
            continue  # Late older event/duplicate phase must never overwrite a newer projection.
        viewer, file, customer = viewers.get(viewer_id), files.get(file_id), customers.get(customer_id)
        if file is None or customer is None or viewer is None:
            if row is not None:
                row.is_viewer_valid = row.is_currently_recommended = row.is_source_valid = False
                row.last_generation_event = work.pk
                row.updated_at = timezone.now()
                updates.append(row)
            continue
        source_valid = file.status == "active" and customer.status == "active"
        if not _association_valid(viewer, file, customer):
            if row is None:
                continue
            row.is_viewer_valid = row.is_currently_recommended = False
            row.is_source_valid = source_valid
        else:
            if viewer_id not in profiles:
                with suppress_events():
                    profiles[viewer_id] = own_profile(actor=viewer)
            result = evaluate_match(file, customer, profiles[viewer_id])
            if row is None:
                if not result.recommended:
                    continue
                row = MatchRecommendation(viewer=viewer, property_file=file, customer=customer)
            keys = [("file", file.pk), ("customer", customer.pk), ("profile", viewer.pk),
                    ("workspace", None), ("workspace", work.workspace_id)]
            if customer.all_regions:
                keys.append(("region", file.region_id))
            material = max((material_events.get(key, 0) for key in keys), default=0) > row.last_generation_event
            _apply_evaluation(row, result, source_valid=source_valid,
                              change=ChangeContext(material_inputs_changed=material), persist=False)
        row.last_generation_event = work.pk
        row.updated_at = timezone.now()
        (updates if identity in stored else inserts).append(row)
    # Private validated batch sink: same lifecycle and current locked ownership checks,
    # immutable loaded references, DB constraints; never exposed to API payloads.
    raw = models.QuerySet(model=MatchRecommendation)
    if inserts:
        raw.bulk_create(inserts, batch_size=BATCH_SIZE * 2)
    if updates:
        raw.bulk_update(updates, WRITE_FIELDS, batch_size=BATCH_SIZE * 2)
    # Same transaction as projection/cursor writes; retries cannot leave orphan alerts.
    from apps.notifications.producers import recommendation_notification
    for row in inserts + updates:
        recommendation_notification(row, getattr(row, '_notification_event', None), watermark=work.pk)


@transaction.atomic
def process_work(work_id, step):
    workspace_id = RecommendationWork.objects.filter(pk=work_id).values_list("workspace_id", flat=True).first()
    if workspace_id is None:
        return False
    workspace = Workspace.objects.select_for_update().get(pk=workspace_id)
    work = RecommendationWork.objects.select_for_update().get(pk=work_id)
    work.workspace = workspace
    if work.completed or work.step != step:
        return False
    if work.phase == "existing":
        rows = list(_existing(work)[:BATCH_SIZE])
        identities = [(r.viewer_id, r.property_file_id, r.customer_id) for r in rows]
        if rows:
            work.after_id = rows[-1].pk
        else:
            work.phase, work.after_id = "discover", None
    else:
        identities = _discover(work)
    _refresh_batch(work, identities)
    work.step += 1
    work.save()
    if not work.completed:
        transaction.on_commit(lambda: publish_work(work.pk, work.step))
    return True


@transaction.atomic
def reconcile_workspace(workspace_id, *, viewer_id=None):
    """Internal recovery after unsupported bulk maintenance; ordinary refresh, not material."""
    Workspace.objects.select_for_update().get(pk=workspace_id)
    if viewer_id is not None and not User.objects.filter(pk=viewer_id, workspace_id=workspace_id).exists():
        raise ValueError("Viewer is not in the recovery workspace")
    return record_work(workspace_id, "profile" if viewer_id else "workspace", viewer_id, material=False)


def recover_pending(*, after_id=0, limit=100):
    """Republish a bounded page of durable pending cursors, safe with active workers."""
    if not 1 <= limit <= 100:
        raise ValueError("Recovery limit must be 1..100")
    rows = list(RecommendationWork.objects.filter(completed=False, pk__gt=after_id).order_by("pk").values_list("pk", "step")[:limit])
    for pk, step in rows:
        publish_work(pk, step)
    return rows[-1][0] if rows else None
