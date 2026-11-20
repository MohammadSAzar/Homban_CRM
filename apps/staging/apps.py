from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class StagingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.staging"
    verbose_name = _("پیش‌نویس‌ها")
