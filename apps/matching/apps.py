from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class MatchingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.matching"
    verbose_name = _("تنظیمات تطبیق")

    def ready(self):
        from django.db.models.signals import m2m_changed
        from apps.customers.models import Customer
        from .generation_events import preference_added
        m2m_changed.connect(preference_added, sender=Customer.preferred_regions.through,
                            dispatch_uid="matching.preference_added")
