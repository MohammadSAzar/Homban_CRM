from itertools import count
from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import User
from apps.organizations.authority import resolve_workspace_authority
from apps.organizations.models import Workspace
from apps.organizations.topology import validate_workspace_topology
from apps.organizations.topology_services import TopologyMutationError
from apps.organizations import topology_transitions as services
from apps.ranges.models import Range, RangeMembership
from apps.locations.models import City, Region

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup():
    ws = Workspace.objects.create(name="مجموعه", slug="transitions", customer_type="consultant",
                                  workspace_mode="team", region_mode="custom")
    ids = count()
    def user(role="range_manager", **kwargs):
        return User.objects.create_user(username=f"u{next(ids)}", workspace=ws, role=role, **kwargs)
    first = Range.objects.create(workspace=ws, name="اول", manager=user())
    agency = user("agency_manager")
    incoming = user()
    return ws, user, first, agency, incoming


def expand(data, **kwargs):
    ws, _, _, agency, manager = data
    args = dict(workspace_id=ws.pk, agency_manager_id=agency.pk,
                new_range_manager_id=manager.pk, new_range_name="دوم")
    args.update(kwargs)
    return services.transition_single_to_multi(**args)


def authority(ws, user):
    assert resolve_workspace_authority(ws).authority_user.pk == user.pk
    assert validate_workspace_topology(ws).status == "valid"


def test_single_to_multi_handoff_and_independence(setup):
    ws, user, first, agency, manager = setup
    User.objects.filter(pk=first.manager_id).update(is_workspace_owner=True, is_staff=True, is_superuser=True)
    initial_users = list(User.objects.order_by("pk").values())
    authority(ws, first.manager)
    row = expand(setup)
    authority(ws, agency)
    assert row.manager_id == manager.pk
    assert Range.objects.filter(is_active=True).count() == 2
    assert list(User.objects.order_by("pk").values()) == initial_users
    ws.refresh_from_db()
    assert ws.customer_type == "consultant"


@pytest.mark.parametrize("target", ["agency", "range"])
@pytest.mark.parametrize("defect", ["foreign", "inactive", "role", "missing"])
def test_expand_rejects_invalid_users(setup, target, defect):
    ws, _, first, agency, manager = setup
    candidate = agency if target == "agency" else manager
    candidate_id = candidate.pk
    if defect == "foreign":
        foreign = Workspace.objects.create(name="دیگر", slug="foreign", customer_type="consultant")
        User.objects.filter(pk=candidate.pk).update(workspace=foreign)
    elif defect == "inactive":
        User.objects.filter(pk=candidate.pk).update(is_active=False)
    elif defect == "role":
        User.objects.filter(pk=candidate.pk).update(role="consultant")
    else:
        candidate_id = uuid4()
    key = "agency_manager_id" if target == "agency" else "new_range_manager_id"
    with pytest.raises(TopologyMutationError):
        expand(setup, **{key: candidate_id})
    assert Range.objects.count() == 1
    authority(ws, first.manager)


@pytest.mark.parametrize("conflict", ["agency", "manager", "name"])
def test_expand_conflicts_rollback(setup, conflict):
    ws, user, first, _, _ = setup
    kwargs = {}
    if conflict == "agency":
        user("agency_manager")
    elif conflict == "manager":
        kwargs["new_range_manager_id"] = first.manager_id
    else:
        kwargs["new_range_name"] = first.name
    with pytest.raises(TopologyMutationError):
        expand(setup, **kwargs)
    assert Range.objects.count() == 1
    authority(ws, first.manager)


def test_multi_to_single_preserves_history(setup):
    ws, user, first, agency, _ = setup
    second = expand(setup)
    city = City.objects.create(workspace=ws, name="شهر")
    region = Region.objects.create(workspace=ws, city=city, name="منطقه")
    second.regions.add(region)
    membership = RangeMembership.objects.create(workspace=ws, range=second, user=user("consultant"))
    third = Range.objects.create(workspace=ws, manager=user(), name="سوم")
    services.transition_multi_to_single(workspace_id=ws.pk, keep_range_id=first.pk)
    authority(ws, first.manager)
    for row in (second, third):
        manager_id = row.manager_id
        row.refresh_from_db()
        assert not row.is_active and row.manager_id == manager_id
    assert second.regions.filter(pk=region.pk).exists()
    membership.refresh_from_db()
    assert membership.range_id == second.pk
    agency.refresh_from_db()
    assert agency.is_active and agency.role == "agency_manager"


@pytest.mark.parametrize("defect", ["missing", "foreign", "inactive"])
def test_invalid_keep_range_preserves_topology(setup, defect):
    ws, _, first, agency, _ = setup
    expand(setup)
    keep_id = uuid4()
    if defect != "missing":
        owner = ws if defect == "inactive" else Workspace.objects.create(name="دیگر", slug="foreign", customer_type="consultant")
        keep_id = Range.objects.create(workspace=owner, name="دیگر", is_active=False).pk
    with pytest.raises(TopologyMutationError):
        services.transition_multi_to_single(workspace_id=ws.pk, keep_range_id=keep_id)
    assert Range.objects.filter(workspace=ws, is_active=True).count() == 2
    authority(ws, agency)


@pytest.mark.parametrize("multi", [False, True])
def test_range_replacement_handoff(setup, multi):
    ws, user, first, agency, _ = setup
    if multi:
        expand(setup)
    outgoing = first.manager
    User.objects.filter(pk=outgoing.pk).update(is_workspace_owner=True)
    incoming = user()
    services.replace_range_manager(workspace_id=ws.pk, range_id=first.pk, incoming_manager_id=incoming.pk)
    authority(ws, agency if multi else incoming)
    outgoing.refresh_from_db()
    assert outgoing.is_active and outgoing.role == "range_manager" and outgoing.is_workspace_owner


@pytest.mark.parametrize("defect", ["missing", "foreign", "inactive", "role", "assigned", "same"])
def test_range_replacement_rejections(setup, defect):
    ws, user, first, agency, _ = setup
    second = expand(setup)
    incoming = user()
    candidate = incoming.pk
    if defect == "missing":
        candidate = uuid4()
    elif defect == "same":
        candidate = first.manager_id
    elif defect == "assigned":
        candidate = second.manager_id
    elif defect == "foreign":
        foreign = Workspace.objects.create(name="دیگر", slug="foreign", customer_type="consultant")
        User.objects.filter(pk=incoming.pk).update(workspace=foreign)
    else:
        User.objects.filter(pk=incoming.pk).update(**({"is_active": False} if defect == "inactive" else {"role": "consultant"}))
    old = first.manager_id
    with pytest.raises(TopologyMutationError):
        services.replace_range_manager(workspace_id=ws.pk, range_id=first.pk, incoming_manager_id=candidate)
    first.refresh_from_db()
    assert first.manager_id == old
    authority(ws, agency)


@pytest.mark.parametrize("incoming_owner", [False, True])
def test_agency_replacement(setup, incoming_owner):
    ws, user, _, outgoing, _ = setup
    expand(setup)
    incoming = user("agency_manager", is_active=False, is_workspace_owner=incoming_owner)
    ranges_before = list(Range.objects.order_by("pk").values())
    count_before = User.objects.count()
    services.replace_multi_team_agency_manager(workspace_id=ws.pk, incoming_agency_manager_id=incoming.pk)
    authority(ws, incoming)
    incoming.refresh_from_db()
    outgoing.refresh_from_db()
    assert incoming.is_active and not outgoing.is_active
    assert incoming.role == outgoing.role == "agency_manager"
    assert incoming.is_workspace_owner == incoming_owner and not outgoing.is_workspace_owner
    assert User.objects.count() == count_before
    assert list(Range.objects.order_by("pk").values()) == ranges_before


@pytest.mark.parametrize("defect", ["owner", "active", "role", "foreign", "missing", "same"])
def test_agency_replacement_rejections(setup, defect):
    ws, user, _, outgoing, _ = setup
    expand(setup)
    incoming = user("agency_manager", is_active=False)
    candidate = incoming.pk
    if defect == "owner":
        User.objects.filter(pk=outgoing.pk).update(is_workspace_owner=True)
    elif defect == "active":
        User.objects.filter(pk=incoming.pk).update(is_active=True)
    elif defect == "role":
        User.objects.filter(pk=incoming.pk).update(role="consultant")
    elif defect == "foreign":
        foreign = Workspace.objects.create(name="دیگر", slug="foreign", customer_type="consultant")
        User.objects.filter(pk=incoming.pk).update(workspace=foreign)
    elif defect == "missing":
        candidate = uuid4()
    else:
        candidate = outgoing.pk
    before = list(User.objects.order_by("pk").values())
    with pytest.raises(TopologyMutationError) as error:
        services.replace_multi_team_agency_manager(workspace_id=ws.pk, incoming_agency_manager_id=candidate)
    if defect == "owner":
        assert error.value.errors == ("outgoing_agency_manager_is_owner",)
    assert list(User.objects.order_by("pk").values()) == before


@pytest.mark.parametrize("operation", ["expand", "contract", "range", "agency"])
def test_final_validation_failure_rolls_back_everything(setup, operation, monkeypatch, django_capture_on_commit_callbacks):
    from apps.organizations import topology_services
    from apps.organizations.topology import TopologyValidationResult
    ws, user, first, outgoing, manager = setup
    if operation in ("contract", "agency"):
        expand(setup)
    incoming = user("agency_manager", is_active=False)
    original_authority = resolve_workspace_authority(ws).authority_user
    ranges_before = list(Range.objects.order_by("pk").values())
    users_before = list(User.objects.order_by("pk").values())
    original = topology_services.validate_workspace_topology
    calls = 0
    def reject_final(workspace):
        nonlocal calls
        calls += 1
        if calls > 1:
            return TopologyValidationResult("invalid", "team", "unresolved", ("forced_final_failure",))
        return original(workspace)
    monkeypatch.setattr(topology_services, "validate_workspace_topology", reject_final)
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(TopologyMutationError):
            if operation == "expand":
                expand(setup)
            elif operation == "contract":
                services.transition_multi_to_single(workspace_id=ws.pk, keep_range_id=first.pk)
            elif operation == "range":
                services.replace_range_manager(workspace_id=ws.pk, range_id=first.pk, incoming_manager_id=manager.pk)
            else:
                services.replace_multi_team_agency_manager(workspace_id=ws.pk, incoming_agency_manager_id=incoming.pk)
    assert callbacks == []
    assert list(Range.objects.order_by("pk").values()) == ranges_before
    assert list(User.objects.order_by("pk").values()) == users_before
    authority(ws, original_authority)


@pytest.mark.parametrize("state", ["personal", "null", "inactive", "invalid"])
def test_invalid_start_not_repaired(setup, state):
    ws, _, first, _, _ = setup
    if state == "invalid":
        Range.objects.filter(pk=first.pk).update(manager=None)
    else:
        updates = {"is_active": False} if state == "inactive" else {"workspace_mode": None if state == "null" else "personal"}
        Workspace.objects.filter(pk=ws.pk).update(**updates)
    with pytest.raises(TopologyMutationError):
        expand(setup)
    assert Range.objects.count() == 1


def test_lock_discovery_stays_workspace_users_ranges(setup):
    ws, user, _, _, _ = setup
    expand(setup)
    incoming = user("agency_manager", is_active=False)
    with CaptureQueriesContext(connection) as captured:
        services.replace_multi_team_agency_manager(workspace_id=str(ws.pk), incoming_agency_manager_id=str(incoming.pk))
    locks = [row["sql"] for row in captured if "FOR UPDATE" in row["sql"].upper()]
    assert "organizations_workspace" in locks[0]
    assert "accounts_user" in locks[1] and "ORDER BY" in locks[1]
    assert "ranges_range" in locks[2] and "ORDER BY" in locks[2]
    # Existing User save tracking reacquires only the already-held Workspace lock.
    assert all("organizations_workspace" in sql for sql in locks[3:])
