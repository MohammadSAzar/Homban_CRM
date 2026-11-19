"""Notification Center stays Core; source entitlement is revalidated live."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.matching.tests.test_collaboration import matching_world, world
from apps.matching.tests.test_live import client_for
from apps.matching.recommendation_services import refresh_recommendation
from apps.notifications.models import Notification
from apps.notifications.producers import recommendation_notification
from apps.notifications.services import publish_notification

pytestmark = pytest.mark.django_db
URL = '/api/v1/notifications/'


def publish(actor, kind, key):
    return publish_notification(recipient=actor, kind=kind, event_key=key,
        title='اعلان', message='پیام', action_url='/review/')


@pytest.mark.parametrize('mode', ['personal', None])
def test_core_visible_team_sources_hidden_and_retained(world, mode):
    own = refresh_recommendation(viewer=world.own, property_file=world.own_file,
                                 customer=world.own_customer)
    cross = refresh_recommendation(viewer=world.own, property_file=world.own_file,
                                   customer=world.other_customer)
    own_note = Notification.objects.get(source_id=own.pk)
    hidden = [Notification.objects.get(source_id=cross.pk)]
    for kind in ('collaboration_request', 'chat_message', 'announcement'):
        hidden.append(publish(world.own, kind, kind)[0])
    # Current producer placeholders for Core import/voice workflows stay eligible.
    core = publish(world.own, 'import_review', 'core')[0]
    world.ws.workspace_mode = mode
    world.ws.customer_type = 'agency_manager'
    world.ws.save()
    world.own.is_staff = world.own.is_superuser = world.own.is_workspace_owner = True
    world.own.save()
    client = client_for(world.own)
    response = client.get(URL)
    assert response.status_code == 200
    assert {r['id'] for r in response.data['results']} == {str(own_note.pk), str(core.pk)}
    assert client.get(URL+'unread-count/').data == {'unread_count': 2}
    for note in hidden:
        assert client.get(f'{URL}{note.pk}/').status_code == 404
        for action in ('read', 'unread'):
            assert client.post(f'{URL}{note.pk}/{action}/', {}, format='json').status_code == 404
    assert client.get(own_note.action_url).status_code == 200
    assert client.get(hidden[0].action_url).status_code == 404
    assert client.post(URL+'read-all/', {}, format='json').data == {'affected_count': 2}
    assert Notification.objects.filter(pk__in=[n.pk for n in hidden], read_at__isnull=True).count() == 4
    before = Notification.objects.count()
    for kind in ('collaboration_request', 'chat_message', 'announcement'):
        assert publish(world.own, kind, 'denied:'+kind) == (None, False)
    assert recommendation_notification(cross, 'reactivated', watermark='denied') == (None, False)
    recommendation_notification(own, 'reactivated', watermark='core')
    assert Notification.objects.count() == before+1
    # Restoring entitlement restores history, not new rows.
    world.ws.workspace_mode = 'team'
    world.ws.save()
    assert client.get(f'{URL}{hidden[0].pk}/').status_code == 200


@pytest.mark.parametrize('mode', ['personal', None, 'team'])
def test_query_growth_and_tenant_privacy(world, mode):
    world.ws.workspace_mode = mode
    world.ws.customer_type = 'agency_manager'
    world.ws.save()
    client = client_for(world.own)
    publish(world.own, 'voice_review', 'first')
    with CaptureQueriesContext(connection) as queries:
        assert client.get(URL).status_code == 200
    small = len(queries)
    for n in range(12):
        publish(world.own, 'voice_review', f'core:{n}')
    with CaptureQueriesContext(connection) as queries:
        response = client.get(URL)
    assert len(queries) == small <= 13
    assert response.data['count'] == 13
    for secret in ('score', 'profile', 'source_id', 'recipient', 'SECRET'):
        assert secret not in response.content.decode()
    note = Notification.objects.filter(recipient=world.own).first()
    assert client_for(world.other).get(f'{URL}{note.pk}/').status_code == 404


@pytest.mark.parametrize('kind,capability', [
    ('collaboration_request', 'collaboration'),
    ('chat_message', 'chat'),
    ('announcement', 'announcements'),
])
def test_team_source_uses_exact_capability_id(kind, capability):
    from apps.notifications.capability_policy import source_allowed

    actor = SimpleNamespace(workspace=object())
    with patch('apps.notifications.capability_policy.resolve_workspace_capability') as resolve:
        resolve.return_value.allowed = True
        assert source_allowed(actor, kind, None)
        resolve.assert_called_once_with(actor.workspace, capability)
