"""Explicit Team topology for existing Chat behavior regressions only."""
import pytest

@pytest.fixture
def people(personal_people):
    personal_people.manager = personal_people.user('manager', role='agency_manager')
    personal_people.user('team-fixture-manager', role='agency_manager', ws=personal_people.foreign_workspace)
    return personal_people
