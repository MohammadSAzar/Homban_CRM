"""Closed delegation vocabulary and UI metadata; never stored as product state."""
from django.db import models
from django.utils.translation import gettext_lazy as _


class AdminPermission(models.TextChoices):
    MANAGE_USERS = "manage_users", _("مدیریت کاربران")
    MANAGE_RANGES = "manage_ranges", _("مدیریت رنج‌ها")
    MANAGE_WORKSPACE_SETTINGS = "manage_workspace_settings", _("مدیریت تنظیمات مجموعه")


class PermissionStatus(models.TextChoices):
    RESERVED = "reserved", _("رزرو شده")
    ACTIVE = "active", _("فعال")


def permission_metadata(permission):
    """Unknown identifiers have no metadata or operational eligibility."""
    if permission not in AdminPermission.values:
        return None
    entry = AdminPermission(permission)
    operational = entry in (AdminPermission.MANAGE_RANGES, AdminPermission.MANAGE_WORKSPACE_SETTINGS)
    return {
        "id": entry.value,
        "label": str(entry.label),
        "status": PermissionStatus.ACTIVE.value if operational else PermissionStatus.RESERVED.value,
        "operational": operational,
    }
