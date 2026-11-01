from uuid import uuid4

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import User
from apps.organizations.authority import resolve_workspace_authority
from apps.organizations.models import Workspace
from apps.organizations.topology import validate_workspace_topology
from apps.organizations.topology_services import (
    TopologyMutationError, bootstrap_team_first_range, topology_transaction,
)
from apps.ranges.models import Range

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup():
    workspace = Workspace.objects.create(name="مجموعه", slug="bootstrap", customer_type="consultant", workspace_mode="team")
    manager = User.objects.create_user(username="manager", workspace=workspace, role="range_manager")
    return workspace, manager


def bootstrap(workspace, manager, **kwargs):
    return bootstrap_team_first_range(workspace_id=workspace.pk, manager_id=manager.pk,
                                     range_name="رنج", **kwargs)


def test_bootstrap_validates_and_resolves_authority(setup):
    workspace, manager = setup
    row = bootstrap(workspace, manager)
    assert row.manager_id == manager.pk and row.is_active
    assert Range.objects.filter(workspace=workspace).count() == 1
    assert validate_workspace_topology(workspace).status == "valid"
    assert resolve_workspace_authority(workspace).authority_user == manager


@pytest.mark.parametrize("mode", ["personal", None])
def test_non_team_rejected(setup, mode):
    workspace, manager = setup
    Workspace.objects.filter(pk=workspace.pk).update(workspace_mode=mode)
    with pytest.raises(TopologyMutationError) as error:
        bootstrap(workspace, manager)
    assert error.value.errors == ("workspace_not_team",)
    assert not Range.objects.exists()


@pytest.mark.parametrize("missing", [False, True])
def test_workspace_missing_or_inactive(setup, missing):
    workspace, manager = setup
    Workspace.objects.filter(pk=workspace.pk).update(is_active=False)
    with pytest.raises(TopologyMutationError) as error:
        bootstrap_team_first_range(workspace_id=uuid4() if missing else workspace.pk,
                                  manager_id=manager.pk, range_name="رنج")
    assert error.value.errors == (("workspace_missing",) if missing else ("workspace_inactive",))


def test_existing_active_range_rejected_even_on_repeat(setup):
    workspace, manager = setup
    bootstrap(workspace, manager)
    with pytest.raises(TopologyMutationError) as error:
        bootstrap(workspace, manager)
    assert error.value.errors == ("team_already_has_active_range",)
    assert Range.objects.count() == 1


def test_inactive_history_retained(setup):
    workspace, manager = setup
    old = Range.objects.create(workspace=workspace, manager=manager, name="قدیمی", is_active=False)
    bootstrap(workspace, manager)
    old.refresh_from_db()
    assert not old.is_active and old.manager_id == manager.pk
    assert Range.objects.count() == 2


@pytest.mark.parametrize("defect,code", [
    ("missing", "manager_missing"), ("inactive", "manager_inactive"),
    ("role", "manager_wrong_role"), ("foreign", "manager_wrong_workspace"),
])
def test_manager_rejections(setup, defect, code):
    workspace, manager = setup
    manager_id = manager.pk
    if defect == "missing":
        manager_id = uuid4()
    elif defect == "inactive":
        User.objects.filter(pk=manager.pk).update(is_active=False)
    elif defect == "role":
        User.objects.filter(pk=manager.pk).update(role="consultant")
    else:
        foreign = Workspace.objects.create(name="دیگر", slug="foreign", customer_type="consultant")
        User.objects.filter(pk=manager.pk).update(workspace=foreign)
    with pytest.raises(TopologyMutationError) as error:
        bootstrap_team_first_range(workspace_id=workspace.pk, manager_id=manager_id, range_name="رنج")
    assert error.value.errors == (code,)
    assert not Range.objects.exists()


def test_foreign_legacy_active_assignment_blocks_manager_reuse(setup):
    workspace, manager = setup
    foreign = Workspace.objects.create(name="دیگر", slug="foreign", customer_type="consultant")
    row = Range.objects.create(workspace=foreign, name="دیگر")
    Range.objects.filter(pk=row.pk).update(manager=manager)
    with pytest.raises(TopologyMutationError) as error:
        bootstrap(workspace, manager)
    assert error.value.errors == ("range_manager_multiple_active_ranges",)
    assert not Range.objects.filter(workspace=workspace).exists()


@pytest.mark.parametrize("invalid", ["duplicate", "blank", "scope"])
def test_existing_range_validation_preserved(setup, invalid):
    workspace, manager = setup
    if invalid == "duplicate":
        Range.objects.create(workspace=workspace, name="رنج", is_active=False)
    with pytest.raises(TopologyMutationError) as error:
        bootstrap_team_first_range(workspace_id=workspace.pk, manager_id=manager.pk,
                                  range_name="" if invalid == "blank" else "رنج",
                                  activity_scope="invalid" if invalid == "scope" else "all")
    assert error.value.errors == ("range_validation_failed",)
    assert not Range.objects.filter(is_active=True).exists()


def test_final_validation_rolls_back_rows_manager_and_callbacks(setup, django_capture_on_commit_callbacks):
    workspace, manager = setup
    old = Range.objects.create(workspace=workspace, name="قدیمی", is_active=False)
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(TopologyMutationError) as error:
            with topology_transaction(workspace_id=workspace.pk, user_ids=(manager.pk,), range_ids=(old.pk,)) as locked:
                Range.objects.create(workspace=locked.workspace, name="جدید")
                locked.ranges[0].manager = locked.users[0]
                locked.ranges[0].save()
                locked.users[0].is_active = False
                locked.users[0].save()  # Existing matching work must also roll back.
                transaction.on_commit(lambda: pytest.fail("rolled-back callback executed"))
    assert error.value.errors == ("active_range_missing_manager",)
    assert error.value.status == "invalid" and error.value.topology == "single_team"
    assert callbacks == []
    old.refresh_from_db()
    manager.refresh_from_db()
    assert old.manager_id is None and manager.is_active
    assert Range.objects.count() == 1


def test_bootstrap_rolls_back_when_final_validator_rejects(setup, monkeypatch):
    from apps.organizations import topology_services
    from apps.organizations.topology import TopologyValidationResult
    workspace, manager = setup
    def reject(workspace):
        assert Range.objects.filter(workspace=workspace, is_active=True).count() == 1
        return TopologyValidationResult("invalid", "team", "single_team", ("active_range_manager_inactive",))
    monkeypatch.setattr(topology_services, "validate_workspace_topology", reject)
    with pytest.raises(TopologyMutationError):
        bootstrap(workspace, manager)
    assert not Range.objects.exists()


def test_unresolved_final_state_rolls_back(setup):
    workspace, _ = setup
    with pytest.raises(TopologyMutationError) as error:
        with topology_transaction(workspace_id=workspace.pk) as locked:
            locked.workspace.workspace_mode = None
            locked.workspace.save()
    assert error.value.status == "unresolved"
    workspace.refresh_from_db()
    assert workspace.workspace_mode == "team"


def test_lock_order_and_current_persisted_state(setup):
    workspace, manager = setup
    other = User.objects.create_user(username="other", workspace=workspace, role="range_manager")
    rows = [Range.objects.create(workspace=workspace, name=str(i), is_active=False) for i in range(2)]
    with CaptureQueriesContext(connection) as queries:
        with topology_transaction(workspace_id=workspace.pk, user_ids=(other.pk, manager.pk),
                                  range_ids=tuple(row.pk for row in reversed(rows))) as locked:
            assert [user.pk for user in locked.users] == sorted([manager.pk, other.pk])
            assert [row.pk for row in locked.ranges] == sorted(row.pk for row in rows)
            Range.objects.create(workspace=locked.workspace, manager=manager, name="فعال")
    locks = [q["sql"] for q in queries if "FOR UPDATE" in q["sql"].upper()]
    assert len(locks) == 3
    assert "organizations_workspace" in locks[0]
    assert "accounts_user" in locks[1] and "ORDER BY" in locks[1]
    assert "ranges_range" in locks[2] and "ORDER BY" in locks[2]


def test_foreign_range_lock_rejected(setup):
    workspace, _ = setup
    foreign = Workspace.objects.create(name="دیگر", slug="foreign", customer_type="consultant")
    row = Range.objects.create(workspace=foreign, name="دیگر")
    with pytest.raises(TopologyMutationError) as error:
        with topology_transaction(workspace_id=workspace.pk, range_ids=(row.pk,)):
            pytest.fail("foreign row accepted")
    assert error.value.errors == ("range_missing_or_wrong_workspace",)


def test_identity_and_roles_unchanged(setup):
    workspace, manager = setup
    User.objects.filter(pk=manager.pk).update(is_workspace_owner=True, is_staff=True, is_superuser=True)
    bootstrap(workspace, manager)
    workspace.refresh_from_db()
    manager.refresh_from_db()
    assert workspace.customer_type == "consultant"
    assert manager.role == "range_manager" and manager.is_workspace_owner
    assert manager.is_staff and manager.is_superuser
    assert User.objects.count() == 1


def test_outer_rollback_undoes_bootstrap(setup):
    workspace, manager = setup
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            bootstrap(workspace, manager)
            raise RuntimeError("abort outer transaction")
    assert not Range.objects.exists()
