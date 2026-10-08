import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

_CHAT_WRITE = object()
MAX_TEXT_LENGTH = 4000


def service_only(*args, **kwargs):
    raise ValidationError(_('تغییر گفتگو فقط از طریق سرویس گفتگو مجاز است.'))


class ChatQuerySet(models.QuerySet):
    update = bulk_update = bulk_create = delete = service_only


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name=_('شناسه'))
    workspace = models.ForeignKey('organizations.Workspace', on_delete=models.PROTECT, verbose_name=_('مجموعه'))
    participant_a = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='chats_a', verbose_name=_('عضو اول'))
    participant_b = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='chats_b', verbose_name=_('عضو دوم'))
    participant_a_read_at = models.DateTimeField(null=True, blank=True, verbose_name=_('آخرین خواندن عضو اول'))
    participant_b_read_at = models.DateTimeField(null=True, blank=True, verbose_name=_('آخرین خواندن عضو دوم'))
    created_at = models.DateTimeField(default=timezone.now, editable=False, verbose_name=_('زمان ایجاد'))
    updated_at = models.DateTimeField(default=timezone.now, editable=False, verbose_name=_('آخرین فعالیت'))
    objects = ChatQuerySet.as_manager()

    class Meta:
        verbose_name = _('گفتگوی خصوصی')
        verbose_name_plural = _('گفتگوهای خصوصی')
        constraints = [
            models.UniqueConstraint(fields=['workspace', 'participant_a', 'participant_b'], name='chat_unique_pair'),
            models.CheckConstraint(condition=Q(participant_a__lt=F('participant_b')), name='chat_ordered_pair'),
        ]
        indexes = [
            models.Index(fields=['workspace', 'participant_a', 'updated_at'], name='chat_a_activity'),
            models.Index(fields=['workspace', 'participant_b', 'updated_at'], name='chat_b_activity'),
        ]

    def clean(self):
        super().clean()
        from apps.accounts.models import User
        if self.participant_a_id and self.participant_b_id:
            if self.participant_a_id >= self.participant_b_id:
                raise ValidationError(_('دو عضو متمایز با ترتیب معتبر لازم است.'))
            if User.objects.filter(pk__in=[self.participant_a_id, self.participant_b_id],
                    workspace_id=self.workspace_id, workspace__is_active=True,
                    is_active=True, role__in=User.Role.values).count() != 2:
                raise ValidationError(_('اعضای گفتگو باید کاربران فعال همین مجموعه باشند.'))
        for name in ('created_at', 'updated_at', 'participant_a_read_at', 'participant_b_read_at'):
            value = getattr(self, name)
            if value is not None and timezone.is_naive(value):
                raise ValidationError({name: _('زمان باید دارای منطقه زمانی باشد.')})
        if not self._state.adding:
            old = type(self).objects.get(pk=self.pk)
            if any(getattr(old, name) != getattr(self, name) for name in
                   ('workspace_id', 'participant_a_id', 'participant_b_id', 'created_at')):
                raise ValidationError(_('اعضای گفتگو و مجموعه قابل تغییر نیستند.'))

    def save(self, *args, _token=None, **kwargs):
        if _token is not _CHAT_WRITE or kwargs.get('update_fields') is not None:
            service_only()
        self.full_clean()
        return super().save(*args, **kwargs)

    delete = service_only


class Message(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name=_('شناسه'))
    conversation = models.ForeignKey(Conversation, on_delete=models.PROTECT, related_name='messages', verbose_name=_('گفتگو'))
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name=_('فرستنده'))
    text = models.TextField(validators=[MaxLengthValidator(MAX_TEXT_LENGTH)], verbose_name=_('متن'))
    created_at = models.DateTimeField(default=timezone.now, editable=False, verbose_name=_('زمان ارسال'))
    objects = ChatQuerySet.as_manager()

    class Meta:
        verbose_name = _('پیام')
        verbose_name_plural = _('پیام‌ها')
        indexes = [models.Index(fields=['conversation', 'created_at', 'id'], name='chat_message_history')]
        constraints = [models.CheckConstraint(condition=~Q(text=''), name='chat_message_not_empty')]

    def clean(self):
        super().clean()
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValidationError({'text': _('متن پیام الزامی است.')})
        if timezone.is_naive(self.created_at):
            raise ValidationError(_('زمان باید دارای منطقه زمانی باشد.'))
        if self.sender_id not in (self.conversation.participant_a_id, self.conversation.participant_b_id):
            raise ValidationError(_('فرستنده باید عضو گفتگو باشد.'))
        self.conversation.clean()

    def save(self, *args, _token=None, **kwargs):
        if _token is not _CHAT_WRITE or not self._state.adding or kwargs.get('update_fields') is not None:
            service_only()
        self.full_clean()
        return super().save(*args, **kwargs)

    delete = service_only
