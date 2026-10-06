from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class TasksConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.tasks'
    verbose_name = _('کارهای شخصی')
