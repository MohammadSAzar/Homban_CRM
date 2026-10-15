import io
import json
import struct
from unittest.mock import patch

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, models, transaction
from mutagen.ogg import OggPage

from apps.chat.models import Message
from apps.chat.services import send_message
from apps.chat.tests.test_attachments import private_storage, post
from apps.chat.tests.test_chat import client
from apps.chat.tests.test_realtime import setup, socket, ticket
from apps.tasks.tests.conftest import people
from apps.notifications.models import Notification
from apps.chat.voice import VOICE_LIMIT, packet_samples

pytestmark = pytest.mark.django_db(transaction=True)


def audio(seconds=1, final_position=None, packet=b'\xf8\xff\xfe'):
    packets = [b'OpusHead' + struct.pack('<BBHIhB', 1, 1, 0, 48000, 0, 0),
               b'OpusTags' + struct.pack('<II', 0, 0)]
    pages = []
    for index, header in enumerate(packets):
        page = OggPage()
        page.serial, page.sequence, page.first = 123, index, index == 0
        page.packets = [header]
        pages.append(page)
    remaining, samples = max(1, round(seconds * 50)), 0
    while remaining:
        count = min(50, remaining)
        remaining -= count
        samples += count * 960
        page = OggPage()
        page.serial, page.sequence = 123, len(pages)
        page.packets, page.position, page.last = [packet] * count, samples, not remaining
        pages.append(page)
    if final_position is not None:
        pages[-1].position = final_position
    return b''.join(page.write() for page in pages)


def voice(data=None, name='recording.ogg', mime='audio/ogg'):
    return SimpleUploadedFile(name, audio() if data is None else data, content_type=mime)


@pytest.mark.parametrize('name,text', [('recording.ogg', ''), ('recording.oga', 'توضیح')])
def test_voice_creation_private_playback_and_state(setup, name, text):
    people, row, _ = setup
    response = post(people, row, voice(name=name), text=text)
    assert response.status_code == 201, response.data
    attachment = response.data['attachment']
    assert attachment == {'kind': 'voice', 'filename': name, 'content_type': 'audio/ogg',
                          'size': len(audio()), 'duration_seconds': 1,
                          'download_url': f"/api/v1/chat/messages/{response.data['id']}/attachment/"}
    message = Message.objects.get()
    assert message.text == text and message.attachment_duration_seconds == 1
    assert message.attachment.name not in response.content.decode()
    with pytest.raises(ValueError): _ = message.attachment.url
    for actor in (people.owner, people.other):
        result = client(actor).get(attachment['download_url'])
        assert result.status_code == 200 and result.streaming
        assert result['Content-Type'] == 'audio/ogg'
        assert result['Content-Disposition'].startswith('inline;')
        assert result['X-Content-Type-Options'] == 'nosniff'
        assert result['Cache-Control'] == 'private, no-store'
        assert b''.join(result.streaming_content) == audio()
        result.close()
    row.refresh_from_db()
    assert row.participant_a_read_at is row.participant_b_read_at is None
    notification = Notification.objects.get()
    assert notification.recipient_id == people.other.pk and notification.read_at is None
    assert name not in notification.message


@pytest.mark.parametrize('seconds,expected', [(0.02, 1), (1.02, 2), (600, 600)])
def test_server_duration_boundaries(setup, seconds, expected):
    people, row, _ = setup
    response = post(people, row, voice(audio(seconds)))
    assert response.status_code == 201, response.data
    assert response.data['attachment']['duration_seconds'] == expected


@pytest.mark.parametrize('mime,name', [('audio/webm', 'a.webm'), ('audio/mpeg', 'a.mp3'),
                                      ('audio/mp4', 'a.m4a'), ('audio/ogg', 'a.mp3')])
def test_unsupported_format_or_suffix(setup, mime, name):
    people, row, _ = setup
    assert post(people, row, voice(name=name, mime=mime)).status_code == 400
    assert not Message.objects.exists()


@pytest.mark.parametrize('failure', ['binary', 'truncated', 'checksum', 'trailing', 'zero', 'too_long', 'packet'])
def test_invalid_audio_has_no_writes(setup, private_storage, failure):
    people, row, _ = setup
    data = audio()
    if failure == 'binary': data = b'MZnot audio'
    elif failure == 'truncated': data = data[:-1]
    elif failure == 'checksum': data = data[:-1] + bytes([data[-1] ^ 1])
    elif failure == 'trailing': data += audio()
    elif failure == 'zero': data = audio(final_position=0)
    elif failure == 'too_long': data = audio(600.02)
    else: data = audio(packet=b'\xfb\x00')
    assert post(people, row, voice(data)).status_code == 400
    assert not Message.objects.exists() and not Notification.objects.exists()
    assert not list(private_storage.rglob('*'))


def test_oversized_voice_and_untrusted_size(setup):
    people, row, _ = setup
    for declared in (VOICE_LIMIT + 1, 1):
        file = voice()
        file.file = io.BytesIO(b'x' * (VOICE_LIMIT + 1))
        file.size = declared
        with pytest.raises(Exception) as error:
            send_message(actor=people.owner, pk=row.pk, attachment=file)
        assert error.value.status_code == 400
    assert not Message.objects.exists()


def test_untrusted_metadata_and_filename(setup):
    people, row, _ = setup
    for field in ('duration_seconds', 'attachment_duration_seconds', 'attachment_kind', 'content_type'):
        assert post(people, row, voice(), **{field: 'forged'}).status_code == 400
    assert post(people, row, voice(name='a' * 181 + '.ogg')).status_code == 400
    response = post(people, row, voice(name='..\\..\\safe.ogg'))
    assert response.status_code == 201 and response.data['attachment']['filename'] == 'safe.ogg'
    assert '..' not in Message.objects.get().attachment.name


@pytest.mark.parametrize('actor_kind', ['outsider', 'foreign'])
def test_voice_download_denies_nonparticipants(setup, actor_kind):
    people, row, _ = setup
    response = post(people, row, voice())
    actor = people.foreign if actor_kind == 'foreign' else people.user('boss', role='agency_manager')
    actor.is_staff = actor.is_superuser = actor.is_workspace_owner = True
    actor.save()
    with patch.object(storages['chat'], 'open', side_effect=AssertionError('unauthorized storage access')):
        assert client(actor).get(response.data['attachment']['download_url']).status_code == 404


def test_voice_realtime_once_ws_remains_text_only(setup):
    people, row, app = setup
    value = ticket(people.other, row)
    async def run():
        async with socket(app, row, value) as (ws, _):
            response = await sync_to_async(post)(people, row, voice())
            assert response.status_code == 201
            event = json.loads((await ws.receive_output(timeout=3))['text'])
            assert event['message'] == dict(response.data, is_own=False)
            assert event['message']['attachment']['kind'] == 'voice'
            assert await ws.receive_nothing()
            for field in ('attachment', 'duration_seconds', 'attachment_kind'):
                await ws.send_input({'type': 'websocket.receive', 'text': json.dumps({
                    'type': 'chat.send', 'text': 'bad', field: 'forged'})})
                assert json.loads((await ws.receive_output(timeout=3))['text'])['type'] == 'chat.error'
    async_to_sync(run)()
    assert Message.objects.count() == Notification.objects.count() == 1


@pytest.mark.parametrize('failure', ['storage', 'database'])
def test_voice_reuses_cleanup(setup, private_storage, failure):
    people, row, _ = setup
    target = patch.object(storages['chat'], 'save', side_effect=OSError('storage failure')) if failure == 'storage' else patch(
        'apps.chat.services.notify_message', side_effect=RuntimeError('failed transaction'))
    with target:
        with pytest.raises(Exception):
            send_message(actor=people.owner, pk=row.pk, attachment=voice())
    assert not Message.objects.exists() and not Notification.objects.exists()
    assert not [p for p in private_storage.rglob('*') if p.is_file()]


def test_duration_database_invariants(setup):
    people, row, _ = setup
    for mime, duration in [('audio/ogg', None), ('audio/ogg', 0), ('audio/ogg', 601), ('text/plain', 1)]:
        with pytest.raises(IntegrityError), transaction.atomic():
            models.QuerySet(model=Message).bulk_create([Message(conversation=row, sender=people.owner,
                attachment='private', attachment_filename='a.ogg', attachment_content_type=mime,
                attachment_size=100, attachment_duration_seconds=duration)])


def test_opus_packet_framing():
    for packet, samples in [(b'\xf8\xff\xfe', 960), (b'\xf9aa', 1920),
                            (b'\xfa\x01ab', 1920), (b'\xfb\x02ab', 1920),
                            (b'\xfb\x82\x01ab', 1920), (b'\xfb\x42\x01abx', 1920)]:
        assert packet_samples(packet) == samples
    for packet in (b'', b'\xf9a', b'\xfb', b'\xfb\x00', b'\xfb\x07', b'\xfa\xff', b'\xfb\x42\xff'):
        with pytest.raises(ValueError): packet_samples(packet)
