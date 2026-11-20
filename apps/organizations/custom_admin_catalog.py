"""Closed delegation vocabulary; no feature consumers are integrated in CA1."""
from django.db import models
from django.utils.translation import gettext_lazy as _


class AdminPermission(models.TextChoices):
    MANAGE_USERS = "manage_users", _("مدیریت کاربران")
    MANAGE_RANGES = "manage_ranges", _("مدیریت رنج‌ها")
    MANAGE_WORKSPACE_SETTINGS = "manage_workspace_settings", _("مدیریت تنظیمات مجموعه")
