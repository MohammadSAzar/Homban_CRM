import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import User
from apps.organizations.models import Workspace
from apps.ranges.models import Range
from apps.chat.announcement_services import delegate_publisher
from apps.chat.general_group_services import set_general_group_enabled
from apps.chat.services import open_conversation, send_message
from apps.chat.tests.fixtures import single_team
from apps.chat.tests.test_attachments import private_storage
from apps.chat.tests.test_chat import client, URL
from apps.tasks.tests.conftest import people

pytestmark = pytest.mark.django_db
A = '/api/v1/chat/announcements/'
P = '/api/v1/chat/announcement-publishers/'
G = '/api/v1/chat/general-group/'


@pytest.fixture
def topology(people):
    manager = people.user('range-manager', role='range_manager')
    team = single_team(manager)
    agency = people.user('agency', role='agency_manager')
    return people, manager, team, agency


def actions(actor, delegate, allowed):
    api = client(actor)
    assert api.post(A, {'text': 'رسمی'}).status_code == (201 if allowed else 403)
    assert api.get(P).status_code == (200 if allowed else 403)
    response = api.post(P, {'user_id': str(delegate.pk)})
    assert response.status_code == (201 if allowed else 403)
    assert api.patch(G + 'settings/', {'is_enabled': True}, format='json').status_code == (200 if allowed else 403)
    if allowed:
        assert api.delete(f"{P}{response.data['id']}/").status_code == 204


@pytest.mark.parametrize('multi', [False, True])
def test_canonical_authority_and_non_authority_roles(topology, multi):
    people, manager, team, agency = topology
    other = people.user('other-range', role='range_manager')
    if multi:
        Range.objects.create(workspace=people.workspace, name='second', manager=other)
    delegate = people.user('delegate', role='secretary')
    actions(agency if multi else manager, delegate, True)
    actions(manager if multi else agency, delegate, False)
    actions(other, delegate, False)


@pytest.mark.parametrize('state', ['personal', 'null', 'zero', 'missing', 'inactive_manager',
                                 'wrong_role', 'foreign_manager', 'multiple_agency', 'no_agency'])
def test_invalid_authority_does_not_replace_compatibility_availability(topology, state):
    people, manager, team, agency = topology
    set_general_group_enabled(actor=manager, is_enabled=True)
    if state in ('personal', 'null'):
        Workspace.objects.filter(pk=people.workspace.pk).update(workspace_mode='personal' if state == 'personal' else None)
    elif state == 'zero':
        Range.objects.filter(pk=team.pk).update(is_active=False)
    elif state == 'missing':
        Range.objects.filter(pk=team.pk).update(manager=None)
    elif state == 'inactive_manager':
        User.objects.filter(pk=manager.pk).update(is_active=False)
    elif state == 'wrong_role':
        User.objects.filter(pk=manager.pk).update(role='consultant')
    elif state == 'foreign_manager':
        User.objects.filter(pk=manager.pk).update(workspace=people.foreign_workspace)
    else:
        Range.objects.create(workspace=people.workspace, name='second', manager=manager)
        if state == 'multiple_agency':
            people.user('second-agency', role='agency_manager')
        else:
            User.objects.filter(pk=agency.pk).update(is_active=False)
    delegate = people.user('delegate', role='secretary')
    # Use active current members, including privileged flags, without stale forced-auth state.
    for actor in User.objects.filter(workspace=people.workspace, is_active=True, role__in=['range_manager', 'agency_manager']):
        actor.is_workspace_owner = actor.is_staff = actor.is_superuser = True
        actor.save()
        actions(actor, delegate, False)
    api = client(people.owner)
    assert api.get(A).status_code == 200
    assert api.get(URL).status_code == 200
    assert api.get(G).status_code == 200
    assert api.post(G, {'text': 'عضو'}).status_code == 201
    assert api.post(A + 'realtime-ticket/', {}, format='json').status_code == 200
    assert api.post(G + 'realtime-ticket/', {}, format='json').status_code == 200


@pytest.mark.parametrize('change', ['none', 'personal', 'null', 'inactive', 'moved', 'role'])
def test_retained_delegation_only_grants_publishing(topology, change):
    people, manager, _, agency = topology
    delegate = people.user('delegate', role='secretary')
    row, _ = delegate_publisher(actor=manager, user_id=delegate.pk)
    if change in ('personal', 'null'):
        Workspace.objects.filter(pk=people.workspace.pk).update(workspace_mode='personal' if change == 'personal' else None)
    elif change == 'inactive':
        User.objects.filter(pk=delegate.pk).update(is_active=False)
    elif change == 'moved':
        people.user('foreign-manager', role='range_manager', ws=people.foreign_workspace)
        User.objects.filter(pk=delegate.pk).update(workspace=people.foreign_workspace)
    elif change == 'role':
        User.objects.filter(pk=delegate.pk).update(role='consultant')
    delegate.refresh_from_db()
    api = client(delegate)
    assert api.post(A, {'text': 'تفویض'}).status_code == (201 if change in ('none', 'personal', 'null') else 403)
    assert api.get(P).status_code == 403
    assert api.delete(f'{P}{row.pk}/').status_code == 403
    assert api.patch(G + 'settings/', {'is_enabled': True}, format='json').status_code == 403


@pytest.mark.parametrize('purchaser', Workspace.CustomerType.values)
def test_purchaser_and_flags_do_not_override_range_manager(topology, purchaser):
    people, manager, _, agency = topology
    Workspace.objects.filter(pk=people.workspace.pk).update(customer_type=purchaser)
    agency.is_workspace_owner = agency.is_staff = agency.is_superuser = True
    agency.save()
    delegate = people.user('delegate', role='secretary')
    actions(agency, delegate, False)
    actions(manager, delegate, True)


def test_inactive_range_excluded_and_authority_deactivation_revokes(topology):
    people, manager, _, agency = topology
    Range.objects.create(workspace=people.workspace, name='inactive', is_active=False)
    delegate = people.user('delegate', role='secretary')
    actions(manager, delegate, True)
    User.objects.filter(pk=manager.pk).update(is_active=False)
    manager.refresh_from_db()
    actions(manager, delegate, False)
    actions(agency, delegate, False)


@pytest.mark.parametrize('kind', ['file', 'voice'])
def test_authority_has_no_private_conversation_or_attachment_access(topology, private_storage, kind):
    people, manager, _, _ = topology
    conversation, _ = open_conversation(actor=people.owner, participant_id=people.other.pk)
    from apps.chat.tests.test_voice import voice
    attachment = voice() if kind == 'voice' else SimpleUploadedFile('note.txt', b'private', content_type='text/plain')
    message = send_message(actor=people.owner, pk=conversation.pk, attachment=attachment)
    api = client(manager)
    assert api.get(URL).data['count'] == 0
    for suffix in ('', 'messages/'):
        assert api.get(f'{URL}{conversation.pk}/{suffix}').status_code == 404
    for suffix, data in [('read/', {}), ('messages/', {'text': 'ممنوع'}), ('realtime-ticket/', {})]:
        assert api.post(f'{URL}{conversation.pk}/{suffix}', data, format='json').status_code == 404
    assert api.get(f'/api/v1/chat/messages/{message.pk}/attachment/').status_code == 404
    conversation.refresh_from_db()
    assert conversation.participant_a_read_at is conversation.participant_b_read_at is None
