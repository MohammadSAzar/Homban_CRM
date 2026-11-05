import json
from dataclasses import asdict
from uuid import UUID

from django.core.management.base import BaseCommand
from apps.organizations.topology_inventory import inventory_workspaces


class Command(BaseCommand):
    help = "Read-only internal Workspace topology inventory; never applies repairs."

    def add_arguments(self, parser):
        parser.add_argument("--workspace", action="append", type=UUID, dest="workspace_ids")
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument("--invalid-only", action="store_true")

    def handle(self, *args, **options):
        rows = [row for row in inventory_workspaces(options["workspace_ids"])
                if not options["invalid_only"] or row.validation_status != "valid"]
        if options["as_json"]:
            self.stdout.write(json.dumps([asdict(row) for row in rows], ensure_ascii=False, sort_keys=True))
        else:
            for row in rows:
                self.stdout.write(f"{row.workspace_id} {row.validation_status} {row.topology} "
                                  f"active={row.active_range_count} issues={','.join(row.issue_classes) or '-'}")
            self.stdout.write(f"Workspaces: {len(rows)}")
