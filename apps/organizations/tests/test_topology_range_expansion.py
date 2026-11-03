from itertools import count
from uuid import uuid4

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import User
from apps.locations.models import City, Region
from apps.organizations import topology_services
from apps.organizations.authority import resolve_workspace_authority
from apps.organizations.models import Workspace
from apps.organizations.topology import TopologyValidationResult, validate_workspace_topology
from apps.organizations.topology_services import TopologyMutationError
from apps.organizations.topology_transitions import add_active_range_to_multi_team, activate_prepared_range
from apps.ranges.models import Range, RangeMembership

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup():
    ws = Workspace.objects.create(name="مجموعه", slug="expand", customer_type="consultant",
                                  workspace_mode="team", region_mode="custom")
    sequence = count()
    def user(role="range_manager", **kwargs):
        return User.objects.create_user(username=f"u{next(sequence)}", workspace=ws, role=role, **kwargs)
    def team(manager=None, **kwargs):
        return Range.objects.create(workspace=ws, manager=manager, name=f"رنج {next(sequence)}", **kwargs)
    first = team(user())
    agency = user("agency_manager")
    prepared = team(user(), is_active=False)
    return ws, user, team, first, agency, prepared


def assert_valid(ws, agency):
    result = validate_workspace_topology(ws)
    assert (result.status, result.topology) == ("valid", "multi_team")
    assert resolve_workspace_authority(ws).authority_user.pk == agency.pk


def add(ws, manager, **kwargs):
    return add_active_range_to_multi_team(workspace_id=ws.pk, manager_id=manager.pk,
                                        range_name="جدید", **kwargs)


@pytest.mark.parametrize("purchaser", Workspace.CustomerType.values)
def test_add_preserves_authority_users_and_existing_ranges(setup, purchaser):
    ws, user, team, first, agency, prepared = setup
    team(user())
    Workspace.objects.filter(pk=ws.pk).update(customer_type=purchaser)
    User.objects.filter(pk=prepared.manager_id).update(is_workspace_owner=True, is_staff=True, is_superuser=True)
    before_users = list(User.objects.order_by("pk").values())
    before_ranges = list(Range.objects.order_by("pk").values())
    row = add(ws, prepared.manager)
    assert row.manager_id == prepared.manager_id and row.is_active
    assert Range.objects.filter(is_active=True).count() == 3
    assert list(Range.objects.exclude(pk=row.pk).order_by("pk").values()) == before_ranges
    assert list(User.objects.order_by("pk").values()) == before_users
    assert_valid(ws, agency)


@pytest.mark.parametrize("state", ["single", "invalid", "personal", "null", "inactive"])
def test_add_invalid_start(setup, state):
    ws, user, team, first, agency, prepared = setup
    if state != "single":
        team(user())
    if state == "invalid":
        Range.objects.filter(pk=first.pk).update(manager=None)
    elif state in ("personal", "null"):
        Workspace.objects.filter(pk=ws.pk).update(workspace_mode=None if state == "null" else state)
    elif state == "inactive":
        Workspace.objects.filter(pk=ws.pk).update(is_active=False)
    count_before = Range.objects.count()
    with pytest.raises(TopologyMutationError):
        add(ws, prepared.manager)
    assert Range.objects.count() == count_before


@pytest.mark.parametrize("defect", ["missing", "foreign", "inactive", "role", "assigned", "name", "scope"])
def test_add_rejections(setup, defect):
    ws, user, team, first, agency, prepared = setup
    team(user())
    manager = prepared.manager
    manager_id = manager.pk
    if defect == "missing":
        manager_id = uuid4()
    elif defect == "foreign":
        other = Workspace.objects.create(name="دیگر", slug="other", customer_type="consultant")
        User.objects.filter(pk=manager.pk).update(workspace=other)
    elif defect == "inactive":
        User.objects.filter(pk=manager.pk).update(is_active=False)
    elif defect == "role":
        User.objects.filter(pk=manager.pk).update(role="consultant")
    elif defect == "assigned":
        manager_id = first.manager_id
    count_before = Range.objects.count()
    with pytest.raises(TopologyMutationError):
        add_active_range_to_multi_team(workspace_id=ws.pk, manager_id=manager_id,
                                      range_name=first.name if defect == "name" else "جدید",
                                      activity_scope="invalid" if defect == "scope" else "all")
    assert Range.objects.count() == count_before
    assert_valid(ws, agency)


@pytest.mark.parametrize("multi,explicit", [(False, True), (True, False), (True, True)])
def test_activate_preserves_identity_and_history(setup, multi, explicit):
    ws, user, team, first, agency, prepared = setup
    if multi:
        team(user())
    city = City.objects.create(workspace=ws, name="شهر")
    region = Region.objects.create(workspace=ws, city=city, name="منطقه")
    prepared.regions.add(region)
    member = RangeMembership.objects.create(workspace=ws, range=prepared, user=user("consultant"))
    before_users = list(User.objects.order_by("pk").values())
    manager_id, count_before = prepared.manager_id, Range.objects.count()
    result = activate_prepared_range(workspace_id=ws.pk, range_id=prepared.pk,
                                    agency_manager_id=agency.pk if explicit else None)
    assert result.pk == prepared.pk and result.is_active and result.manager_id == manager_id
    assert Range.objects.count() == count_before
    assert result.regions.filter(pk=region.pk).exists()
    member.refresh_from_db()
    assert member.range_id == prepared.pk
    assert list(User.objects.order_by("pk").values()) == before_users
    assert_valid(ws, agency)


@pytest.mark.parametrize("defect", ["omitted", "missing", "foreign", "inactive", "role", "extra"])
def test_single_activation_agency_rejections(setup, defect):
    ws, user, team, first, agency, prepared = setup
    agency_id = agency.pk
    if defect == "omitted":
        agency_id = None
    elif defect == "missing":
        agency_id = uuid4()
    elif defect == "foreign":
        other = Workspace.objects.create(name="دیگر", slug="other", customer_type="consultant")
        User.objects.filter(pk=agency.pk).update(workspace=other)
    elif defect == "inactive":
        User.objects.filter(pk=agency.pk).update(is_active=False)
    elif defect == "role":
        User.objects.filter(pk=agency.pk).update(role="consultant")
    else:
        user("agency_manager")
    with pytest.raises(TopologyMutationError):
        activate_prepared_range(workspace_id=ws.pk, range_id=prepared.pk, agency_manager_id=agency_id)
    prepared.refresh_from_db()
    assert not prepared.is_active
    assert resolve_workspace_authority(ws).authority_user.pk == first.manager_id


@pytest.mark.parametrize("defect", ["missing_manager", "foreign_manager", "inactive_manager", "role", "assigned",
                                   "active", "foreign_range", "missing_range", "zero"])
def test_prepared_rejections(setup, defect):
    ws, user, team, first, agency, prepared = setup
    range_id = prepared.pk
    if defect == "missing_manager":
        Range.objects.filter(pk=prepared.pk).update(manager=None)
    elif defect == "foreign_manager":
        other = Workspace.objects.create(name="دیگر", slug="other", customer_type="consultant")
        User.objects.filter(pk=prepared.manager_id).update(workspace=other)
    elif defect == "inactive_manager":
        User.objects.filter(pk=prepared.manager_id).update(is_active=False)
    elif defect == "role":
        User.objects.filter(pk=prepared.manager_id).update(role="consultant")
    elif defect == "assigned":
        Range.objects.filter(pk=prepared.pk).update(manager=first.manager_id)
    elif defect == "active":
        range_id = first.pk
    elif defect == "foreign_range":
        other = Workspace.objects.create(name="دیگر", slug="other", customer_type="consultant")
        range_id = Range.objects.create(workspace=other, name="دیگر", is_active=False).pk
    elif defect == "missing_range":
        range_id = uuid4()
    else:
        Range.objects.filter(pk=first.pk).update(is_active=False)
    before = list(Range.objects.order_by("pk").values())
    with pytest.raises(TopologyMutationError) as error:
        activate_prepared_range(workspace_id=ws.pk, range_id=range_id, agency_manager_id=agency.pk)
    if defect == "zero":
        assert error.value.errors == ("team_has_no_active_ranges",)
    assert list(Range.objects.order_by("pk").values()) == before


@pytest.mark.parametrize("conflict", ["agency", "manager"])
def test_multi_activation_conflicts(setup, conflict):
    ws, user, team, first, agency, prepared = setup
    team(user())
    candidate = None
    if conflict == "agency":
        candidate = user("agency_manager", is_active=False).pk
    else:
        Range.objects.filter(pk=prepared.pk).update(manager=first.manager_id)
    with pytest.raises(TopologyMutationError):
        activate_prepared_range(workspace_id=ws.pk, range_id=prepared.pk, agency_manager_id=candidate)
    prepared.refresh_from_db()
    assert not prepared.is_active
    assert_valid(ws, agency)


@pytest.mark.parametrize("operation", ["add", "activate_single", "activate_multi"])
def test_final_failure_rolls_back_and_cancels_callbacks(setup, operation, monkeypatch, django_capture_on_commit_callbacks):
    ws, user, team, first, agency, prepared = setup
    if operation != "activate_single":
        team(user())
    original_authority = resolve_workspace_authority(ws).authority_user.pk
    city = City.objects.create(workspace=ws, name="شهر")
    region = Region.objects.create(workspace=ws, city=city, name="منطقه")
    prepared.regions.add(region)
    member = RangeMembership.objects.create(workspace=ws, range=prepared, user=user("consultant"))
    before = list(Range.objects.order_by("pk").values())
    original = topology_services.validate_workspace_topology
    calls = 0
    def reject_final(workspace):
        nonlocal calls
        calls += 1
        if calls > 1:
            transaction.on_commit(lambda: pytest.fail("callback survived rollback"))
            return TopologyValidationResult("invalid", "team", "multi_team", ("forced_failure",))
        return original(workspace)
    monkeypatch.setattr(topology_services, "validate_workspace_topology", reject_final)
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(TopologyMutationError):
            if operation == "add":
                add(ws, prepared.manager)
            else:
                activate_prepared_range(workspace_id=ws.pk, range_id=prepared.pk, agency_manager_id=agency.pk)
    assert callbacks == []
    assert list(Range.objects.order_by("pk").values()) == before
    assert prepared.regions.filter(pk=region.pk).exists()
    member.refresh_from_db()
    assert member.range_id == prepared.pk
    assert resolve_workspace_authority(ws).authority_user.pk == original_authority


def test_prepared_manager_locked_before_range(setup):
    ws, _, _, first, agency, prepared = setup
    with CaptureQueriesContext(connection) as queries:
        activate_prepared_range(workspace_id=ws.pk, range_id=prepared.pk, agency_manager_id=agency.pk)
    locks = [q["sql"] for q in queries if "FOR UPDATE" in q["sql"].upper()]
    assert "organizations_workspace" in locks[0]
    assert "accounts_user" in locks[1] and "ORDER BY" in locks[1]
    assert prepared.manager_id.hex in locks[1].replace("-", "")
    assert "ranges_range" in locks[2] and "ORDER BY" in locks[2]
