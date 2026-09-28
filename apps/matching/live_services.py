from types import SimpleNamespace

from django.db import transaction
from django.db.models import F, Prefetch
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.accounts.authentication import customer_can_authenticate
from apps.accounts.models import User
from apps.customers.models import Customer
from apps.customers.policies import visible_customers
from apps.locations.models import Region
from apps.organizations.models import Workspace
from apps.properties.models import PropertyFile
from apps.properties.policies import visible_files
from .engine import evaluate_eligibility, evaluate_match
from .hard_constraints import has_age_requirement, hard_constraint_failure
from .live_serializers import (
    LiveMatchingRequestSerializer, MatchingProfileSerializer,
    FileCandidateSerializer, CustomerCandidateSerializer, matching_result_data,
)
from .models import SETTING_FIELDS, WEIGHT_FIELDS
from .services import own_profile

MAX_CANDIDATES = 1000


def effective_profile(base, overrides):
    values = {name: getattr(base, name) for name in SETTING_FIELDS}
    serializer = MatchingProfileSerializer(data={**values, **overrides})
    serializer.is_valid(raise_exception=True)
    values = serializer.validated_data
    # Field validators already enforce score/rate ranges and lower <= 1 <= upper.
    if not any(values[name] > 0 for name in WEIGHT_FIELDS):
        raise ValidationError({"overrides": _("حداقل یک وزن باید بیشتر از صفر باشد.")})
    return SimpleNamespace(**values)


def scoped_preferences(workspace_id):
    return Prefetch("preferred_regions", queryset=Region.objects.filter(
        workspace_id=workspace_id, city__workspace_id=workspace_id,
    ).order_by("pk"))


@transaction.atomic
def live_matches(*, actor, source_id, direction, data):
    # Same lock order as operational writes; re-authorize current database state.
    try:
        workspace = Workspace.objects.select_for_update().get(pk=actor.workspace_id, is_active=True)
        actor = User.objects.select_for_update().get(pk=actor.pk, workspace=workspace)
    except (Workspace.DoesNotExist, User.DoesNotExist):
        raise PermissionDenied(_("دسترسی به حساب کاربری یا مجموعه فعال نیست.")) from None
    actor.workspace = workspace
    if not customer_can_authenticate(actor):
        raise PermissionDenied(_("دسترسی به حساب کاربری یا مجموعه فعال نیست."))
    from_customer = direction == "customer"
    sources = visible_customers(actor).prefetch_related(scoped_preferences(workspace.pk)) if from_customer else visible_files(actor).select_related("region")
    source = sources.filter(pk=source_id).first()
    if source is None:
        raise NotFound(_("رکورد یافت نشد."))
    request = LiveMatchingRequestSerializer(data=data)
    request.is_valid(raise_exception=True)
    constraints = request.validated_data.get("hard_constraints", {})
    if from_customer and constraints.get("building_age") and not has_age_requirement(source):
        raise ValidationError({"hard_constraints": {"building_age": _("مشتری محدودیت سن بنا ندارد.")}})
    base = own_profile(actor=actor)
    profile = effective_profile(base, request.validated_data.get("overrides", {}))
    if source.status != "active":
        return []
    common = dict(workspace_id=workspace.pk, status="active", assigned_to__workspace_id=workspace.pk,
                  assigned_to__role=User.Role.CONSULTANT)
    if from_customer:
        candidates = PropertyFile.objects.filter(**common,
            transaction_type="sale" if source.customer_type == "buyer" else "rent",
            city__workspace_id=workspace.pk, region__workspace_id=workspace.pk,
            region__city__workspace_id=workspace.pk, region__city_id=F("city_id"),
        ).select_related("region").defer("owner_name", "owner_phone", "visit_contact_phone", "address", "description")
        serializer = FileCandidateSerializer
    else:
        candidates = Customer.objects.filter(**common,
            customer_type="buyer" if source.transaction_type == "sale" else "tenant",
        ).prefetch_related(scoped_preferences(workspace.pk)).defer("name", "mobile", "description")
        serializer = CustomerCandidateSerializer
    candidates = list(candidates.order_by("pk")[:MAX_CANDIDATES + 1])
    if len(candidates) > MAX_CANDIDATES:
        raise ValidationError({"candidates": _("تعداد نامزدها از سقف ۱۰۰۰ رکورد این نسخه بیشتر است؛ محاسبه انجام نشد.")}, code="candidate_limit_exceeded")
    matches = []
    for candidate in candidates:
        file, customer = (candidate, source) if from_customer else (source, candidate)
        code, _budget = evaluate_eligibility(file, customer, profile)
        if code:
            continue
        if hard_constraint_failure(file, customer, constraints):
            continue
        result = evaluate_match(file, customer, profile)
        if result.recommended:
            matches.append((candidate, result))
    matches.sort(key=lambda pair: pair[0].pk)
    matches.sort(key=lambda pair: pair[1].final_score, reverse=True)
    return [{"candidate": serializer(candidate).data, **matching_result_data(result)} for candidate, result in matches]
