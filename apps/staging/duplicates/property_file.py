from django.db.models import Q
from apps.properties.policies import visible_files
from ..normalization.phone import phone_query_forms
from ..normalization.numeric import bounds, near
from .snapshots import property_snapshot
from .common import CANDIDATE_LIMIT, evidence, result

FIELDS = ("id", "code", "region", "transaction_type", "address", "owner_phone", "visit_contact_phone",
          "area", "bedrooms", "total_price", "deposit_amount", "monthly_rent")


def candidates(actor, snapshot, raw):
    filters = Q(pk__in=[])
    forms = set(phone_query_forms(raw.get("owner_phone"))) | set(phone_query_forms(raw.get("visit_contact_phone")))
    if forms:
        filters |= Q(owner_phone__in=forms) | Q(visit_contact_phone__in=forms)
    if (snapshot["region_id"] and snapshot["address"] and len(snapshot["address"]) >= 8
            and snapshot["area"] is not None and snapshot["area"] >= 0 and snapshot["bedrooms"] is not None):
        filters |= Q(region_id=snapshot["region_id"], area__range=bounds(snapshot["area"]), bedrooms=snapshot["bedrooms"])
    rows = visible_files(actor).filter(workspace_id=actor.workspace_id, region__workspace_id=actor.workspace_id,
        transaction_type__in=["sale", "rent"]).filter(filters).order_by("pk").values(*FIELDS)
    return list(rows[:CANDIDATE_LIMIT + 1])


def compare(snapshot, row):
    other = property_snapshot(row)
    phones = {snapshot[key] for key in ("owner_phone", "visit_contact_phone") if snapshot[key]}
    same_phone = bool(phones & {other[key] for key in ("owner_phone", "visit_contact_phone") if other[key]})
    checks = {
        "phone": same_phone,
        "region": bool(snapshot["region_id"] and snapshot["region_id"] == other["region_id"]),
        "address": bool(snapshot["address"] and len(snapshot["address"]) >= 8 and snapshot["address"] == other["address"]),
        "area": near(snapshot["area"], other["area"]),
        "bedrooms": snapshot["bedrooms"] is not None and snapshot["bedrooms"] == other["bedrooms"],
        "transaction_type": snapshot["transaction_type"] is not None and snapshot["transaction_type"] == other["transaction_type"],
    }
    financials = ("total_price",) if snapshot["transaction_type"] == "sale" else ("deposit_amount", "monthly_rent")
    checks["financials"] = all(near(snapshot[key], other[key], financial=True) for key in financials)
    identity = checks["area"] and checks["bedrooms"]
    if not checks["transaction_type"]:
        return None
    if same_phone and checks["region"] and (identity or checks["address"]):
        confidence = "high"
    elif (same_phone and (checks["region"] or checks["address"] or identity)) or (checks["region"] and checks["address"] and identity):
        confidence = "medium"
    else:
        return None
    reasons = evidence(checks, [
        ("phone", "contact_phone", "normalized_exact_match", "strong"),
        ("region", "region", "exact_match", "supporting"),
        ("address", "address", "normalized_exact_match", "strong"),
        ("area", "area", "within_tolerance", "supporting"),
        ("bedrooms", "bedrooms", "exact_match", "supporting"),
        ("transaction_type", "transaction_type", "exact_match", "supporting"),
        ("financials", "financials", "within_tolerance", "supporting"),
    ])
    return result(row, confidence, checks, reasons)
