import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .attachments import attachment_path, chat_storage, TYPES, IMAGE_LIMIT, FILE_LIMIT
from .voice import VOICE_MIME, VOICE_LIMIT, VOICE_SECONDS

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
    text = models.TextField(blank=True, default='', validators=[MaxLengthValidator(MAX_TEXT_LENGTH)], verbose_name=_('متن'))
    attachment = models.FileField(storage=chat_storage, upload_to=attachment_path, max_length=255,
                                  blank=True, default='', verbose_name=_('پیوست'))
    attachment_filename = models.CharField(max_length=180, blank=True, default='', verbose_name=_('نام فایل'))
    attachment_content_type = models.CharField(max_length=64, blank=True, default='', verbose_name=_('نوع فایل'))
    attachment_size = models.PositiveIntegerField(null=True, blank=True, verbose_name=_('حجم فایل'))
    attachment_duration_seconds = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name=_('مدت پیام صوتی به ثانیه'))
    created_at = models.DateTimeField(default=timezone.now, editable=False, verbose_name=_('زمان ارسال'))
    objects = ChatQuerySet.as_manager()

    class Meta:
        verbose_name = _('پیام')
        verbose_name_plural = _('پیام‌ها')
        indexes = [models.Index(fields=['conversation', 'created_at', 'id'], name='chat_message_history')]
        constraints = [
            models.CheckConstraint(condition=~Q(text='') | ~Q(attachment=''), name='chat_message_content'),
            models.CheckConstraint(condition=(
                Q(attachment='', attachment_filename='', attachment_content_type='', attachment_size__isnull=True)
                | (~Q(attachment='') & ~Q(attachment_filename='') & Q(attachment_size__isnull=False, attachment_size__gt=0)
                   & (Q(attachment_content_type__in=['image/jpeg', 'image/png', 'image/webp'], attachment_size__lte=IMAGE_LIMIT)
                      | Q(attachment_content_type__in=['application/pdf', 'text/plain'], attachment_size__lte=FILE_LIMIT)
                      | Q(attachment_content_type=VOICE_MIME, attachment_size__lte=VOICE_LIMIT)))
            ), name='chat_attachment_metadata'),
            models.CheckConstraint(condition=(
                Q(attachment_content_type=VOICE_MIME, attachment_duration_seconds__isnull=False,
                  attachment_duration_seconds__gte=1, attachment_duration_seconds__lte=VOICE_SECONDS)
                | (~Q(attachment_content_type=VOICE_MIME) & Q(attachment_duration_seconds__isnull=True))
            ), name='chat_voice_duration'),
        ]

    @property
    def attachment_kind(self):
        if self.attachment_content_type == VOICE_MIME:
            return 'voice'
        return 'image' if self.attachment_content_type.startswith('image/') else 'file'

    def clean(self):
        super().clean()
        if not isinstance(self.text, str) or not (self.text.strip() or self.attachment):
            raise ValidationError({'text': _('متن یا پیوست پیام الزامی است.')})
        if self.attachment:
            if (self.attachment.name != attachment_path(self, '')
                    or self.attachment_content_type not in TYPES):
                raise ValidationError({'attachment': _('پیوست پیام معتبر نیست.')})
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


class Announcement(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name=_('شناسه'))
    workspace = models.ForeignKey('organizations.Workspace', on_delete=models.PROTECT, verbose_name=_('مجموعه'))
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name=_('ناشر'))
    text = models.TextField(validators=[MaxLengthValidator(MAX_TEXT_LENGTH)], verbose_name=_('متن اطلاعیه'))
    created_at = models.DateTimeField(default=timezone.now, editable=False, verbose_name=_('زمان انتشار'))
    objects = ChatQuerySet.as_manager()

    class Meta:
        verbose_name = _('اطلاعیه')
        verbose_name_plural = _('اطلاعیه‌ها')
        indexes = [models.Index(fields=['workspace', '-created_at', 'id'], name='chat_announcement_history')]
        constraints = [models.CheckConstraint(condition=~Q(text=''), name='chat_announcement_text')]

    def clean(self):
        super().clean()
        if self.author_id and self.author.workspace_id != self.workspace_id:
            raise ValidationError(_('ناشر باید متعلق به همین مجموعه باشد.'))
        if not isinstance(self.text, str) or not self.text.strip() or self.text != self.text.strip():
            raise ValidationError({'text': _('متن اطلاعیه معتبر نیست.')})
        if timezone.is_naive(self.created_at):
            raise ValidationError(_('زمان باید دارای منطقه زمانی باشد.'))

    def save(self, *args, _token=None, **kwargs):
        if _token is not _CHAT_WRITE or not self._state.adding or kwargs.get('update_fields') is not None:
            service_only()
        self.full_clean()
        return super().save(*args, **kwargs)

    delete = service_only


class AnnouncementPublisher(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name=_('شناسه'))
    workspace = models.ForeignKey('organizations.Workspace', on_delete=models.PROTECT, verbose_name=_('مجموعه'))
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name=_('ناشر مجاز'))
    created_at = models.DateTimeField(default=timezone.now, editable=False, verbose_name=_('زمان واگذاری'))
    objects = ChatQuerySet.as_manager()

    class Meta:
        verbose_name = _('مجوز انتشار اطلاعیه')
        verbose_name_plural = _('مجوزهای انتشار اطلاعیه')
        constraints = [models.UniqueConstraint(fields=['workspace', 'user'], name='chat_unique_publisher')]

    def clean(self):
        super().clean()
        from apps.accounts.models import User
        if self.user_id and (self.user.workspace_id != self.workspace_id or not self.user.is_active
                or self.user.role not in (User.Role.RANGE_MANAGER, User.Role.SECRETARY)
                or not self.workspace.is_active):
            raise ValidationError(_('مجوز فقط برای مدیر رنج یا منشی فعال همین مجموعه مجاز است.'))
        if timezone.is_naive(self.created_at):
            raise ValidationError(_('زمان باید دارای منطقه زمانی باشد.'))

    def save(self, *args, _token=None, **kwargs):
        if _token is not _CHAT_WRITE or not self._state.adding or kwargs.get('update_fields') is not None:
            service_only()
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, _token=None, **kwargs):
        if _token is not _CHAT_WRITE:
            service_only()
        return super().delete(*args, **kwargs)
