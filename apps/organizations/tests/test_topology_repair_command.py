import json
from io import StringIO
from uuid import uuid4

import pytest
from django.core.management import call_command, CommandError
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.organizations.topology_inventory import inventory_workspace
from .test_topology import setup
from .test_topology_repair_plan import document

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def plan_file(setup, tmp_path):
    workspace, _, _ = setup
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(document(workspace, [
        {"type": "set_workspace_mode", "workspace_mode": "personal"},
    ])), encoding="utf-8")
    return path


def run(path, *args):
    output = StringIO()
    call_command("topology_repair", "--plan", str(path), *args, stdout=output)
    return output.getvalue()


def test_dry_run_no_writes(setup, plan_file):
    before = inventory_workspace(setup[0].pk).state_token
    with CaptureQueriesContext(connection) as queries:
        result = json.loads(run(plan_file, "--dry-run", "--json"))
    assert result["valid"] and result["status"] == "dry_run"
    assert "final_topology_not_simulated" in result["warnings"]
    assert all(q["sql"].upper().startswith("SELECT") for q in queries)
    assert inventory_workspace(setup[0].pk).state_token == before


@pytest.mark.parametrize("flags,code", [
    (["--apply"], "repair_confirm_workspace_required"),
    (["--apply", "--confirm-workspace", str(uuid4())], "repair_confirm_workspace_mismatch"),
])
def test_confirmation_required(setup, plan_file, flags, code):
    before = inventory_workspace(setup[0].pk).state_token
    output = StringIO()
    with pytest.raises(CommandError, match=code) as error:
        call_command("topology_repair", "--plan", str(plan_file), *flags, "--json", stdout=output)
    assert error.value.returncode != 0
    assert json.loads(output.getvalue())["errors"] == [code]
    assert inventory_workspace(setup[0].pk).state_token == before


def test_apply_json_and_stale_exit(setup, plan_file):
    workspace = setup[0]
    args = ("--apply", "--confirm-workspace", str(workspace.pk), "--json")
    result = json.loads(run(plan_file, *args))
    assert result["status"] == "applied" and result["final_topology"] == "personal"
    assert result["reviewer"] and result["reason"] and result["completed_at"]
    assert result["before_state_token"] != result["after_state_token"]
    output = StringIO()
    with pytest.raises(CommandError, match="repair_plan_stale") as error:
        call_command("topology_repair", "--plan", str(plan_file), *args, stdout=output)
    assert error.value.returncode != 0
    assert json.loads(output.getvalue())["errors"] == ["repair_plan_stale"]


@pytest.mark.parametrize("bad", ["{", "{}", "[]"])
def test_bad_file_nonzero(plan_file, bad):
    plan_file.write_text(bad, encoding="utf-8")
    output = StringIO()
    with pytest.raises(CommandError) as error:
        call_command("topology_repair", "--plan", str(plan_file), "--dry-run", "--json", stdout=output)
    assert error.value.returncode != 0
    assert json.loads(output.getvalue())["status"] == "failed"


@pytest.mark.parametrize("flags", [[], ["--apply", "--dry-run"]])
def test_exactly_one_mode(plan_file, flags):
    with pytest.raises(CommandError):
        run(plan_file, *flags)


def test_blocked_dry_run_json(plan_file):
    data = json.loads(plan_file.read_text(encoding="utf-8"))
    data["expected_state_token"] = "0" * 64
    plan_file.write_text(json.dumps(data), encoding="utf-8")
    output = StringIO()
    with pytest.raises(CommandError, match="repair_plan_stale"):
        call_command("topology_repair", "--plan", str(plan_file), "--dry-run", "--json", stdout=output)
    payload = json.loads(output.getvalue())
    assert payload["stale"] and not payload["valid"]


def test_human_output(plan_file):
    assert run(plan_file, "--dry-run").startswith("dry_run:")


def test_unexpected_execution_error_is_safe_json(setup, plan_file, monkeypatch):
    from apps.organizations.management.commands import topology_repair as command
    def fail(plan):
        raise RuntimeError("must not expose internal details")
    monkeypatch.setattr(command, "apply_repair_plan", fail)
    output = StringIO()
    with pytest.raises(CommandError, match="repair_execution_error"):
        call_command("topology_repair", "--plan", str(plan_file), "--apply", "--confirm-workspace",
                     str(setup[0].pk), "--json", stdout=output)
    result = json.loads(output.getvalue())
    assert result["commit_state"] == "unknown"
    assert "must not expose" not in output.getvalue()
