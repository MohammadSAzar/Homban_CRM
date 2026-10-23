"""Explicit Team topology for existing Chat behavior regressions only."""
import pytest

@pytest.fixture
def people(personal_people):
    personal_people.manager = personal_people.user('manager', role='agency_manager')
    personal_people.user('team-fixture-manager', role='agency_manager', ws=personal_people.foreign_workspace)
    return personal_people


def single_team(manager):
    """Explicit topology only for tests that publish or administer Chat."""
    from apps.accounts.models import User
    from apps.organizations.models import Workspace
    from apps.ranges.models import Range
    Workspace.objects.filter(pk=manager.workspace_id).update(workspace_mode='team')
    User.objects.filter(pk=manager.pk).update(role='range_manager')
    manager.refresh_from_db()
    return Range.objects.update_or_create(workspace=manager.workspace, name='authority-team',
                                         defaults={'manager': manager, 'is_active': True})[0]


@pytest.fixture
def authority_people(base_people):
    people = base_people
    from apps.accounts.models import User
    single_team(people.manager)
    single_team(User.objects.get(workspace=people.foreign_workspace, role='agency_manager'))
    return people
