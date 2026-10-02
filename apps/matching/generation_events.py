"""Matching-only change capture. No scoring or broker work inside business transactions."""
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from django.db import transaction

FILE_FIELDS = ("transaction_type", "status", "city_id", "region_id", "area", "bedrooms", "building_age", "parking", "elevator", "storage", "balcony", "total_price", "deposit_amount", "monthly_rent", "assigned_to_id")
CUSTOMER_FIELDS = ("customer_type", "status", "min_area", "max_area", "bedrooms", "min_building_age", "max_building_age", "budget", "deposit_budget", "monthly_rent_budget", "all_regions", "assigned_to_id")
_tracking = ContextVar("matching_tracking", default=frozenset())
_suppressed = ContextVar("matching_suppressed", default=False)
logger = logging.getLogger(__name__)


@contextmanager
def suppress_events():
    token = _suppressed.set(True)
    try:
        yield
    finally:
        _suppressed.reset(token)


def publish_work(work_id, step=0):
    from .tasks import generate_recommendations
    try:
        generate_recommendations.delay(work_id, step)
    except Exception:
        logger.exception("Matching broker publish failed for work %s", work_id)


def record_work(workspace_id, kind, target_id=None, *, material=True, using="default"):
    from .models import RecommendationWork
    if _suppressed.get() or workspace_id is None:
        return None
    work = RecommendationWork.objects.using(using).create(workspace_id=workspace_id, kind=kind, target_id=target_id, material=material)
    transaction.on_commit(lambda: publish_work(work.pk), using=using)
    return work


def _snapshot(model, pk, fields, kind, using):
    values = model.objects.using(using).filter(pk=pk).values(*fields).first()
    if values is not None and kind == "customer":
        from apps.customers.models import CustomerRegionPreference
        values["preferred_regions"] = frozenset(CustomerRegionPreference.objects.using(using).filter(customer_id=pk).values_list("region_id", flat=True))
    return values


def track_save(kind, fields, *, creation=True, material=True):
    """Compare persisted effective inputs, including partial saves, in the write transaction."""
    def decorate(save):
        @wraps(save)
        def tracked(instance, *args, **kwargs):
            if _suppressed.get():
                return save(instance, *args, **kwargs)
            from apps.organizations.models import Workspace
            from apps.accounts.models import User
            using = kwargs.get("using") or instance._state.db or "default"
            model = type(instance)
            target_id = instance.user_id if kind == "profile" and hasattr(instance, "user_id") else instance.pk
            key = (kind, target_id)
            if key in _tracking.get():
                return save(instance, *args, **kwargs)
            if isinstance(instance, Workspace):
                workspace_id = instance.pk
            elif kind == "profile" and hasattr(instance, "user_id"):
                workspace_id = User.objects.using(using).filter(pk=instance.user_id).values_list("workspace_id", flat=True).first()
            else:
                workspace_id = model.objects.using(using).filter(pk=instance.pk).values_list("workspace_id", flat=True).first() or instance.workspace_id
            with transaction.atomic(using=using):
                list(Workspace.objects.using(using).select_for_update().filter(pk=workspace_id))
                before = _snapshot(model, instance.pk, fields, kind, using)
                token = _tracking.set(_tracking.get() | {key})
                try:
                    result = save(instance, *args, **kwargs)
                finally:
                    _tracking.reset(token)
                after = _snapshot(model, instance.pk, fields, kind, using)
                if before != after and (before is not None or creation):
                    record_work(workspace_id, kind, target_id, using=using, material=material)
                return result
        return tracked
    return decorate


@contextmanager
def track_preferences(customer_ids, using="default"):
    from apps.customers.models import Customer
    ids = set(customer_ids) - {pk for kind, pk in _tracking.get() if kind == "customer"}
    before = {pk: _snapshot(Customer, pk, CUSTOMER_FIELDS, "customer", using) for pk in ids}
    token = _tracking.set(_tracking.get() | {("customer", pk) for pk in ids})
    try:
        yield
    finally:
        _tracking.reset(token)
    for pk in ids:
        after = _snapshot(Customer, pk, CUSTOMER_FIELDS, "customer", using)
        if before[pk] != after and after is not None:
            workspace_id = Customer.objects.using(using).values_list("workspace_id", flat=True).get(pk=pk)
            record_work(workspace_id, "customer", pk, using=using)


def preference_added(sender, instance, action, reverse, pk_set, using, **kwargs):
    if action != "post_add" or not pk_set:
        return
    from apps.customers.models import Customer
    ids = pk_set if reverse else {instance.pk}
    ids = set(ids) - {pk for kind, pk in _tracking.get() if kind == "customer"}
    for pk, workspace_id in Customer.objects.using(using).filter(pk__in=ids).values_list("pk", "workspace_id"):
        record_work(workspace_id, "customer", pk, using=using)
