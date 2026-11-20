"""Advisory, ephemeral duplicate analysis; no conversion hooks or mutation."""
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError
from ..policies import review_actor, review_record
from ..normalization.text import NORMALIZATION_VERSION
from .snapshots import build_comparison_snapshot
from . import property_file, customer
from .common import CANDIDATE_LIMIT, RESULT_LIMIT

ADAPTERS = {"property_file": property_file, "customer": customer}


def find_duplicate_candidates(*, record_id, actor):
    current = review_actor(actor)
    record = review_record(current, record_id)
    if record.status not in ("draft", "ready"):
        raise ValidationError({"status": _("بررسی تکرار فقط برای پیش‌نویس یا رکورد آماده مجاز است.")})
    adapter = ADAPTERS.get(record.entity_type)
    if adapter is None:
        raise ValidationError({"entity_type": _("نوع رکورد معتبر نیست.")})
    snapshot = build_comparison_snapshot(record)
    raw = record.normalized_data if isinstance(record.normalized_data, dict) else {}
    rows = adapter.candidates(current, snapshot, raw)
    matches = [match for row in rows[:CANDIDATE_LIMIT] if (match := adapter.compare(snapshot, row)) is not None]
    matches.sort(key=lambda item: (
        {"high": 0, "medium": 1}[item["confidence"]],
        -sum(reason["weight"] == "strong" for reason in item["evidence"]),
        -len(item["evidence"]), item["id"],
    ))
    return {"entity_type": record.entity_type, "normalization_version": NORMALIZATION_VERSION,
            "candidate_count": min(len(matches), RESULT_LIMIT), "candidates": matches[:RESULT_LIMIT],
            "truncated": len(rows) > CANDIDATE_LIMIT or len(matches) > RESULT_LIMIT,
            "candidate_limit": CANDIDATE_LIMIT, "result_limit": RESULT_LIMIT}
