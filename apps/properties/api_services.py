from django.core.exceptions import ValidationError as ModelValidationError
from django.db import transaction
from django.db.models import Max
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.accounts.models import User
from apps.accounts.services import reject_extra_fields
from apps.locations.models import City, Region
from apps.organizations.models import Workspace
from .models import PropertyFile, PropertyFileImage, PropertyFileValuableReason
from .policies import require_property_actor, scoped_consultants, visible_files
from .services import create_property_file


WRITE_FIELDS = (
    "assigned_to", "transaction_type", "status", "city", "region", "address",
    "owner_name", "owner_phone", "visit_contact_phone", "area", "bedrooms",
    "total_floors", "units_per_floor", "unit_floor", "building_age", "parking",
    "storage", "elevator", "balcony", "price_per_square_meter", "total_price",
    "deposit_amount", "monthly_rent", "description", "is_valuable", "valuable_reasons",
)


def lock_actor(actor):
    require_property_actor(actor)
    try:
        workspace = Workspace.objects.select_for_update().get(pk=actor.workspace_id, is_active=True)
        current = User.objects.select_for_update().get(pk=actor.pk, workspace=workspace)
    except (Workspace.DoesNotExist, User.DoesNotExist):
        raise PermissionDenied(_("دسترسی به مجموعه فعال نیست.")) from None
    current.workspace = workspace
    require_property_actor(current)
    return current


def get_scoped_file(actor, file_id, *, lock=False):
    queryset = visible_files(actor)
    if lock:
        queryset = queryset.select_for_update()
    try:
        return queryset.get(pk=file_id)
    except PropertyFile.DoesNotExist:
        raise NotFound(_("فایل یافت نشد.")) from None


def replace_reasons(item, reasons):
    item.valuable_reasons.all().delete()
    for reason in reasons:
        PropertyFileValuableReason.objects.create(property_file=item, reason=reason)


@transaction.atomic
def save_file(*, actor, data, file_id=None):
    actor = lock_actor(actor)
    item = get_scoped_file(actor, file_id, lock=True) if file_id else None
    reject_extra_fields(data, set(WRITE_FIELDS))
    fields = dict(data)
    reasons = fields.pop("valuable_reasons", None)
    if item is None or "assigned_to" in fields:
        target_id = fields.get("assigned_to", actor.pk if actor.role == User.Role.CONSULTANT else None)
        assignee = scoped_consultants(actor).filter(pk=target_id, is_active=True).first()
        if assignee is None:
            raise ValidationError({"assigned_to": _("مشاور فعال و مجاز در محدوده خود انتخاب کنید.")})
        fields["assigned_to"] = assignee
    for key, model in (("city", City), ("region", Region)):
        if key in fields and fields[key] is not None:
            target = model.objects.filter(pk=fields[key], workspace_id=actor.workspace_id).first()
            if target is None:
                raise ValidationError({key: _("مکان انتخاب‌شده معتبر نیست.")})
            fields[key] = target
    try:
        if item is None:
            return create_property_file(workspace=actor.workspace, valuable_reasons=reasons or (), **fields)
        for key, value in fields.items():
            setattr(item, key, value)
        item.save()
        if reasons is not None:
            replace_reasons(item, reasons)
        return item
    except ModelValidationError as error:
        raise ValidationError(error.message_dict) from error


@transaction.atomic
def add_image(*, actor, file_id, data):
    actor = lock_actor(actor)
    item = get_scoped_file(actor, file_id, lock=True)
    reject_extra_fields(data, {"reference"})
    last = item.images.aggregate(last=Max("sort_order"))["last"]
    try:
        return PropertyFileImage.objects.create(property_file=item, reference=data.get("reference", ""), sort_order=0 if last is None else last + 1)
    except ModelValidationError as error:
        raise ValidationError(error.message_dict) from error


@transaction.atomic
def remove_image(*, actor, file_id, image_id):
    actor = lock_actor(actor)
    item = get_scoped_file(actor, file_id, lock=True)
    try:
        image = item.images.get(pk=image_id)
    except PropertyFileImage.DoesNotExist:
        raise NotFound(_("تصویر یافت نشد.")) from None
    image.delete()


@transaction.atomic
def reorder_images(*, actor, file_id, order):
    actor = lock_actor(actor)
    item = get_scoped_file(actor, file_id, lock=True)
    images = {image.pk: image for image in item.images.all()}
    if len(order) != len(set(order)) or set(order) != set(images):
        raise ValidationError({"order": _("فهرست باید شامل همه تصاویر فایل، هر کدام دقیقاً یک بار باشد.")})
    for position, image_id in enumerate(order):
        images[image_id].sort_order = position
    PropertyFileImage.objects.bulk_update(images.values(), ["sort_order"])
