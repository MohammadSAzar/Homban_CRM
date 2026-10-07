"""Personal event summaries. Only notification services may write normal application data."""
import re
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

_NOTIFICATION_WRITE = object()


def service_only(*args, **kwargs):
    raise ValidationError(_('تغییر اعلان فقط از طریق سرویس اعلان‌ها مجاز است.'))


def validate_action_url(value):
    # Server-owned path only: no authority, query, fragment, encoding or browser-normalized slash.
    if not re.fullmatch(r'/[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*/?', value):
        raise ValidationError(_('پیوند اعلان باید یک مسیر داخلی معتبر باشد.'))


class NotificationQuerySet(models.QuerySet):
    update = bulk_update = bulk_create = delete = service_only

    def for_recipient(self, actor):
        return self.filter(recipient_id=actor.pk, workspace_id=actor.workspace_id,
                           recipient__workspace_id=F('workspace_id'))


class Notification(models.Model):
    class Kind(models.TextChoices):
        MATCH = 'match_recommendation', _('پیشنهاد تطبیق')
        COLLABORATION = 'collaboration_request', _('درخواست همکاری')
        CHAT = 'chat_message', _('پیام گفتگو')
        IMPORT = 'import_review', _('بررسی ورود اطلاعات')
        VOICE = 'voice_review', _('بررسی اطلاعات صوتی')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name=_('شناسه'))
    workspace = models.ForeignKey('organizations.Workspace', on_delete=models.PROTECT, verbose_name=_('مجموعه'))
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='notifications', verbose_name=_('گیرنده'))
    kind = models.CharField(max_length=24, choices=Kind.choices, verbose_name=_('نوع'))
    title = models.CharField(max_length=120, verbose_name=_('عنوان'))
    message = models.CharField(max_length=400, verbose_name=_('پیام'))
    action_url = models.CharField(max_length=240, validators=[validate_action_url], verbose_name=_('پیوند'))
    source_type = models.CharField(max_length=40, blank=True, default='', verbose_name=_('نوع منبع'))
    source_id = models.UUIDField(null=True, blank=True, verbose_name=_('شناسه منبع'))
    event_key = models.CharField(max_length=200, verbose_name=_('کلید رویداد'))
    read_at = models.DateTimeField(null=True, blank=True, verbose_name=_('زمان خواندن'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('زمان ایجاد'))
    objects = NotificationQuerySet.as_manager()

    class Meta:
        verbose_name = _('اعلان')
        verbose_name_plural = _('اعلان‌ها')
        constraints = [
            models.UniqueConstraint(fields=['recipient', 'event_key'], name='notification_recipient_event'),
            models.CheckConstraint(condition=Q(kind__in=['match_recommendation','collaboration_request','chat_message','import_review','voice_review']), name='notification_kind_valid'),
        ]
        indexes = [
            models.Index(fields=['recipient','read_at','created_at'], name='notification_read_time'),
            models.Index(fields=['recipient','kind','created_at'], name='notification_kind_time'),
        ]

    @property
    def is_read(self):
        return self.read_at is not None

    def clean(self):
        super().clean()
        if self.recipient_id and self.workspace_id and self.recipient.workspace_id != self.workspace_id:
            raise ValidationError(_('گیرنده باید متعلق به همین مجموعه باشد.'))
        if self.read_at is not None and timezone.is_naive(self.read_at):
            raise ValidationError(_('زمان خواندن باید دارای منطقه زمانی باشد.'))
        for name in ('title','message','event_key'):
            if not getattr(self, name).strip():
                raise ValidationError({name: _('این مقدار الزامی است.')})
        if not self._state.adding:
            stored = type(self).objects.get(pk=self.pk)
            if any(getattr(self, f.attname) != getattr(stored, f.attname)
                   for f in self._meta.concrete_fields if f.name != 'read_at'):
                raise ValidationError(_('اطلاعات رویداد اعلان قابل تغییر نیست.'))

    def save(self, *args, _token=None, **kwargs):
        if _token is not _NOTIFICATION_WRITE or kwargs.get('update_fields') is not None:
            service_only()
        self.full_clean()
        return super().save(*args, **kwargs)

    delete = service_only
