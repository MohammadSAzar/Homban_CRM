"""Deterministic single-pair evaluation. No authorization, candidate search or writes."""
from dataclasses import dataclass
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext

from .explanations import explain

VERSION = "matching-v1"
ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")
DEPOSIT_BASE = Decimal("100000000")
# Fixed precision/rounding, independent of the caller's Decimal context.
MATH_CONTEXT = Context(prec=50, rounding=ROUND_HALF_EVEN)
Scalar = Decimal | int | bool | str | None


@dataclass(frozen=True)
class CriterionResult:
    code: str
    applicable: bool
    weight: Decimal
    earned: Decimal
    reason_code: str
    explanation: str
    references: tuple[tuple[str, Scalar], ...]


@dataclass(frozen=True)
class BudgetGate:
    transaction_type: str
    passed: bool
    customer_amount: Decimal
    file_amount: Decimal
    lower: Decimal
    upper: Decimal
    conversion_rate: Decimal | None
    reason_code: str
    explanation: str


@dataclass(frozen=True)
class RegionPenalty:
    applied: bool
    multiplier: Decimal
    reason_code: str
    explanation: str


@dataclass(frozen=True)
class MatchingResult:
    eligible: bool
    recommended: bool
    final_score: Decimal | None
    normalized_score: Decimal | None
    minimum_score: Decimal
    rejection_code: str | None
    reason_code: str
    explanations: tuple[str, ...]
    criteria: tuple[CriterionResult, ...]
    budget_gate: BudgetGate | None
    region_penalty: RegionPenalty | None
    earned_points: Decimal = ZERO
    available_points: Decimal = ZERO
    formula_version: str = VERSION


def _criterion(code, weight, earned, reason, *, applicable=True, **references):
    return CriterionResult(code, applicable, Decimal(weight), earned, reason,
                           explain(reason), tuple(references.items()))


def _budget(file, customer, profile):
    lower_ratio = Decimal(profile.sale_budget_lower_ratio)
    upper_ratio = Decimal(profile.sale_budget_upper_ratio)
    rate = None
    if file.transaction_type == "sale":
        file_scaled = Decimal(file.total_price)
        customer_scaled = Decimal(customer.budget)
        divisor = ONE
    else:
        rate = Decimal(profile.rent_per_100m_deposit)
        # Compare scaled amounts before division: repeating equivalents cannot
        # incorrectly reject a pair exactly on an inclusive gate boundary.
        file_scaled = Decimal(file.deposit_amount) * rate + Decimal(file.monthly_rent) * DEPOSIT_BASE
        customer_scaled = Decimal(customer.deposit_budget) * rate + Decimal(customer.monthly_rent_budget) * DEPOSIT_BASE
        divisor = rate
    lower_scaled, upper_scaled = customer_scaled * lower_ratio, customer_scaled * upper_ratio
    passed = lower_scaled <= file_scaled <= upper_scaled
    reason = "budget_pass" if passed else "budget_outside_range"
    return BudgetGate(file.transaction_type, passed, customer_scaled / divisor,
                      file_scaled / divisor, lower_scaled / divisor, upper_scaled / divisor,
                      rate, reason, explain(reason))


def _area(file, customer, profile):
    value, lower, upper = map(Decimal, (file.area, customer.min_area, customer.max_area))
    weight = Decimal(profile.area)
    if lower <= value <= upper:
        return _criterion("area", weight, weight, "area_inside", value=value, minimum=lower, maximum=upper)
    below = value < lower
    boundary = lower if below else upper
    distance = abs(value - boundary)
    if boundary == ZERO:
        return _criterion("area", weight, ZERO, "area_zero_boundary", value=value, minimum=lower, maximum=upper,
                          distance_percent=None, factor=ZERO)
    percent = distance / boundary * HUNDRED
    factor = ZERO
    for limit, multiplier in ((5, "0.88"), (10, "0.76"), (15, "0.56"), (20, "0.32"), (25, "0.16")):
        if distance * HUNDRED <= boundary * limit:
            factor = Decimal(multiplier)
            break
    return _criterion("area", weight, weight * factor, "area_below" if below else "area_above",
                      value=value, minimum=lower, maximum=upper, distance_percent=percent, factor=factor)


def _bedrooms(file, customer, profile):
    difference = abs(file.bedrooms - customer.bedrooms)
    weight = Decimal(profile.bedrooms)
    earned = weight if difference == 0 else weight * 8 / 15 if difference == 1 else ZERO
    reason = "bedrooms_exact" if difference == 0 else "bedrooms_one" if difference == 1 else "bedrooms_outside"
    return _criterion("bedrooms", weight, earned, reason, value=file.bedrooms,
                      requested=customer.bedrooms, difference=difference)


def _age(file, customer, profile):
    lower, upper, value = customer.min_building_age, customer.max_building_age, file.building_age
    weight = Decimal(profile.building_age)
    refs = dict(value=value, minimum=lower, maximum=upper)
    if lower is None and upper is None:
        return _criterion("building_age", weight, ZERO, "age_not_requested", applicable=False, **refs)
    if value is None:
        return _criterion("building_age", weight, ZERO, "age_unknown", **refs)
    distance = lower - value if lower is not None and value < lower else value - upper if upper is not None and value > upper else 0
    factor = ONE if distance == 0 else Decimal("0.80") if distance <= 2 else Decimal("0.50") if distance <= 5 else Decimal("0.20") if distance <= 10 else ZERO
    return _criterion("building_age", weight, weight * factor, "age_inside" if distance == 0 else "age_outside",
                      **refs, distance_years=distance, factor=factor)


def _preferred(file, customer):
    """Consume loaded relationships without populating/mutating input caches.

    No queries with select_related('region') + prefetch_related('preferred_regions').
    Otherwise at most one Region read and one explicit preference existence query.
    """
    region = file._state.fields_cache.get("region")
    if region is None:
        region_model = file._meta.get_field("region").remote_field.model
        region = region_model.objects.using(file._state.db or "default").only("id", "workspace_id", "is_active").get(pk=file.region_id)
    if region.workspace_id != customer.workspace_id:
        return False, "region_workspace_mismatch"
    if customer.all_regions:
        return region.is_active, "region_all_active" if region.is_active else "region_inactive"
    prefetched = getattr(customer, "_prefetched_objects_cache", {}).get("preferred_regions")
    if prefetched is None:
        preferred = customer.preferred_regions.filter(pk=file.region_id, workspace_id=customer.workspace_id).exists()
    else:
        preferred = any(region.pk == file.region_id and region.workspace_id == customer.workspace_id for region in prefetched)
    return preferred, "region_preferred" if preferred else "region_nonpreferred"


def evaluate_match(property_file, customer, profile):
    """Evaluate canonical records only; caller supplies/authorizes the profile.

    Decimal results retain 50 significant digits, with no display quantization.
    """
    with localcontext(MATH_CONTEXT):
        return _evaluate(property_file, customer, profile)


def _evaluate(file, customer, profile):
    minimum = Decimal(profile.minimum_score)
    def reject(code, budget=None):
        return MatchingResult(False, False, None, None, minimum, code, code,
                              (explain(code),), (), budget, None)

    if file.workspace_id != customer.workspace_id:
        return reject("workspace_mismatch")
    if (customer.customer_type, file.transaction_type) not in (("buyer", "sale"), ("tenant", "rent")):
        return reject("type_mismatch")
    if file.status != "active":
        return reject("file_not_active")
    if customer.status != "active":
        return reject("customer_not_active")
    budget = _budget(file, customer, profile)
    if not budget.passed:
        return reject("budget_outside_range", budget)
    preferred, region_reason = _preferred(file, customer)
    if region_reason == "region_workspace_mismatch":
        return reject(region_reason)
    region_weight = Decimal(profile.region)
    criteria = [_area(file, customer, profile), _bedrooms(file, customer, profile), _age(file, customer, profile),
                _criterion("region", region_weight, region_weight if preferred else ZERO, region_reason,
                           all_regions=customer.all_regions, preferred=preferred)]
    for name in ("parking", "elevator", "storage", "balcony"):
        value, weight = getattr(file, name), Decimal(getattr(profile, name))
        reason = "facility_available" if value is True else "facility_unknown" if value is None else "facility_unavailable"
        criteria.append(_criterion(name, weight, weight if value is True else ZERO, reason, value=value))
    earned = sum((item.earned for item in criteria if item.applicable), ZERO)
    available = sum((item.weight for item in criteria if item.applicable), ZERO)
    penalized = not customer.all_regions and not preferred
    penalty_code = "region_penalty" if penalized else "no_region_penalty"
    penalty = RegionPenalty(penalized, Decimal("0.90") if penalized else ONE, penalty_code, explain(penalty_code))
    normalized = earned / available * HUNDRED if available else None
    final = min(HUNDRED, max(ZERO, normalized * penalty.multiplier)) if normalized is not None else None
    recommended = final is not None and final >= minimum
    code = "no_applicable_weight" if final is None else "recommended" if recommended else "below_threshold"
    return MatchingResult(True, recommended, final, normalized, minimum, None, code,
                          (explain(code),), tuple(criteria), budget, penalty, earned, available)
