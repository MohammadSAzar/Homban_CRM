"""Trusted operational entry point. Archive reviewed input and emitted result."""
import json
from dataclasses import asdict
from pathlib import Path
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError

from apps.organizations.topology_repair import apply_repair_plan
from apps.organizations.topology_repair_plan import parse_repair_plan, RepairPlanError, validate_repair_plan
from apps.organizations.topology_services import TopologyMutationError


class Command(BaseCommand):
    help = "Validate or atomically apply one trusted reviewed Workspace repair plan."

    def add_arguments(self, parser):
        parser.add_argument("--plan", required=True, type=Path)
        modes = parser.add_mutually_exclusive_group(required=True)
        modes.add_argument("--dry-run", action="store_true")
        modes.add_argument("--apply", action="store_true")
        parser.add_argument("--confirm-workspace", type=UUID)
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        try:
            try:
                data = json.loads(options["plan"].read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError) as error:
                raise RepairPlanError("invalid_plan_file") from error
            plan = parse_repair_plan(data)
            if options["apply"]:
                if options["confirm_workspace"] is None:
                    raise RepairPlanError("repair_confirm_workspace_required")
                if str(options["confirm_workspace"]) != plan.workspace_id:
                    raise RepairPlanError("repair_confirm_workspace_mismatch")
                result = apply_repair_plan(plan)
                payload = {"status": "applied", **asdict(result)}
            else:
                result = validate_repair_plan(plan)
                payload = {"status": "dry_run", **asdict(result)}
                if not result.valid:
                    self._emit(payload, options["as_json"])
                    raise CommandError(", ".join(result.errors))
        except (RepairPlanError, TopologyMutationError) as error:
            self._emit({"status": "failed", "errors": error.errors}, options["as_json"])
            raise CommandError(", ".join(error.errors)) from error
        except CommandError:
            raise
        except Exception as error:
            # May include an unexpected on_commit failure. Never promise rollback
            # after commit or expose exception details/foreign identity data.
            self._emit({"status": "error", "errors": ("repair_execution_error",),
                        "commit_state": "unknown"}, options["as_json"])
            raise CommandError("repair_execution_error") from error
        # Output/archive errors here happen after success, outside apply handling.
        self._emit(payload, options["as_json"])

    def _emit(self, payload, as_json):
        if as_json:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            self.stdout.write(f"{payload['status']}: " + json.dumps(payload, ensure_ascii=False, sort_keys=True))
