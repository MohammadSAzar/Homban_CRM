# ADR 0004 — Explicit Workspace product mode

## Status
Accepted for Phase B1, additive foundation.

## Decision
Workspace remains the tenant isolation boundary. `Workspace.workspace_mode` is
the canonical product classification: `personal` (شخصی) or `team` (تیمی).
`customer_type` remains unchanged purchaser/commercial/provisioning metadata;
it is not runtime product mode and is not synchronized with it.

The expand migration adds a nullable field without a personal/team default and a
database constraint allowing only NULL, personal, or team. Existing rows remain
NULL. NULL means unresolved migration/provisioning state, never a third product
mode. No purchaser, ownership, user-role, active-manager, user-count, or Range-count
heuristic classifies a Workspace.

New normal application provisioning must call
`apps.organizations.services.create_workspace`, with explicit `workspace_mode` and
independent `customer_type`. It validates both and creates only the Workspace.
No user, Range, authority, or capability is created. Direct ORM creation remains
available for legacy fixtures and controlled migration/maintenance; it is not the
new application provisioning contract. There was no production provisioning path
or registered Workspace Django admin. Full provisioning remains future work.

No customer-facing mode-edit endpoint or admin registration is added. Mode
transition is not an ordinary customer setting. Future mode-aware authorization
must fail closed for unresolved modes; B1 does not wire mode into existing features.
Existing owner privileges and Chat's temporary manager-role policy remain unchanged
until their dedicated authority/capability phases.

## Subsequent clarification — B2a / B2b
B2a adds informational, read-only workspace_mode to GET `/api/v1/auth/me/`, sourced
from the current Workspace as personal/team/null. It changes no authentication,
permission or capability behavior. B2b aligns product documentation with the approved
target while preserving the B1 decision. Central Workspace Authority, topology
enforcement and capability migration remain deferred.

## Classification and later enforcement
Before final non-null enforcement: inventory deployed data read-only, approve an
explicit Workspace-to-mode mapping, handle every unresolved row, and verify all
provisioning paths supply mode. Do not convert missing classification into a default.
Do not rewrite history, assignments, or geography to satisfy classification.

For a small read-only inventory in an internal Django shell:

```python
from apps.organizations.models import Workspace

for row in Workspace.objects.order_by("pk").values(
    "id", "slug", "customer_type", "workspace_mode", "is_active",
).iterator(chunk_size=100):
    print(row)
```

Run only in an approved internal environment; this performs no classification or
mutation. No new inventory subsystem is introduced.

Permission migration, Workspace Authority, Team topology enforcement, and the
final non-null migration are separate later phases.
