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
D2d-2 now constrains topology-changing structural edits to valid transitions;
active manager removal and implicit single-to-multi expansion are rejected.
An authorized transition returns its saved result even after authority handoff;
subsequent requests resolve current authority. Ordinary global APIs cannot
bootstrap or repair invalid legacy topology. No automatic succession is added.
No capability layer, schema migration or Personal setup workflow is introduced.
Other domains and canonical geography remain unchanged.

### Phase D2a — Read-only target topology validation
`apps.organizations.topology.validate_workspace_topology` returns an immutable
status/mode/topology/errors result with stable diagnostic codes. Personal permits
no active Ranges; NULL mode remains unresolved. Team requires valid active
same-Workspace Range Managers, at most one active Range per manager, and exactly
one active Agency Manager only for multi-team topology. Inactive assignments are
excluded. C1 authority resolution remains unchanged, including its existing
interpretation of repeated managers. D2b/D2c subsequently added internal mutation
consumers; D2d-2 now integrates ordinary structural writes. Provisioning and legacy
repair remain deferred.

### Phase D2b — Internal topology transaction foundation
`organizations.topology_services.topology_transaction` locks Workspace first,
then declared same-Workspace Users and Ranges in primary-key order. Trusted callers
must authorize separately; this internal boundary grants no customer permissions.
Final persisted state must pass D2a validation before transaction exit, otherwise
all enclosed writes and on-commit callbacks roll back. Nested use awaits the outer
transaction for durability. `bootstrap_team_first_range` creates only the first
active Range for an existing active Team and existing eligible Range Manager;
existing active Ranges are rejected. C1 and D2a remain unchanged. D2c added
transitions/replacement and D2d-2 adds customer mutation enforcement; automatic
repair and complete onboarding remain deferred. No schema migration is added.

### Phase D2c — Explicit internal atomic topology transitions
`organizations.topology_transitions` adds single-to-multi and multi-to-single
transitions, active Range Manager replacement, and multi-team Agency Manager
replacement. Each requires a valid active Team starting state and uses D2b's
Workspace-first boundary with deterministically locked current Users/Ranges,
canonical D2a final validation and C1 authority verification. C1/D2a are unchanged.
Single-to-multi uses existing active Agency/Range Managers; multi-to-single retains
inactive Range history and the former Agency account, handing authority to the
kept Range Manager. Range replacement leaves the outgoing user active. Agency
replacement activates an explicitly selected existing inactive Agency Manager and
deactivates the outgoing one atomically, preserving roles and owner flags.
Outgoing owner Agency replacement is rejected (`outgoing_agency_manager_is_owner`)
pending explicit owner lifecycle design; incoming legacy owner status is allowed.
D2c itself adds no ordinary API enforcement; D2d-2 supplies the customer integration.
Complete onboarding, generic repair and automatic role conversion/user creation
remain absent. No schema migration is introduced.

### Phase D2c.1 — Multi-team expansion and prepared Range activation
Internal `add_active_range_to_multi_team` adds one eligible managed active Range
to valid multi-team topology while preserving its Agency authority.
`activate_prepared_range` activates an existing inactive Range without changing
its manager, memberships or Regions. Single-to-multi activation requires explicit
existing active Agency designation; multi-team activation preserves current Agency
authority and rejects a conflicting designation. Both use D2b locking and final
D2a validation with C1 verification. Manager discovery includes the requested
inactive Range before User/Range locks. Zero-to-one prepared restoration remains
deferred to repair design. D2d-2 subsequently wires ordinary multi-team expansion
and activation. No onboarding, automatic role conversion/user creation or schema
migration is introduced.

### Phase D2d-2 — Customer User/Range topology enforcement
Topology-changing Range writes now use D2b/D2c/D2c.1 with canonical D2a validation
and C1 authority. Workspace is locked first; the current actor is reloaded and
authorized under that lock, before D2b locks relevant Users by PK then Ranges by
PK. No early actor User lock is added: cooperating management writes serialize on
the same Workspace, and D2b includes current authority among its ordered Users.

Ordinary active Range creation and prepared activation require valid multi-team
topology and preserve Agency authority. Zero-to-one bootstrap and implicit
single-to-multi expansion are rejected. Active manager replacement delegates to
D2c; unchanged manager is a no-op, and clearing an active manager is rejected.
Last active Range deactivation is rejected; two-to-one delegates to D2c and hands
authority to the remaining manager; N>=3 deactivation validates the final state
and retains Agency authority. Inactive history, memberships and Regions remain.
Each request permits at most one topology-changing intent. Neutral name,
activity_scope and Region changes may accompany it atomically; any failure rolls
back the transition and its on-commit callbacks.

Explicit Workspace-Authority-only workflows:

- `POST /api/v1/ranges/expand/`: `agency_manager_id`, `new_range_manager_id`,
  `new_range_name`, optional `activity_scope` and `regions`; returns the created
  Range (201) after an atomic single-to-multi handoff.
- `POST /api/v1/ranges/replace-agency-manager/`: `incoming_agency_manager_id`;
  activates an existing inactive Agency Manager and deactivates the outgoing
  authority atomically (204). Outgoing owner Agency replacement remains blocked;
  owner flags and roles are never transferred or changed.

User activity changes are rejected for any target managing an active Range,
including reactivation of an invalid legacy assignment. Ordinary Agency lifecycle
PATCH remains protected. User creation cannot fill an active managerless Range;
unassigned manager candidates and existing supported non-manager roles remain
creatable. Profile changes, consultant membership, local Range Manager scope and
inactive Range preparation retain their existing policies without blanket global
D2a validation. Invalid/NULL global topology does not erase valid local scope.
Owner/customer_type/staff/superuser confer no authority. Foreign/missing resources
remain non-disclosing and internal topology codes map to Persian API validation.

C1/D2a semantics and D2b/D2c/D2c.1 primitives are unchanged. Explicit prepared
single-to-multi activation remains internal; its customer contract is deferred.
Legacy repair, complete onboarding and owner Agency lifecycle remain deferred.
No generic repair, automatic successor/role conversion, capability gating or schema
migration is introduced.

### Phase D2e.1 — Read-only legacy inventory and reviewed plans
`topology_inventory.inventory_workspace(id)` and `inventory_workspaces(ids=None)`
combine canonical D2a validity and C1 authority with deterministic, contact-free
context, historical warnings and a SHA-256 state token. Query count is bounded per
Workspace (not per Range); all-Workspace inventory streams IDs and reuses canonical
resolvers. `manage.py topology_inventory` defaults to all Workspaces; repeated
`--workspace UUID`, `--json` and `--invalid-only` are supported. Invalid/unresolved
rows are inventory data, not a command failure. Owner Agency warnings identify a
potential lifecycle blocker, not a D2a validity failure.

`topology_repair_plan.parse_repair_plan` accepts strict version-1 JSON-shaped data:
workspace_id, expected_state_token (from inventory), expected_workspace_mode,
expected_d2a_status, expected_d2a_errors (sorted unique list), operations, reviewer
and reason. Reviewer/reason are required; operations have explicit allowlisted IDs
and values. Bootstrap specifies manager_id/range_name/activity_scope; active manager
assignment/replacement specifies range_id/manager_id; deactivation specifies range_id;
Agency activity specifies user_id; inactive-link repair specifies range_id and an
explicit manager_id (or null to clear); classification explicitly specifies mode.

`validate_repair_plan` performs read-only preflight: stale-state comparison, local
reference/eligibility checks and outgoing owner Agency deactivation blocking.
The token includes mode/activity, all Range manager assignments/eligibility and local
user role/activity/owner state; missing/foreign candidates share one internal error.
Foreign legacy FK replacement/clearing targets only the local Range. Inactive null,
inactive or repeated managers are tolerated; foreign/wrong-role history is warned.

A valid dry-run means the starting state and plan references pass preflight, NOT
that the proposed final topology was simulated or certified. Results explicitly
warn `final_topology_not_simulated`. No locks/snapshot guarantee are provided: D2e.2
must recheck under Workspace lock and validate final D2a state before commit.
NULL classification remains explicit/manual with no purchaser/role/count inference.
No repair application, customer API, admin action or schema migration exists in
D2e.1. D2e.2 application is described below; owner Agency lifecycle remains deferred.


### Phase D2e.2 — Trusted reviewed repair application
`topology_repair.apply_repair_plan` applies one strictly parsed, reviewed RepairPlan
inside its own outermost transaction; nested callers are rejected. Repair is an
internal operational boundary, never customer authorization through purchaser,
owner, staff or superuser flags. No customer API or admin action is provided.

Workspace is locked before inventory/preflight reads. The existing D2b boundary
then locks explicit local Users by PK followed by local Ranges by PK; foreign
legacy managers are never locked or modified. Stale tokens/expectations and plan
preconditions are rechecked before and after subordinate locks. Operations run in
canonical order: mode, active manager assignment/replacement, inactive-link cleanup,
Range deactivation, Agency deactivation/activation, then first-Range bootstrap.
File order cannot change execution semantics. Bootstrap shares D2b's already-locked
helper; its public contract is unchanged. Final persisted D2a validity and C1
consistency are mandatory; failure rolls back the entire repair.

Malformed foreign/wrong-role manager links may be explicitly retained on a
Range being deactivated. Only this repair case bypasses Range.full_clean through
a scoped update of is_active/updated_at, checking one affected row and refreshing.
Other Range writes use existing validation. History, memberships and Regions are
preserved. Agency activity uses normal User tracking; transactional Matching work
and callbacks roll back with repair. Callbacks publish after outer commit; a later
delivery failure cannot undo committed data. No events are globally suppressed.

`manage.py topology_repair --plan PATH --dry-run [--json]` performs read-only D2e.1
preflight (not final-state simulation). Application requires `--apply` instead of
`--dry-run` plus `--confirm-workspace UUID` matching the plan. Stale/blocked/failed
requests exit non-zero. Immutable results include reviewer/reason, ordered operations,
created Range ID, before/after tokens, completion time, final D2a/C1 state and warnings.
Archive the reviewed plan and JSON result externally; no DB audit model is added.
Unexpected execution errors report unknown commit state rather than falsely claiming
rollback; output/archive errors after success likewise require operational review.

Every successful repair deliberately advances Workspace.updated_at so replay of
the same plan is stale, including logical no-ops. NULL mode classification remains
explicit/manual. Owner Agency deactivation remains blocked with
owner_agency_lifecycle_unresolved. No role conversion, successor selection, owner
transfer, user creation, onboarding, prepared zero-to-one restoration, or schema
migration is introduced. Optional inactive-history cleanup is not required for D2a
validity. D2f concurrent-connection verification remains deferred.
