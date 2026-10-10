import io
import json
from unittest.mock import patch

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.storage import storages
from django.db import IntegrityError, models, transaction
from PIL import Image

from apps.accounts.models import User
from apps.chat.attachments import FILE_LIMIT, IMAGE_LIMIT
from apps.chat.models import Message
from apps.chat.services import send_message
from apps.chat.tests.test_chat import client, URL
from apps.chat.tests.test_realtime import setup, socket, ticket
from apps.notifications.models import Notification
from apps.tasks.tests.conftest import people

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def private_storage(settings, tmp_path):
    settings.STORAGES = {**settings.STORAGES, 'chat': {
        'BACKEND': 'apps.chat.attachments.PrivateChatStorage',
        'OPTIONS': {'location': tmp_path},
    }}
    return tmp_path


def upload(kind='text/plain', name=None):
    if kind.startswith('image/'):
        format_name, extension = {'image/jpeg': ('JPEG', 'jpg'), 'image/png': ('PNG', 'png'),
                                  'image/webp': ('WEBP', 'webp')}[kind]
        stream = io.BytesIO()
        Image.new('RGB', (2, 2), 'white').save(stream, format_name)
        data = stream.getvalue()
    elif kind == 'application/pdf':
        extension, data = 'pdf', b'%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n'
    else:
        extension, data = 'txt', 'متن خصوصی'.encode()
    return SimpleUploadedFile(name or f'private.{extension}', data, content_type=kind)


def post(people, row, file, **extra):
    return client(people.owner).post(f'{URL}{row.pk}/messages/', {'attachment': file, **extra}, format='multipart')


@pytest.mark.parametrize('kind', ['image/jpeg', 'image/png', 'image/webp', 'application/pdf', 'text/plain'])
def test_supported_attachment_only_and_private_stream(setup, kind, private_storage):
    people, row, _ = setup
    file = upload(kind)
    expected = file.read()
    file.seek(0)
    response = post(people, row, file)
    assert response.status_code == 201, response.data
    data = response.data['attachment']
    assert response.data['text'] == ''
    assert data == {'kind': 'image' if kind.startswith('image/') else 'file', 'filename': file.name,
                    'content_type': kind, 'size': len(expected),
                    'download_url': f"/api/v1/chat/messages/{response.data['id']}/attachment/"}
    message = Message.objects.get()
    assert message.attachment.name == f'chat/{people.workspace.pk}/{row.pk}/{message.pk}/attachment'
    assert message.attachment.name not in response.content.decode()
    assert str(private_storage) not in response.content.decode()
    with pytest.raises(ValueError): _ = message.attachment.url
    for actor in (people.owner, people.other):
        result = client(actor).get(data['download_url'])
        assert result.status_code == 200 and result.streaming
        assert result['Content-Type'] == kind and result['Content-Disposition'].startswith('attachment;')
        assert result['Cache-Control'] == 'private, no-store' and result['X-Content-Type-Options'] == 'nosniff'
        assert b''.join(result.streaming_content) == expected
        result.close()
    assert Notification.objects.count() == 1
    assert file.name not in Notification.objects.get().message
    row.refresh_from_db()
    assert row.participant_a_read_at is row.participant_b_read_at is None


def test_text_plus_attachment_and_history(setup):
    people, row, _ = setup
    response = post(people, row, upload(), text=' توضیح ')
    assert response.status_code == 201 and response.data['text'] == 'توضیح'
    history = client(people.other).get(f'{URL}{row.pk}/messages/').data['results']
    assert history[0]['attachment'] == response.data['attachment']
    assert client(people.other).get(f'{URL}{row.pk}/').data['unread_count'] == 1


@pytest.mark.parametrize('name,mime,content', [
    ('run.exe', 'application/octet-stream', b'MZ\x00executable'),
    ('fake.png', 'image/png', b'not an image'),
    ('fake.pdf', 'application/pdf', b'MZfake'),
    ('fake.txt', 'text/plain', b'MZ\x00\xff'),
    ('fake.jpg', 'image/png', b'fake'),
])
def test_invalid_content_rejected(setup, private_storage, name, mime, content):
    people, row, _ = setup
    response = post(people, row, SimpleUploadedFile(name, content, content_type=mime))
    assert response.status_code == 400
    assert not Message.objects.exists() and not Notification.objects.exists()
    assert not list(private_storage.rglob('*'))


@pytest.mark.parametrize('kind,limit', [('image/png', IMAGE_LIMIT), ('text/plain', FILE_LIMIT)])
def test_oversize_rejected(setup, kind, limit):
    people, row, _ = setup
    file = upload(kind)
    file.file = io.BytesIO(b'x' * (limit + 1))
    file.size = limit + 1
    assert post(people, row, file).status_code == 400
    assert not Message.objects.exists()


def test_exact_file_limit_and_real_size_validation(setup):
    people, row, _ = setup
    file = SimpleUploadedFile('large.txt', b'a' * FILE_LIMIT, content_type='text/plain')
    message = send_message(actor=people.owner, pk=row.pk, attachment=file)
    assert message.attachment_size == FILE_LIMIT
    file = SimpleUploadedFile('large.txt', b'a' * (FILE_LIMIT + 1), content_type='text/plain')
    file.size = 1  # Domain boundary must not trust a caller's size metadata.
    with pytest.raises(Exception) as error:
        send_message(actor=people.owner, pk=row.pk, attachment=file)
    assert error.value.status_code == 400 and Message.objects.count() == 1


@pytest.mark.parametrize('name', ['..\\..\\private.txt', 'a' * 181 + '.txt'])
def test_filename_safety(setup, name):
    people, row, _ = setup
    response = post(people, row, upload(name=name))
    if len(name) > 180:
        assert response.status_code == 400 and not Message.objects.exists()
    else:
        assert response.status_code == 201 and response.data['attachment']['filename'] == 'private.txt'
        assert '..' not in Message.objects.get().attachment.name


@pytest.mark.parametrize('state', ['outsider', 'foreign', 'moved', 'inactive', 'workspace'])
def test_download_current_permissions_no_bypass(setup, state):
    people, row, _ = setup
    response = post(people, row, upload())
    actor = people.owner
    if state == 'outsider':
        actor = people.user('boss', role='agency_manager')
        actor.is_staff = actor.is_superuser = actor.is_workspace_owner = True
        actor.save()
    elif state == 'foreign': actor = people.foreign
    elif state == 'moved':
        models.QuerySet(model=User).filter(pk=people.other.pk).update(workspace=people.foreign_workspace)
    elif state == 'inactive':
        people.other.is_active = False
        people.other.save()
    else:
        people.workspace.is_active = False
        people.workspace.save()
    with patch.object(storages['chat'], 'open', side_effect=AssertionError('must not access storage')):
        result = client(actor).get(response.data['attachment']['download_url'])
    assert result.status_code == (403 if state == 'workspace' else 404)


def test_strict_fields_multiple_files_and_no_attachment_lookup(setup):
    people, row, _ = setup
    for extra in ({'sender': 'forged'}, {'attachment_size': 1}, {'attachment_content_type': 'image/png'},
                  {'workspace': 'forged'}, {'attachment_path': 'secret'}):
        assert post(people, row, upload(), **extra).status_code == 400
    assert post(people, row, [upload(), upload()]).status_code == 400
    message = send_message(actor=people.owner, pk=row.pk, text='text only')
    assert client(people.owner).get(f'/api/v1/chat/messages/{message.pk}/attachment/').status_code == 404
    assert Message.objects.count() == 1


@pytest.mark.parametrize('failure', ['storage', 'database'])
def test_failed_send_cleans_file_and_rolls_back(setup, private_storage, failure):
    people, row, _ = setup
    storage = storages['chat']
    def partial_save(name, content, **kwargs):
        storage._save(name, content)
        raise OSError('private filesystem error')
    target = patch.object(storage, 'save', side_effect=partial_save) if failure == 'storage' else patch(
        'apps.chat.services.notify_message', side_effect=RuntimeError('notification failure'))
    with target:
        if failure == 'storage':
            response = post(people, row, upload())
            assert response.status_code == 503 and b'filesystem' not in response.content
        else:
            with pytest.raises(RuntimeError): send_message(actor=people.owner, pk=row.pk, attachment=upload())
    assert not Message.objects.exists() and not Notification.objects.exists()
    assert not [p for p in private_storage.rglob('*') if p.is_file()]


def test_rest_attachment_realtime_once_and_ws_stays_text_only(setup):
    people, row, app = setup
    value = ticket(people.other, row)
    async def run():
        async with socket(app, row, value) as (ws, _):
            response = await sync_to_async(post)(people, row, upload())
            assert response.status_code == 201
            event = json.loads((await ws.receive_output(timeout=3))['text'])
            assert event['type'] == 'chat.message'
            assert event['message'] == dict(response.data, is_own=False)
            assert await ws.receive_nothing()
            await ws.send_input({'type': 'websocket.receive', 'text': json.dumps({
                'type': 'chat.send', 'text': 'bad', 'attachment': 'encoded bytes'})})
            assert json.loads((await ws.receive_output(timeout=3))['text'])['type'] == 'chat.error'
    async_to_sync(run)()
    assert Message.objects.count() == Notification.objects.count() == 1


def test_database_empty_and_incomplete_metadata_constraints(setup):
    people, row, _ = setup
    for fields in ({'text': ''}, {'text': '', 'attachment': 'bad'},
                   {'text': 'hi', 'attachment_size': 1}):
        with pytest.raises(IntegrityError), transaction.atomic():
            models.QuerySet(model=Message).bulk_create([Message(conversation=row, sender=people.owner, **fields)])
