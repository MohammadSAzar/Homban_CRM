"""Short-lived proof of an authorized cross-consultant live result, never scores."""
import uuid

from django.core import signing
from django.utils.crypto import salted_hmac
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError

SALT = "homban.collaboration.live.v1"
MAX_AGE = 300


def ownership_fingerprint(file, customer):
    # A signed payload is readable. Bind assignments without disclosing the other owner.
    return salted_hmac(SALT + ".ownership", f"{file.assigned_to_id}:{customer.assigned_to_id}", algorithm="sha256").hexdigest()


def issue_reference(actor, file, customer):
    if (actor.role != "consultant" or actor.pk not in (file.assigned_to_id, customer.assigned_to_id)
            or file.assigned_to_id == customer.assigned_to_id):
        return None
    return signing.dumps({
        "actor": str(actor.pk), "workspace": str(actor.workspace_id),
        "file": str(file.pk), "customer": str(customer.pk),
        "ownership": ownership_fingerprint(file, customer),
    }, salt=SALT)


def verify_reference(reference, actor):
    try:
        data = signing.loads(reference, salt=SALT, max_age=MAX_AGE)
        if (not isinstance(data, dict) or set(data) != {"actor", "workspace", "file", "customer", "ownership"}
                or data["actor"] != str(actor.pk) or data["workspace"] != str(actor.workspace_id)):
            raise signing.BadSignature()
        for key in ("actor", "workspace", "file", "customer"):
            uuid.UUID(data[key])
        if not isinstance(data["ownership"], str) or len(data["ownership"]) != 64:
            raise signing.BadSignature()
        return data
    except (signing.BadSignature, ValueError, TypeError, AttributeError):
        raise ValidationError(_("مرجع تطبیق معتبر نیست یا منقضی شده است.")) from None
