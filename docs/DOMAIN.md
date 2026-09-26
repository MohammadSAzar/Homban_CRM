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

Workspace Region configuration is workspace-wide: exactly one operational taxonomy,
`custom` OR `divar`, shared by all users and Ranges. It is never a per-user,
per-consultant or per-Range setting. A custom Workspace has one Workspace-owned
collection of Regions, grouped by City, not multiple independent Region systems.
`region_mode = NULL` (the default) means setup is incomplete, not an operating mode.
`none` is no longer valid. Configuration must explicitly choose custom or divar.
Cities/Regions, including future Divar-derived records, remain stored within each
Workspace boundary; there is no cross-workspace Region sharing or business mapping.

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

The settings API reads NULL/custom/divar, but accepts only custom or divar for
configuration. It rejects none, NULL, blank and arbitrary write values. Switching
mode never deletes, rewrites or synchronizes Cities/Regions, nor changes their source.
Retained records are history in the same Workspace collection, not separate taxonomies.
Existing manual location management remains available; no source-based filtering or
Divar synchronization is introduced. Setup state does not change existing record
requiredness or block the existing operational APIs in this focused change.

## Records
Future crawler/API/voice-AI ingestion may use incomplete Draft/Staging records.
Canonical PropertyFile/Customer records will require complete operational data before
promotion. No Draft/Staging implementation is included. Canonical PropertyFile
and Customer completeness are enforced below. Incomplete extracted data cannot
become canonical until validation succeeds.

### File
`apps.properties.PropertyFile` is the core stored CRM record, with an operational REST API.
It has a UUID, immutable workspace, required same-workspace consultant assignee,
server-generated code, transaction type (`sale`/`rent`), status, and timezone-aware
created/updated timestamps. Range membership is not required.

City and exactly one Region are required, even while Workspace setup is incomplete.
Both, and the Region's City, must belong to the file's Workspace; Region.city must
match the selected City. New Region selection requires an active Region. An existing
link may remain after its Region becomes inactive; changing Region requires an active
replacement. No new City-active-state rule is introduced.
A Region with linked files cannot move to a City inconsistent with those files.
Address and description are explicit text fields. Owner name, owner phone, and
visit-contact phone are separate fields; contact data is sensitive and must receive
field-level authorization in future APIs.

Structured characteristics are area (decimal square metres), bedrooms, total_floors,
units_per_floor, unit_floor, building_age, parking, storage, elevator, and balcony.
Area is required and strictly positive; bedrooms is required and may be zero. Other
optional numbers may be NULL; zero is accepted for ground floor and new buildings.
Negative numbers are rejected (basement numbering is not introduced in this stage).
Facilities are nullable booleans: NULL means unknown, False explicitly absent.
No floor-count interpretation or additional upper business bounds are inferred.

All four monetary fields use Decimal values in **Iranian تومان**, with two decimal
places, never floats or rials. Sale total_price is required and nonnegative; rent
amounts must be NULL. Rent deposit_amount and monthly_rent are both required and
nonnegative (zero is valid); total_price and price_per_square_meter must be NULL.
No deposit/rent conversion occurs.

For sale, price_per_square_meter is stored but system-derived:
`floor((total_price / area) / 1_000_000) * 1_000_000` تومان. Thus 152,778,623
becomes 152,000,000, not 153,000,000. Exact Decimal integer ratios avoid floating point
and intermediate division rounding. Model validation derives it; normal saves run in
an atomic block and lock an existing row. Partial saves validate the resulting stored
row and always persist its recomputed derived price, ignoring excluded in-memory
changes. Rent saves clear the derived value. API create/PATCH rejects this field.
Type changes must explicitly clear incompatible amounts and supply all target-type
amounts; missing values are never invented. Invalid aggregate changes roll back.

Source is an extensible technical choice field, currently `manual` only. Future
choices may include Divar, Amlak Plus, Kashano, Peyvand, colleague, previous contact,
or office sources; no integrations, discovery or import framework exists yet.

`is_valuable` is independent of zero or more `PropertyFileValuableReason` children.
Each child stores one reason label, unique per file. There is no comma-separated
list, shared cross-workspace catalogue, seeded reason taxonomy, or inferred flag
change when reasons are edited.

`PropertyFileImage` stores UUID, parent, one opaque reference (URL/path/storage key),
nonnegative sort_order and created_at. Ordering uses sort_order, then created_at/UUID
for ties. No fetching, URL rendering, file uploads or storage infrastructure is added.
A future storage relation can extend this child model without changing PropertyFile.

The display/search code is `PF-` plus the complete uppercase UUID hex (35 characters).
It is derived server-side, stable, non-sequential and globally UNIQUE in the database.
No row counts, shortened random codes or read-then-increment races are used. A UUID/code
collision fails safely on uniqueness rather than overwriting a record during creation.
Codes are identifiers, never authorization credentials.

Statuses are active (default), inactive, sold, rented and archived. This foundation
stores status without introducing a deal workflow or automatic transitions. Ordinary
workflows should change status, not delete records. Workspace, assignee, City and
Region use PROTECT: deleting them cannot cascade away property history. This also
intentionally blocks Workspace deletion when files exist. Image/reason children use
CASCADE only if a file is deliberately deleted through privileged maintenance ORM;
no hard-delete service or customer workflow is provided.

Model saves call full_clean for workspace/assignee/location and field validation.
NOT NULL fields and CHECKs enforce required core data, positive area, nonnegative
amounts, required sale/rent financials and their separation, valid status,
and nonempty code. The code and per-file reason uniqueness also have database guards.
The atomic creation service saves a file and its children together, using the existing
Workspace lock order. It is an internal domain operation, not an authorization API.
Bulk updates/raw SQL bypass cross-table model validation and are unsupported for
relationship mutation or derived-price maintenance. Derived equality is an application
invariant, not a database-generated column. Existing User/City workspace changes must not be made through
unvalidated maintenance writes; cross-table invariants are not SQL CHECK constraints.
PropertyFile partial saves also validate the resulting stored row: an unsaved City
change cannot conceal an incompatible Region-only update (or the reverse).

Indexes cover workspace + status, transaction_type, region and assigned_to for normal
CRM scoping/filtering, in addition to FK and unique-code indexes. Area, bedrooms,
total_price and building_age are future Matching/filter candidates; additional indexes
await actual query plans rather than speculative independent numeric indexes.
Location, transaction, characteristics and prices may inform future Matching. Address,
description, owner/visit contact data, sources, images and valuable reasons remain
first-class CRM data regardless of future Matching use. Matching remains future work.

#### PropertyFile operational API
`/api/v1/property-files/` supports paginated GET and POST; `/<uuid>/` supports GET and
PATCH only. Actor scope is documented in PERMISSIONS; this API does not expose files
outside management/assignment scope for Matching. PATCH supports profile/property data,
sale/rent values and existing statuses, with no Deal-driven transitions. Workspace,
UUID/code, timestamps, source and price_per_square_meter cannot be written. Applicable fields may be cleared
with null when switching sale/rent type. Existing domain rules remain authoritative;
existing inactive Region links may remain, but newly selected Regions must be active.

`valuable_reasons` is an optional array of individual labels on create/PATCH. Supplying
it replaces the entire set atomically; [] clears it. Omission preserves existing reasons.
The is_valuable flag remains independent. Detail returns reasons and ordered images.
POST `/<uuid>/images/` with reference appends an opaque reference. PATCH that endpoint
with `order: [image UUIDs]` requires every current image exactly once and assigns
contiguous positions. DELETE `/<uuid>/images/<image_uuid>/` removes only that reference;
it never deletes the file or external media. No uploads, fetching or storage are added.

List filtering supports transaction_type, status, assigned_to, city, region, min_area,
max_area, bedrooms, min_total_price, max_total_price, source and is_valuable. Monetary
filters use تومان; price bounds naturally exclude rent rows whose total_price is NULL.
Filters narrow authorized scope and do not implement Matching. Lists use 50-row pages
with created_at/UUID ordering, join display relations, and omit child collections.
Detail prefetches images/reasons and includes contact data only after scope checks.

### Customer
`apps.customers.Customer` is a first-class CRM record with an operational REST API.
Core fields are UUID, immutable workspace, required same-workspace consultant
assigned_to, stable code, customer_type (`buyer`/`tenant`), status, name, mobile,
description, is_valuable and timezone-aware created_at/updated_at. Name is required;
unknown mobile and notes may be empty. Mobile is contact data, not a unique identity.
Assignment does not require Range membership and remains explicit for future pass.
No pass, Matching or Deal transitions are implemented. API access uses explicit actor scope.

Canonical min_area/max_area (decimal square metres) and bedrooms are required;
zero is valid and min_area must not exceed max_area. Building-age bounds remain
nullable (no requirement). Negative values and invalid bounds are rejected by model
validation and database CHECKs; required core fields are NOT NULL.

Buyers require nonnegative budget; budget_status remains nullable. The confirmed Google Sheets choices are
`cash` — کاملاً نقد and `cash_plus_property` — بخشی نقد + آپارتمان.
No choice is forced: default is NULL and empty strings normalize to NULL.
Tenants require separate nonnegative deposit_budget and monthly_rent_budget; zero is valid for either. Buyer tenant-only
amounts must be NULL; tenant budget and budget_status must be NULL. All money uses
Decimal (two decimal places), in Iranian **تومان**. No automatic conversion occurs.

preferred_regions is a real M2M via CustomerRegionPreference, with a unique
(customer, region) pair. all_regions defaults to False: canonical creation must
explicitly select all_regions=True or provide at least one preferred Region.
True requires empty M2M links and means every currently active Region in this
Workspace, including future active additions, without backfill. False requires one
or more explicit Regions, possibly in different Cities; there is no duplicated City
preference. No Regions from another Workspace count. This defines future Matching
semantics only; no Matching implementation exists.
Every Region (and its City) must belong to the customer's workspace. New links to
inactive Regions are rejected. Existing inactive preferences can remain or be removed;
removing then re-adding is a new assignment. City active state is not an additional
preference rule. Empty explicit preferences are never a canonical state.

An m2m_changed guard protects forward/reverse add/set because Django's M2M manager
bulk-creates links without calling the through model's save. Direct through-model
saves also validate. Customer.save(preferred_regions=...) and domain/API services
use Workspace-row locking and transactions for aggregate creation and replacement.
Replacement adds validated links before removing old ones. Direct forward/reverse
remove/clear and through-model deletions cannot remove the last explicit preference;
use the aggregate service to replace the full set. all_regions=True clears links
atomically. Customer partial saves validate the effective stored state, not excluded
in-memory changes. Failed changes preserve old links.
These are internal domain services, not actor authorization. Bulk writes/raw SQL
bypass application-level cross-table checks and are unsupported; direct mutation of
related Workspace membership also remains outside these guarantees.
Partial saves of a CustomerRegionPreference validate the resulting stored pair,
so excluded in-memory changes cannot conceal a cross-workspace relationship.

CustomerValuableReason stores one label per child, unique within its Customer.
Reasons are independent of PropertyFile vocabulary. is_valuable may be true with
zero reasons; neither reasons nor flag are automatically inferred from the other.
Statuses are active (default), inactive, completed and archived. They preserve
relationships; no automatic completion or ordinary hard-delete workflow is added.
Workspace and assignee use PROTECT. Region preferences use PROTECT for Region,
also preventing cascading City deletion from silently losing selected preferences.
Customer-owned reasons/preferences use CASCADE only for explicit maintenance deletion.

Customer codes use `CU-` plus complete uppercase UUID hex, unique in the database and
immutable through model saves. This follows PropertyFile's UUID convention without
modifying PropertyFile (it has no existing shared generator to reuse). No row counts
or shortened random values are used; collisions fail safely at uniqueness validation.
Code is searchable/displayable and is never authorization.

Indexes cover workspace + status, customer_type and assigned_to, plus normal FK and
unique-code/pair indexes. Further budget/bedroom/area indexes await actual query plans.
Regions, type, requirements and original financial values are future Matching inputs.
Name, mobile, description, status, assignment and valuable reasons remain essential
operational CRM data regardless of future Matching use. Canonical dates stay timezone
aware; Jalali display is deferred to presentation.

#### Customer operational API
`GET/POST /api/v1/customers/` lists/creates and `GET/PATCH /api/v1/customers/<uuid>/`
retrieves/updates authorized Customers. No PUT or DELETE is available. PATCH accepts
existing statuses without automatic Deal transitions. Workspace, code, UUID and
timestamps are server-owned. Name is required on creation; notes/mobile may be empty.
Type changes require target-type financial values; the API clears incompatible
old fields atomically and rejects supplied incompatible amounts. Missing target
values reject the transition; no amount is invented.

`all_regions` is returned in both list and detail. `preferred_regions` writes use
an array of Region UUIDs. Omission preserves links unless switching to all_regions=True,
which clears them. True plus nonempty explicit Regions is rejected. Switching back
to False requires at least one supplied Region in the same operation. An empty
array while False is rejected. Replacement retains unchanged inactive links but rejects
new inactive links, including previously removed links. Duplicate UUIDs represent one
preference. Regions under inactive Cities remain permitted; Workspace setup policy is unchanged. Reads expose scoped Region id/name/city/is_active in detail.
`valuable_reasons` is an array of labels; omission preserves, `[]` clears, and duplicate
labels fail validation. The valuable flag remains independent. Scalar, assignment,
preference and reason changes roll back together if any validation fails.

Lists are ordered by descending creation time then UUID, with 50 records per page.
They include the authorized Customer name and requirement/financial summaries;
mobile, description, preferred Regions and reasons are detail-only. Filtering supports
customer_type, status, assigned_to, preferred_region, bedrooms, budget_status and
is_valuable. `min_area` filters stored min_area >= the supplied value; `max_area`
filters stored max_area <= the supplied value. These are requirement-bound filters,
not Matching or overlap rules. preferred_region filters explicit links only;
it does not expand all_regions into Matching candidates.
min_budget/max_budget, min_deposit_budget/max_deposit_budget and
min_monthly_rent_budget/max_monthly_rent_budget are inclusive stored-amount filters.
All money remains تومان; no financial conversion is performed.

## Assignment
A consultant is the responsible owner of an assigned file/customer.

Assignment affects:
- Edit/delete permission
- Contact visibility
- Task ownership
- Matching collaboration
- Future commission attribution

## Matching
### Persisted base MatchingProfile
`apps.matching.MatchingProfile` stores each user's base/default configuration,
with a UUID, OneToOne user ownership and timezone-aware timestamps. Workspace is
inherited through the user, never duplicated on the profile. The profile is lazily
created on first authenticated GET/use; Workspace -> User -> Profile locks serialize
creation, PATCH and reset, and the database enforces one profile per user. Personal
settings use CASCADE on user deletion; they are not CRM history records.

Persisted defaults (relative weights, not percentages):
- area = 25, bedrooms = 15, building_age = 10, region = 10
- parking = 4, elevator = 3, storage = 2, balcony = 1
- minimum_score = 50 (allowed range 0–100)
- sale_budget_lower_ratio = 0.80; sale_budget_upper_ratio = 1.20
- rent_per_100m_deposit = 3,000,000 تومان

Weights are nonnegative Decimals with two decimal places; at least one must be
positive. They need not total 100 and are never normalized by settings storage.
Budget is an eligibility gate, not a weighted criterion: there is no budget weight.
Sale gate ratios use four decimal places, with 0 < lower <= 1 <= upper and
lower <= upper. Rent conversion must be positive: the fixed v1 base is 100,000,000
تومان deposit, equivalent by default to 3,000,000 تومان monthly rent. Only the
monthly-rent equivalent is configurable. No conversion or gate is calculated here.

`GET/PATCH /api/v1/matching-profile/` reads/updates only the authenticated user's
settings. `POST /api/v1/matching-profile/reset/` accepts an empty object and restores
all defaults atomically, preserving profile identity. Responses expose only settings,
not owner/workspace/security fields. PATCH validates the complete resulting profile.
There is no collection, owner-ID route or DELETE endpoint.

Temporary detail-page sliders and «خط قرمز» hard constraints are runtime-only future
options, not persisted fields. A future pure Matching Engine will consume profiles,
PropertyFiles, Customers and optional runtime inputs. This feature performs no pair
scoring, candidate search, recommendation generation or calculation/result persistence.

### Future Matching Engine and results
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
