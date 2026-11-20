"""Ephemeral entity snapshots; never save comparison forms back to source rows."""
from uuid import UUID
from ..normalization.text import normalize_text
from ..normalization.phone import normalize_phone
from ..normalization.numeric import normalize_decimal, normalize_boolean


def identifier(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def property_snapshot(data):
    result = {key: normalize_decimal(data.get(key)) for key in (
        "area", "bedrooms", "total_price", "deposit_amount", "monthly_rent")}
    result.update(region_id=identifier(data.get("region")), address=normalize_text(data.get("address")),
                  owner_phone=normalize_phone(data.get("owner_phone")),
                  visit_contact_phone=normalize_phone(data.get("visit_contact_phone")),
                  transaction_type=data.get("transaction_type") if data.get("transaction_type") in ("sale", "rent") else None)
    for key in ("parking", "storage", "elevator", "balcony"):
        result[key] = normalize_boolean(data.get(key))
    return result


def customer_snapshot(data):
    result = {key: normalize_decimal(data.get(key)) for key in (
        "min_area", "max_area", "budget", "deposit_budget", "monthly_rent_budget")}
    regions = data.get("preferred_regions", [])
    result.update(mobile=normalize_phone(data.get("mobile")), name=normalize_text(data.get("name")),
                  customer_type=data.get("customer_type") if data.get("customer_type") in ("buyer", "tenant") else None,
                  all_regions=normalize_boolean(data.get("all_regions")),
                  region_ids=tuple(sorted({identifier(value) for value in regions if identifier(value)})) if isinstance(regions, (list, tuple)) else ())
    return result


def build_comparison_snapshot(record):
    data = record.normalized_data if isinstance(record.normalized_data, dict) else {}
    builders = {"property_file": property_snapshot, "customer": customer_snapshot}
    return builders[record.entity_type](data)
