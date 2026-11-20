from django.db.models import Q
from apps.customers.policies import visible_customers
from apps.customers.models import CustomerRegionPreference
from ..normalization.phone import phone_query_forms
from ..normalization.text import text_query_forms
from ..normalization.numeric import bounds, near
from .snapshots import customer_snapshot
from .common import CANDIDATE_LIMIT, evidence, result

FIELDS = ("id", "code", "mobile", "name", "customer_type", "all_regions", "min_area", "max_area",
          "budget", "deposit_budget", "monthly_rent_budget")


def candidates(actor, snapshot, raw):
    filters = Q(pk__in=[])
    forms = phone_query_forms(raw.get("mobile"))
    if forms:
        filters |= Q(mobile__in=forms)
    financials = ("budget",) if snapshot["customer_type"] == "buyer" else ("deposit_budget", "monthly_rent_budget")
    numeric = ("min_area", "max_area") + financials
    if (snapshot["name"] and len(snapshot["name"]) >= 3 and snapshot["customer_type"]
            and all(snapshot[key] is not None and snapshot[key] >= 0 for key in numeric)):
        preference = Q(name__in=text_query_forms(raw.get("name")), customer_type=snapshot["customer_type"])
        for key in numeric:
            preference &= Q(**{key + "__range": bounds(snapshot[key], financial=key in financials)})
        filters |= preference
    rows = list(visible_customers(actor).filter(workspace_id=actor.workspace_id,
        customer_type__in=["buyer", "tenant"]).filter(filters).order_by("pk").values(*FIELDS)[:CANDIDATE_LIMIT + 1])
    by_id = {row["id"]: row for row in rows}
    for row in rows:
        row["preferred_regions"] = []
    # One bounded relationship query; never per-candidate queries or foreign geography.
    for customer_id, region_id in CustomerRegionPreference.objects.filter(
        customer_id__in=by_id, customer__workspace_id=actor.workspace_id,
        region__workspace_id=actor.workspace_id, region__city__workspace_id=actor.workspace_id,
    ).values_list("customer_id", "region_id"):
        by_id[customer_id]["preferred_regions"].append(region_id)
    return rows


def compare(snapshot, row):
    other = customer_snapshot(row)
    same_mobile = bool(snapshot["mobile"] and snapshot["mobile"] == other["mobile"])
    financials = ("budget",) if snapshot["customer_type"] == "buyer" else ("deposit_budget", "monthly_rent_budget")
    checks = {
        "mobile": same_mobile,
        "name": bool(snapshot["name"] and len(snapshot["name"]) >= 3 and snapshot["name"] == other["name"]),
        "customer_type": bool(snapshot["customer_type"] and snapshot["customer_type"] == other["customer_type"]),
        "area_range": all(near(snapshot[key], other[key]) for key in ("min_area", "max_area")),
        "financials": all(near(snapshot[key], other[key], financial=True) for key in financials),
        "regions": bool(set(snapshot["region_ids"]) & set(other["region_ids"])) or (
            snapshot["all_regions"] is True and other["all_regions"] is True),
    }
    if same_mobile:
        confidence = "high"
    elif (not snapshot["mobile"] or not other["mobile"]) and all(checks[key] for key in ("name", "customer_type", "area_range", "financials", "regions")):
        confidence = "medium"
    else:
        return None
    reasons = evidence(checks, [
        ("mobile", "mobile", "normalized_exact_match", "strong"),
        ("name", "name", "normalized_exact_match", "supporting"),
        ("customer_type", "customer_type", "exact_match", "supporting"),
        ("area_range", "area_range", "within_tolerance", "supporting"),
        ("financials", "financials", "within_tolerance", "supporting"),
        ("regions", "regions", "overlap_or_both_all_regions", "supporting"),
    ])
    return result(row, confidence, checks, reasons)
