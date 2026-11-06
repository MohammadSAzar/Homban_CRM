"""Real MySQL connections; events force overlapping Workspace lock attempts."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from django.db import connection, connections
from rest_framework.exceptions import APIException

from apps.accounts.models import User
from apps.accounts.services import update_organizational_user
from apps.matching.models import RecommendationWork
from apps.organizations.authority import resolve_workspace_authority
from apps.organizations.topology import validate_workspace_topology
from apps.organizations.topology_repair import apply_repair_plan
from apps.organizations.topology_services import TopologyMutationError
from apps.organizations import topology_transitions as transitions
from apps.ranges.models import Range
from apps.ranges.services import save_range
from .test_topology import setup
from .test_topology_repair_plan import document

pytestmark = pytest.mark.django_db(transaction=True)


def overlap(first, second):
    """Pause the first writer holding its lock until the second attempts it.

    SQL interception only schedules overlap; service transactions/locks are real.
    No transaction wrapper is added (repair must own its outermost transaction).
    Both worker connections are closed even on failure. Database exceptions are
    deliberately NOT accepted as safe losers: deadlocks/timeouts fail the test.
    """
    assert connection.vendor == "mysql", "Phase D closure requires real MySQL"
    acquired, attempted = Event(), Event()

    def worker(index, operation):
        db = connections["default"]
        seen = False
        def schedule(execute, sql, params, many, context):
            nonlocal seen
            if not seen and "FOR UPDATE" in sql.upper() and "organizations_workspace" in sql:
                seen = True
                if index == 0:
                    result = execute(sql, params, many, context)
                    acquired.set()
                    assert attempted.wait(10), "second writer never attempted Workspace lock"
                    return result
                assert acquired.wait(10), "first writer never acquired Workspace lock"
                attempted.set()
            return execute(sql, params, many, context)
        try:
            db.ensure_connection()
            with db.cursor() as cursor:
                cursor.execute("SET SESSION innodb_lock_wait_timeout = 10")
                cursor.execute("SELECT CONNECTION_ID()")
                connection_id = cursor.fetchone()[0]
            with db.execute_wrapper(schedule):
                try:
                    value = operation()
                    outcome = (True, value)
                except (TopologyMutationError, APIException) as error:
                    outcome = (False, error)
            assert seen, "writer did not enter Workspace serialization"
            return connection_id, outcome
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, i, fn) for i, fn in enumerate((first, second))]
        results = [future.result(timeout=30) for future in futures]
    assert results[0][0] != results[1][0]
    assert acquired.is_set() and attempted.is_set()
    return [result[1] for result in results]


def valid(workspace, authority=None):
    result = validate_workspace_topology(workspace)
    assert result.status == "valid", result.errors
    resolved = resolve_workspace_authority(workspace)
    assert resolved.status == "resolved"
    if authority is not None:
        assert resolved.authority_user.pk == authority.pk
    managers = list(Range.objects.filter(workspace=workspace, is_active=True).values_list("manager_id", flat=True))
    assert None not in managers and len(managers) == len(set(managers))
    return resolved.authority_user


def multi(setup):
    workspace, user, team = setup
    first = team(user()); team(user())
    agency = user("agency_manager")
    return workspace, user, team, first, agency


def test_single_to_multi(setup):
    workspace, user, team = setup
    team(user()); agency = user("agency_manager")
    candidates = [user(), user()]
    def expand(i):
        return lambda: transitions.transition_single_to_multi(
            workspace_id=workspace.pk, agency_manager_id=agency.pk,
            new_range_manager_id=candidates[i].pk, new_range_name=f"expansion-{i}")
    outcomes = overlap(expand(0), expand(1))
    assert [ok for ok, _ in outcomes] == [True, False]
    assert Range.objects.filter(workspace=workspace).count() == 2
    valid(workspace, agency)


def test_multi_additions(setup):
    workspace, user, _, _, agency = multi(setup)
    candidates = [user(), user()]
    def add(i):
        return lambda: transitions.add_active_range_to_multi_team(
            workspace_id=workspace.pk, manager_id=candidates[i].pk, range_name=f"addition-{i}")
    assert all(ok for ok, _ in overlap(add(0), add(1)))
    assert Range.objects.filter(workspace=workspace, is_active=True).count() == 4
    valid(workspace, agency)


def test_range_replacements(setup):
    workspace, user, team = setup
    outgoing = user(); row = team(outgoing)
    candidates = [user(), user()]
    def replace(i):
        return lambda: transitions.replace_range_manager(
            workspace_id=workspace.pk, range_id=row.pk, incoming_manager_id=candidates[i].pk)
    assert all(ok for ok, _ in overlap(replace(0), replace(1)))
    row.refresh_from_db()
    assert row.manager_id == candidates[1].pk
    assert User.objects.filter(workspace=workspace, role="range_manager", is_active=True).count() == 3
    valid(workspace, candidates[1])


def test_agency_replacements(setup, isolated_matching_broker):
    workspace, user, _, _, _ = multi(setup)
    candidates = [user("agency_manager", is_active=False), user("agency_manager", is_active=False)]
    def replace(i):
        return lambda: transitions.replace_multi_team_agency_manager(
            workspace_id=workspace.pk, incoming_agency_manager_id=candidates[i].pk)
    assert all(ok for ok, _ in overlap(replace(0), replace(1)))
    assert User.objects.filter(workspace=workspace, role="agency_manager", is_active=True).count() == 1
    assert User.objects.filter(workspace=workspace, role="agency_manager").count() == 3
    valid(workspace, candidates[1])
    assert isolated_matching_broker.call_count == 4


@pytest.mark.parametrize("replace_first", [False, True])
def test_user_deactivation_vs_public_range_replacement(setup, replace_first):
    workspace, user, _, row, agency = multi(setup)
    outgoing = row.manager_id; incoming = user()
    def deactivate():
        return update_organizational_user(actor=User.objects.get(pk=agency.pk), user_id=outgoing,
                                          data={"is_active": False})
    def replace():
        return save_range(actor=User.objects.get(pk=agency.pk), range_id=row.pk, data={"manager": incoming.pk})
    outcomes = overlap(replace, deactivate) if replace_first else overlap(deactivate, replace)
    assert [ok for ok, _ in outcomes] == ([True, True] if replace_first else [False, True])
    row.refresh_from_db()
    assert row.manager_id == incoming.pk
    assert User.objects.get(pk=outgoing).is_active is (not replace_first)
    valid(workspace, agency)


def test_prepared_activation(setup):
    workspace, user, team, _, agency = multi(setup)
    row = team(user(), is_active=False)
    def activate():
        return transitions.activate_prepared_range(workspace_id=workspace.pk, range_id=row.pk)
    assert [ok for ok, _ in overlap(activate, activate)] == [True, False]
    assert Range.objects.filter(workspace=workspace).count() == 3
    assert Range.objects.filter(workspace=workspace, is_active=True).count() == 3
    valid(workspace, agency)


@pytest.mark.parametrize("repair_first", [False, True])
def test_repair_vs_public_structural_write(setup, repair_first):
    workspace, user, team = setup
    team(user()); broken = team(); manager = user(); candidate = user()
    agency = user("agency_manager")
    plan = document(workspace, [{"type": "assign_active_range_manager", "range_id": str(broken.pk),
                                 "manager_id": str(manager.pk)}])
    def repair():
        return apply_repair_plan(plan)
    def create():
        return save_range(actor=User.objects.get(pk=agency.pk),
                          data={"name": "customer addition", "manager": candidate.pk})
    outcomes = overlap(repair, create) if repair_first else overlap(create, repair)
    assert [ok for ok, _ in outcomes] == ([True, True] if repair_first else [False, True])
    assert Range.objects.filter(workspace=workspace).count() == (3 if repair_first else 2)
    broken.refresh_from_db()
    assert broken.manager_id == manager.pk
    valid(workspace, agency)


@pytest.mark.parametrize("kind", ["bootstrap", "agency"])
def test_same_reviewed_repair_applies_once(setup, kind, isolated_matching_broker):
    workspace, user, team = setup
    if kind == "bootstrap":
        authority = user()
        operation = dict(type="bootstrap_first_range", manager_id=str(authority.pk),
                         range_name="first", activity_scope="all")
    else:
        team(user()); team(user())
        authority = user("agency_manager", is_active=False)
        operation = dict(type="activate_existing_agency_manager", user_id=str(authority.pk))
    plan = document(workspace, [operation])
    before_work = RecommendationWork.objects.count()
    def repair():
        return apply_repair_plan(plan)
    outcomes = overlap(repair, repair)
    assert [ok for ok, _ in outcomes] == [True, False]
    assert outcomes[1][1].errors == ("repair_plan_stale",)
    assert Range.objects.filter(workspace=workspace).count() == (1 if kind == "bootstrap" else 2)
    assert RecommendationWork.objects.count() - before_work == (0 if kind == "bootstrap" else 1)
    assert isolated_matching_broker.call_count == (0 if kind == "bootstrap" else 1)
    valid(workspace, authority)
