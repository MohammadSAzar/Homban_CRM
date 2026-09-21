# Homban Domain Model

## Core aggregate: Workspace
A `Workspace` is the isolation boundary for one Homban customer installation.

A workspace may represent:
- A whole agency
- A range/team bought independently
- An individual consultant

The business-facing experience adapts to the buyer type, but the core platform remains shared.

## User roles
Customer usernames are unique within their Workspace. The same username may exist
in other workspaces; the globally unique identity is `User.id` (UUID).
Workspace-less internal users retain nullable membership and may share display
usernames, but cannot authenticate as customers. Their internal identity is UUID.

Current roles:
- `agency_manager` — مدیر املاک
- `range_manager` — مدیر رنج
- `consultant` — مشاور
- `secretary` — منشی
- `admin` — ادمین

The workspace purchaser/primary user is not a role.
Use a separate concept such as `is_workspace_owner`.

## Organization
A workspace may have zero or more ranges.

A range:
- Belongs to one workspace
- Has an optional current range manager
- Has consultants
- May be limited to one or more regions
- May be limited by activity scope such as sale/rent
- Has no restriction if scopes are empty/default

Consultants can exist without a range.

### Organizational user management
The customer API supports user creation, scoped list/detail, profile updates, and
activation/deactivation. It does not hard-delete users or create ranges implicitly.
Role and username are immutable after creation through this API. Workspace,
ownership, Django staff/superuser flags, groups, and permissions cannot be supplied
or changed. Passwords are validated and hashed on creation; password resets are
outside this feature.

Agency managers may create range managers, consultants, secretaries, and workspace
admins. Consultants may be unassigned or receive an active same-workspace range
through `RangeMembership`. A newly created range manager may optionally take an
active same-workspace range with no existing manager through `Range.manager`.
An occupied range is rejected rather than silently replacing its manager.

Range managers may create consultants only. Exactly one active, same-workspace
managed range is required, and membership is assigned automatically. Any supplied
`range_id` is rejected, including their own range or null. No/multiple valid active
ranges fail with a Persian validation error. Inactive ranges are not candidates.

Through the User Management API, only agency managers can update a consultant's
range assignment, including clearing membership with `range_id: null`. That API
does not update a range manager's managed-range assignment. The Range Management
API below provides explicit structural/membership operations. Secretary/admin users
do not receive consultant membership.

Deactivation only sets `User.is_active=False`. It preserves UUID, username, ownership,
range membership, managed ranges, and other users' state. Reactivation is explicit.
It does not cascade deactivation to a manager's consultants. Existing authentication
checks deny inactive users on login, access, and refresh. Deactivation does not
blacklist tokens: a still-unexpired token may work again after reactivation.

Agency managers (including self) and workspace owners are read-only targets in this
API. Range managers can view themselves but only manage in-scope consultants.
Sensitive agency-manager/owner lifecycle operations require a future explicit
administrative workflow, as confirmed by the product owner for this feature.

### Range Management API
Agency managers manage Range structure in their workspace: name, optional manager,
activity_scope (`all`, `sale`, `rent`), Region constraints, and active status.
Workspace owners of other operational roles may read the workspace structure but
do not gain structural management powers. Range managers read Ranges assigned to
them; consultants read only their own Range. Non-owner secretary/admin users have
no access. Inactive assigned Ranges remain readable in the same scope.

Managers are existing same-workspace users with role `range_manager`. Assignment,
replacement, and removal are explicit; neither users nor Regions are created
implicitly. Existing model behavior permits inactive managers to remain/be assigned
without activating their account. Assignment does not modify the user's profile,
role, ownership, or authentication state.

Manager multiplicity remains unresolved: Range.manager is a nullable ForeignKey,
so one user may manage multiple Ranges. This API preserves that schema and permits
such assignments, with no uniqueness migration. Range managers can read their
assigned Ranges, but membership writes retain User Management's requirement of
exactly one active same-workspace managed Range; ambiguous states fail safely.
Changing this cardinality requires a future explicit product decision.

Agency managers add/remove consultants and explicitly move them between Ranges.
Range managers may add an unassigned same-workspace consultant to their one active
Range, or remove its consultants; they cannot take consultants from another Range.
RangeMembership remains OneToOne per consultant. Membership writes protect workspace
owners and self targets using the existing user-management target policy.
Inactive consultants may retain/be assigned membership without account activation.
New assignments require an active destination Range. Agency managers may remove
memberships from inactive Ranges; range-manager writes require their active Range.

Membership assignment uses PUT on the destination Range/consultant relationship.
Moving an existing membership requires `from_range` equal to its current Range UUID;
missing/stale source fails without changing membership. Repeating assignment to
the same destination is idempotent. Range managers cannot supply `from_range`.
DELETE on that relationship removes only membership, never the user or Range.

Region constraints are replaced explicitly with a list; an empty list means
unrestricted geography. New assignments require same-workspace active Regions
under active same-workspace Cities. Existing inactive assignments may be retained
or removed, but not newly added. External-source Regions can be linked without
mutating their integration-owned data. No Region constraints means unrestricted
geography; `activity_scope=all` is the default activity setting.

Agency managers, owners, and assigned range managers see existing inactive Region
constraints. Consultants see only active Region constraints under active Cities;
`has_region_constraints` still reports true when constraints are hidden, so an
empty visible list must not be interpreted as unrestricted geography.
Roster reads are limited to agency managers, workspace owners, and the assigned
range manager. User summaries contain only UUID, username, first_name, last_name.

Range deactivation preserves manager, memberships, Regions, and user active flags.
Reactivation is explicit; there is no hard-delete Range endpoint. Existing model
constraints and the Range.regions M2M workspace guard remain unchanged.

## Location
Conceptual hierarchy:
- City
- Region

Workspace location strategy:
- `none`
- `custom`
- `divar`

External-source identity may be stored separately from internal identity.

Do not assume external region labels exactly match internal Homban regions.

### Location configuration API
All active customer members may read their workspace's location configuration.
Agency managers and workspace owners (regardless of operational role) may manage
it. Ownership grants location configuration access only; it does not expand the
organizational user-management policy.

City names are unique per workspace; Region names are unique per workspace/city.
The API derives workspace from authentication and validates Region.city against
that workspace. Manual records can be created, renamed, activated/deactivated;
manual Regions may be assigned to another city in the same workspace. Hard deletion
is not exposed. Existing model constraints are retained without migrations.

Location managers can read both active and inactive records for configuration and
reactivation. Other customer users can read only active cities and active regions
whose city is active. City deactivation preserves Region records and their active
flags; reactivation restores visibility for regions that remain active. Managers
may configure regions under inactive same-workspace cities; they remain hidden from
read-only users until the city is active. Region-mode selection does not alter this
visibility policy.

Ordinary creation always uses `source=manual`. Source and external ID/slug fields
cannot be supplied or changed. External-source records (currently Divar) are
read-only through this API, including their active state, and belong to a future
integration service's synchronization lifecycle.

Changing `region_mode` between `none`, `custom`, and `divar` changes configuration
only. It never deletes, rewrites, or synchronizes Cities/Regions. Manual configuration
remains available in every mode. `none` does not hide configured data by itself;
later File/Customer workflows may make Region optional. Divar synchronization and
network integration remain future work.

## Records
### File
Future core entity.
Type:
- sale
- rent

Assigned to one consultant.

### Customer
Future core entity.
Type:
- buyer
- tenant

Assigned to one consultant.

## Assignment
A consultant is the responsible owner of an assigned file/customer.

Assignment affects:
- Edit/delete permission
- Contact visibility
- Task ownership
- Matching collaboration
- Future commission attribution

## Matching
A Match connects one File and one Customer with:
- Compatibility score
- Explanation/reasons
- Formula/version used
- Potential cross-consultant collaboration information
- State if needed later

Matching should not mutate source records merely because a match exists.

## Pass
"Pass" is cross-consultant collaboration around a candidate file/customer match.

It must preserve:
- Original file owner
- Original customer owner
- Participants
- Future transaction/commission attribution

Do not implement commission calculations until specified.

## Task
Two conceptual task sources:
- System-generated recommendation, especially Match
- User-created reminder/calendar task

They may share one operational feed without necessarily sharing one persistence model.

## Deal
A Deal is historical/business-critical data.

It links the relevant file/customer and captures transaction facts.

Creating a deal triggers business state transitions on related records.

Treat historical deal data more conservatively than ordinary operational records.

## Chat
Conversation types:
- Private direct conversation
- Official announcement channel
- Optional general group

All conversations belong to one workspace.

## Notification
A notification belongs to a recipient and has:
- Type/category
- Event/reason
- Destination/action
- Read state
- Timestamp

Notifications are downstream of domain events and should not own business logic.

## External imports
Future external-import flow:
External source -> fetch/discover -> normalize -> stage -> consultant review -> approve/reject -> internal region mapping -> file creation/update.

Do not directly trust external source fields as internal canonical values.
