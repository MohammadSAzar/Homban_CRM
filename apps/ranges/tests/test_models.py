import pytest
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import User
from apps.locations.models import City, Region
from apps.organizations.models import Workspace
from apps.ranges.models import Range


@pytest.mark.django_db
def test_workspace_deletion_removes_related_data():
    workspace = Workspace.objects.create(
        name="املاک تست",
        slug="delete-test",
        customer_type="agency_manager",
    )

    manager = User.objects.create_user(
        username="range_manager",
        password="StrongPass123!",
        workspace=workspace,
        role="range_manager",
    )

    team = Range.objects.create(
        workspace=workspace,
        name="رنج تست",
        manager=manager,
    )

    workspace_id, manager_id, range_id = workspace.pk, manager.pk, team.pk
    workspace.delete()

    assert not Workspace.objects.filter(pk=workspace_id).exists()
    assert not User.objects.filter(pk=manager_id).exists()
    assert not Range.objects.filter(pk=range_id).exists()
    assert not Range.objects.filter(workspace_id=workspace_id).exists()


@pytest.fixture
def region_assignment(db):
    workspaces = [
        Workspace.objects.create(name="مجموعه تست", slug=slug, customer_type="agency_manager")
        for slug in ("first", "second")
    ]
    teams = []
    regions = []
    for workspace in workspaces:
        teams.append(Range.objects.create(workspace=workspace, name="رنج تست"))
        city = City.objects.create(workspace=workspace, name="تهران")
        regions.append(Region.objects.create(workspace=workspace, city=city, name="منطقه تست"))
    return teams, regions


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("operation", ["add", "set", "set_clear"])
def test_same_workspace_region_assignment(region_assignment, reverse, operation):
    teams, regions = region_assignment
    relation = regions[0].ranges if reverse else teams[0].regions
    target = teams[0] if reverse else regions[0]
    if operation == "add":
        relation.add(target.pk)
        relation.add(target.pk)  # Repeated assignment is harmless.
    else:
        relation.set([target.pk], clear=operation == "set_clear")
    assert list(relation.values_list("pk", flat=True)) == [target.pk]


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("operation", ["add", "set", "set_clear"])
def test_cross_workspace_region_assignment_is_atomic(region_assignment, reverse, operation):
    teams, regions = region_assignment
    relation = regions[0].ranges if reverse else teams[0].regions
    valid, invalid = teams if reverse else regions
    relation.add(valid)

    with pytest.raises(ValidationError) as error:
        with transaction.atomic():
            if operation == "add":
                relation.add(valid.pk, invalid.pk)
            else:
                relation.set([invalid.pk], clear=operation == "set_clear")

    assert "regions" in error.value.message_dict
    assert list(relation.values_list("pk", flat=True)) == [valid.pk]


@pytest.mark.parametrize("operation", ["remove", "clear"])
def test_region_assignment_can_be_removed(region_assignment, operation):
    teams, regions = region_assignment
    relation = teams[0].regions
    relation.add(regions[0])
    if operation == "remove":
        relation.remove(regions[0])
    else:
        relation.clear()
    assert not relation.exists()

