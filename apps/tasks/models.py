import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, F
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from common.jalali import day_start, local_now

_TASK_WRITE = object()


def service_only(*args, **kwargs):
    raise ValidationError(_('تغییر کار فقط از طریق سرویس کارهای شخصی مجاز است.'))


class TaskQuerySet(models.QuerySet):
    update = bulk_update = bulk_create = delete = service_only

    def for_owner(self, actor):
        return self.filter(workspace_id=actor.workspace_id, owner_id=actor.pk,
                           owner__workspace_id=F('workspace_id'))


class ManualTask(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', _('در انتظار')
        DONE = 'done', _('انجام‌شده')
        CANCELLED = 'cancelled', _('لغوشده')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name=_('شناسه'))
    workspace = models.ForeignKey('organizations.Workspace', on_delete=models.PROTECT, verbose_name=_('مجموعه'))
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='manual_tasks', verbose_name=_('مالک'))
    title = models.CharField(max_length=200, verbose_name=_('عنوان'))
    scheduled_for = models.DateTimeField(verbose_name=_('زمان برنامه‌ریزی'))
    is_all_day = models.BooleanField(default=False, verbose_name=_('تمام‌روز'))
    status = models.CharField(max_length=9, choices=Status.choices, default=Status.PENDING, verbose_name=_('وضعیت'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('زمان ایجاد'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('زمان تغییر'))
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name=_('زمان انجام'))
    objects = TaskQuerySet.as_manager()

    class Meta:
        verbose_name = _('کار شخصی')
        verbose_name_plural = _('کارهای شخصی')
        indexes = [models.Index(fields=['workspace', 'owner', 'status', 'scheduled_for'], name='manual_owner_status_time'),
                   models.Index(fields=['workspace', 'owner', 'scheduled_for'], name='manual_owner_time')]
        constraints = [models.CheckConstraint(condition=Q(status__in=['pending','done','cancelled']), name='manual_status_valid'),
                       models.CheckConstraint(condition=(Q(status='done', completed_at__isnull=False) |
                            Q(status__in=['pending','cancelled'], completed_at__isnull=True)), name='manual_completion_valid')]

    def clean(self):
        super().clean()
        if not self.title or not self.title.strip():
            raise ValidationError({'title': _('عنوان الزامی است.')})
        if self.owner_id and self.workspace_id and self.owner.workspace_id != self.workspace_id:
            raise ValidationError(_('مالک باید متعلق به همین مجموعه باشد.'))
        if not self._state.adding:
            stored = type(self).objects.get(pk=self.pk)
            if (stored.workspace_id, stored.owner_id) != (self.workspace_id, self.owner_id):
                raise ValidationError(_('مالک و مجموعه قابل تغییر نیستند.'))
        for field in ('scheduled_for', 'completed_at'):
            value = getattr(self, field)
            if value is not None and timezone.is_naive(value):
                raise ValidationError({field: _('زمان باید دارای منطقه زمانی باشد.')})
        if self.scheduled_for and self.is_all_day and self.scheduled_for != day_start(local_now(self.scheduled_for).date()):
            raise ValidationError(_('کار تمام‌روز باید در آغاز روز محلی ذخیره شود.'))
        if (self.status == self.Status.DONE) != (self.completed_at is not None):
            raise ValidationError(_('زمان انجام با وضعیت کار سازگار نیست.'))

    def save(self, *args, _token=None, **kwargs):
        if _token is not _TASK_WRITE or kwargs.get('update_fields') is not None:
            service_only()
        self.full_clean()
        return super().save(*args, **kwargs)

    delete = service_only
