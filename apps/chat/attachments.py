"""Private storage and bounded content validation for one Chat upload."""
import codecs
import unicodedata
import warnings
from pathlib import PurePosixPath

from django.core.files.storage import FileSystemStorage, Storage, storages
from django.core.files.uploadedfile import UploadedFile
from django.utils.translation import gettext_lazy as _
from PIL import Image, UnidentifiedImageError
from rest_framework.exceptions import APIException, ValidationError
from .voice import VOICE_MIME, VOICE_LIMIT, voice_duration

IMAGE_LIMIT = 10 * 1024 * 1024
FILE_LIMIT = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
TYPES = {
    'image/jpeg': ('.jpg', '.jpeg'),
    'image/png': ('.png',),
    'image/webp': ('.webp',),
    'application/pdf': ('.pdf',),
    'text/plain': ('.txt',),
    VOICE_MIME: ('.ogg', '.oga'),
}


class PrivateChatStorage(FileSystemStorage):
    def url(self, name):
        raise ValueError('Chat files require authorized download.')


def chat_storage():
    return ChatStorage()


class ChatStorage(Storage):
    """Resolve the private alias at use time, including under settings overrides."""
    def _open(self, name, mode='rb'):
        return storages['chat'].open(name, mode)

    def _save(self, name, content):
        return storages['chat'].save(name, content)

    def exists(self, name):
        return storages['chat'].exists(name)

    def delete(self, name):
        return storages['chat'].delete(name)

    def url(self, name):
        raise ValueError('Chat files require authorized download.')


def attachment_path(instance, filename):
    # Neither uploaded names nor other user-supplied text become storage keys.
    return f'chat/{instance.conversation.workspace_id}/{instance.conversation_id}/{instance.pk}/attachment'


class AttachmentUnavailable(APIException):
    status_code = 503
    default_detail = _('دسترسی به فایل در حال حاضر ممکن نیست.')
    default_code = 'attachment_unavailable'


def invalid_upload():
    raise ValidationError({'attachment': _('نوع، محتوا یا اندازه فایل معتبر نیست.')})


def validate_upload(upload):
    """Return safe metadata; MIME and suffix must agree with inspected content.

    PDF validation is envelope-only; text is UTF-8. This is not malware scanning.
    Reads use bounded chunks; only image decoding allocates pixels (also bounded).
    """
    if not isinstance(upload, UploadedFile):
        invalid_upload()
    filename = PurePosixPath(str(upload.name).replace('\\', '/')).name
    filename = ''.join(c for c in unicodedata.normalize('NFC', filename)
                       if not unicodedata.category(c).startswith('C')).strip(' .')
    content_type = getattr(upload, 'content_type', '')
    suffix = PurePosixPath(filename).suffix.lower()
    if not filename or len(filename) > 180 or content_type not in TYPES or suffix not in TYPES[content_type]:
        invalid_upload()
    limit = VOICE_LIMIT if content_type == VOICE_MIME else IMAGE_LIMIT if content_type.startswith('image/') else FILE_LIMIT
    if upload.size is None or upload.size <= 0 or upload.size > limit:
        invalid_upload()
    size = 0
    decoder = codecs.getincrementaldecoder('utf-8')() if content_type == 'text/plain' else None
    duration = None
    try:
        upload.seek(0)
        while chunk := upload.read(64 * 1024):
            size += len(chunk)
            if size > limit:
                invalid_upload()
            if decoder:
                text = decoder.decode(chunk)
                if any(ord(c) < 32 and c not in '\n\r\t' or 127 <= ord(c) < 160 for c in text):
                    invalid_upload()
        if not size:
            invalid_upload()
        if decoder:
            decoder.decode(b'', final=True)
        upload.seek(0)
        if content_type == VOICE_MIME:
            duration = voice_duration(upload)
        elif content_type.startswith('image/'):
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(upload) as image:
                    if Image.MIME.get(image.format) != content_type or image.width * image.height > MAX_IMAGE_PIXELS:
                        invalid_upload()
                    if getattr(image, 'n_frames', 1) != 1:
                        invalid_upload()
                    image.verify()
                upload.seek(0)
                with Image.open(upload) as image:
                    image.load()  # Reject truncated/corrupt data beyond the header.
        elif content_type == 'application/pdf':
            header = upload.read(8)
            upload.seek(max(0, size - 1024))
            if not (header.startswith(b'%PDF-1.') or header == b'%PDF-2.0') or not upload.read().rstrip().endswith(b'%%EOF'):
                invalid_upload()
    except (UnicodeError, OSError, ValueError, UnidentifiedImageError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        invalid_upload()
    finally:
        if not upload.closed:
            upload.seek(0)
    return filename, content_type, size, duration
