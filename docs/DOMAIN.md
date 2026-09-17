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

Only agency managers can update a consultant's range assignment, including clearing
membership with `range_id: null`. Updating a range manager's managed-range assignment
is outside this feature. Secretary/admin users do not receive consultant membership.

Deactivation only sets `User.is_active=False`. It preserves UUID, username, ownership,
range membership, managed ranges, and other users' state. Reactivation is explicit.
It does not cascade deactivation to a manager's consultants. Existing authentication
checks deny inactive users on login, access, and refresh. Deactivation does not
blacklist tokens: a still-unexpired token may work again after reactivation.

Agency managers (including self) and workspace owners are read-only targets in this
API. Range managers can view themselves but only manage in-scope consultants.
Sensitive agency-manager/owner lifecycle operations require a future explicit
administrative workflow, as confirmed by the product owner for this feature.

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
