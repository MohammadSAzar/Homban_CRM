# Homban Architecture

## Current stack
- Python 3.10.11
- Django 5.2 LTS line
- Django REST Framework
- MySQL
- pytest
- pytest-django
- Git
- PyCharm for local development

## Architectural style
API-first backend.

Frontend will be developed separately and consume REST APIs.

Avoid building a large server-rendered template architecture.

## Repository layout
Current intended shape:

```text
HombanCRM/
├── AGENTS.md
├── apps/
│   ├── accounts/
│   ├── organizations/
│   ├── locations/
│   ├── ranges/
│   ├── properties/
│   ├── customers/
│   ├── matching/
│   ├── tasks/
│   ├── chat/
│   └── notifications/
├── common/
├── config/
│   └── settings/
├── docs/
├── requirements/
├── manage.py
└── pytest.ini
```

Future apps may include:
- deals
- imports/integrations
- audit/logging

Do not create apps only because they are listed here. Create them when the domain boundary is ready.

`properties` contains the PropertyFile domain models and atomic aggregate-creation
service and operational REST endpoints. File-owned image references and valuable-reason
rows have no duplicated workspace field: their parent is the isolation boundary.
Business references use PROTECT to retain file history. See DOMAIN for fields,
validation limits, monetary units and the UUID-derived display code.

`customers` holds Customer, customer-specific valuable reasons and an explicit Region
preference through model. Model and M2M guards enforce cross-table integrity; internal
transactional services follow the existing Workspace lock order. Customer REST APIs
and Matching are implemented; their contracts are described in DOMAIN.

## Settings
Settings are split into:
- `base.py`
- `development.py`
- `production.py`

Secrets belong in environment variables.

## Workspace isolation
Homban uses one codebase with multiple customer workspaces.

Phase B1 adds explicit `Workspace.workspace_mode` (`personal` / `team`) while
preserving purchaser `customer_type` metadata. The expand migration allows NULL
only for unresolved classification, with no product-mode default or heuristic
backfill. New application creation uses the small organization-layer
`create_workspace` service and requires an explicit mode. No customer mode-edit
API is added. Existing runtime permissions remain unchanged; authority consumer migration,
capabilities, topology enforcement, and final non-null enforcement are deferred.
See [ADR 0004](decisions/0004-workspace-product-mode.md).

B2a exposes workspace_mode read-only in GET `/api/v1/auth/me/` as personal/team/null,
from the current Workspace row. No fallback or authorization behavior is attached.

Every workspace-owned business entity must be isolated by workspace.

The backend must never rely on frontend filtering for tenant isolation.

Typical query flow:
1. Determine authenticated user's workspace
2. Scope queryset to workspace
3. Apply role/organizational scope
4. Apply object-level restrictions
5. Apply field-level serialization restrictions

## Planned authority and capability layers
The C1 foundation now exists in `apps.organizations.authority`: read-only
`resolve_workspace_authority(workspace)` returns a frozen result with status
(`resolved`/`none`/`invalid`), authority_user, topology and an internal reason code.
It reloads Workspace mode and considers active same-Workspace Ranges only. Personal
returns none even with legacy teams; NULL fails closed. Team requires valid active
same-Workspace Range Managers for every operational Range. One Range resolves its
manager regardless of Agency Manager presence; multiple Ranges require exactly one
active same-Workspace Agency Manager. Zero active Ranges or malformed topology is invalid.
Repeated Range managers remain schema-permitted; no topology is repaired or enforced.
The resolver introduces no purchaser/owner/staff fallback, writes or caching.
This resolver is not endpoint authorization or a Workspace activation check. Consumers
must retain access checks and use their Workspace-locked transaction when needed.

Keep tenant isolation, purchaser metadata, product mode and operational authority separate.
Personal is one consultant with Core capabilities. Team-only and topology-dependent
features are classified in PRODUCT; their central backend capability layer is not built.
Target Team has one or more Ranges: single-team authority is the Range Manager;
multi-team authority is an Agency Manager above managed teams. Current schema permits
legacy states and does not enforce these invariants.

C2 migrates Chat announcement default publishing, delegation administration and General
Group settings to the C1 resolver. Personal/NULL/invalid Team have no Workspace Chat
Authority; valid single-team uses its Range Manager, valid multi-team its unique Agency
Manager. Explicit eligible delegation remains publishing-only. Chat ordinary availability
still uses a separate temporary manager-presence check, not workspace_mode gating.
Private participant privacy, ordinary read/send/realtime eligibility and protocols remain
unchanged. No capability layer or structural topology enforcement is added.
Other domains retain role/scope rules and some owner privileges. Target owner identity
never grants operational authority; future authority cannot override private object
boundaries documented in PERMISSIONS.

Migration remains incremental: B1 mode foundation and B2a read-only context are complete;
B2b aligns documentation. C1 adds the resolver; C2 migrates Chat and C3 Location authority consumers.
Next come remaining authority consumer migration,
topology invariants
(D), and capabilities (E), followed by focused feature refactors (F): Chat, Collaboration,
Matching scopes, Daily Tasks projection, User/Range management, Notification producers,
File/Customer scopes, then remaining admin-facing permissions.
Review data/backward compatibility before final architecture verification. Explicit legacy
classification and verified provisioning must precede final NOT NULL enforcement.
No big-bang permission rewrite or heuristic mode inference is part of this plan.

## Company control plane
Customer credential lookup is scoped by workspace. A reusable resolver in
`apps/accounts/workspace_context.py` uses an exact configured host-to-slug map,
with an explicit development-only `X-Workspace-Slug` fallback. Missing or conflicting
context fails closed. No body-supplied workspace ID selects the tenant.

The database enforces `(workspace, username)` uniqueness. UUID remains the Django
internal/JWT identity; authorization uses current database state. Workspace-less
staff use UUID-based internal authentication and never enter customer auth.
See [ADR 0003](decisions/0003-workspace-scoped-username.md) for configuration,
nullable-uniqueness semantics, admin compatibility, and future host-routing limits.

A future company-level control plane may manage:
- Customer provisioning
- Subscription/service status
- Support
- Operational observability
- Legal/privacy-aware analytics

Do not mix control-plane privileges with ordinary workspace roles.

Company support access to customer data must eventually be:
- Explicit
- Auditable
- Least-privilege
- Privacy/legal-aware

## Database
MySQL with `utf8mb4`.

Prefer:
- Explicit indexes for common filters
- Uniqueness constraints scoped to workspace when appropriate
- UUID primary keys where already established
- Foreign keys with business-appropriate deletion behavior

Be careful with `PROTECT`, `CASCADE`, and `SET_NULL`.
Deletion behavior is business logic.

## Services
Business logic that spans models should generally live in service modules rather than serializers/views/signals.

Good examples:
- Create consultant in range
- Register deal and transition statuses
- Calculate match score
- Approve imported external file

Signals should be used only for truly implicit side effects that remain safe and testable.

## Integrations
External systems must use dedicated clients/adapters.

Examples:
- Divar
- Amlak Plus
- Kashano
- Peyvand

Do not scatter raw HTTP calls across views/tasks.

Future scheduled ingestion likely uses Celery or a similar task runner, but implementation is deferred.

## Chat
Real-time chat likely requires ASGI and a real-time layer (for example Django Channels/WebSocket architecture), but exact technology is not finalized.

## Frontend
Not yet selected.

Mandatory frontend properties:
- Persian
- 100% RTL
- Mobile-first
- Fast and low-friction
- Full desktop functionality
- Minimal typing

Backend APIs must remain frontend-agnostic.

### Phase C3 — Location Workspace Authority
Workspace-wide City/Region create, update and activation changes, and region_mode
configuration now use the C1 resolver. A valid single active Range's manager is
authority; valid multi-team topology requires exactly one active Agency Manager.
Personal, NULL and invalid Team topology have no Location administration authority.
Owner/customer_type/staff/superuser flags grant none. Existing authentication,
Workspace isolation and Workspace-first write locking remain required.
Ordinary active geography reads remain available to customer members; only resolved
authority can inspect inactive taxonomy. Personal setup/admin UX remains an explicit
later product decision. Geography semantics, Range.regions, capability gating and
structural topology enforcement are unchanged; no schema migration is added.

### Phase C4 — User and Range authority consumption
Workspace-global user lists/detail, supported user creation/profile/activation and
consultant assignment, Range structure/Region constraints and global membership
moves now use C1 resolution. Single-team authority is its assigned Range Manager;
valid multi-team authority is the unique Agency Manager. Personal, NULL and invalid
topology grant no global authority. No owner/customer_type/staff/superuser shortcut
remains. Owner is still a protected lifecycle target, not an administrator.
Non-authority Range Managers retain self/local consultant visibility and existing
exactly-one-active-Range consultant creation/update/add/remove rules. NULL or invalid
global topology does not erase valid local scope. Personal legacy managers receive
no organizational administration surface. Consultants retain membership-scoped
Range reads; owner flags confer no global Range/roster visibility.
Self, current authority, purchaser targets and Agency Managers remain protected
from ordinary user lifecycle edits. Agency Manager protection is deliberately
retained pending explicit lifecycle design, even when not current authority.
Existing structural edits may invalidate authority (manager removal, deactivation,
or a second Range without a unique Agency Manager). The authorized write returns
its saved result; subsequent requests resolve current state and fail closed. No
auto-repair/succession or topology enforcement is added. Creating an active
managerless Range can therefore require a later controlled repair workflow; ordinary
global APIs cannot bootstrap/repair an already invalid topology.
No capability layer, schema migration or Personal setup workflow is introduced.
Other domains and canonical geography remain unchanged.
