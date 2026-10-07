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

Temporary detail-page sliders and «خط قرمز» hard constraints are live request
options described below, not persisted fields. The profile does not persist results.

### Pure Matching Engine v1
`apps.matching.engine.evaluate_match(property_file, customer, profile)` evaluates
exactly one canonical pair; direction does not change logic. It returns frozen
MatchingResult/BudgetGate/CriterionResult/RegionPenalty value objects, identified by
`matching-v1`. It never creates profiles, mutates source objects or writes results.
Authorization and candidate visibility belong to future callers, not this evaluator.

Eligibility is checked before scoring: equal Workspace IDs, buyer/sale or tenant/rent,
`active` status on both records, and an inclusive budget gate. Other existing statuses
are rejected. Foreign file Region relationships fail closed. Rejection gives
eligible=False, recommended=False, no score, and an English code/Persian explanation.

Sale gate: customer budget × profile lower/upper ratios bounds file total_price.
Rent equivalent: deposit + monthly_rent × 100,000,000 / rent_per_100m_deposit,
for both sides; apply the same profile ratios to customer equivalent. Budget earns
no scoring points. Zero amounts are allowed. Gate comparison uses amounts scaled
by the common conversion rate before division, preserving exact inclusive boundaries
even when equivalent deposits repeat as decimals.

Scoring (W is the corresponding profile weight):
- Area inside [min_area,max_area]: W. Outside, distance from the nearest boundary
  divided by that boundary gives percentage deviation. <=5%, <=10%, <=15%, <=20%,
  <=25%, >25% earn respectively .88W, .76W, .56W, .32W, .16W, 0.
  Approved zero-bound edge case: max_area=0 with positive file area earns 0;
  percentage deviation is undefined (None), never division by zero.
- Bedrooms: exact earns W, one-room absolute difference earns 8W/15, >=2 earns 0.
- Age: no requested bound excludes the criterion entirely. A one-sided bound leaves
  the other side unrestricted. Requested but unknown file age earns 0. Inside earns
  W; outside the nearest bound by <=2, <=5, <=10, >10 years earns .80W, .50W, .20W, 0.
- Explicit preferred Region earns W, including retained inactive historical links.
  Non-preferred earns 0. all_regions=True earns W only for an active Region in this
  Workspace. Approved historical edge case: inactive Region earns 0 under all_regions,
  remains eligible, and receives no region penalty.
- Parking/elevator/storage/balcony are file-quality bonuses, always applicable:
  True earns W; False or NULL earns 0. No Customer facility preferences are inferred.

Normalized score = earned applicable points / available applicable weight × 100.
Zero weights add nothing. If applicable weight is zero, eligible remains True but
recommended=False, both scores are None, and reason_code is `no_applicable_weight`.
For explicit non-preferred Regions only, final score is normalized score × .90;
otherwise it is unchanged. Final score is clamped to [0,100]. Recommendation uses
final score >= minimum_score inclusively; below threshold remains eligible.
Arithmetic uses a fixed 50-significant-digit Decimal context, independent of the
caller, without display rounding or quantization. Bucket comparisons avoid early
division rounding. Persian messages live separately in `explanations.py`.

Results expose eligibility/recommendation, scores/threshold, version, technical codes,
Persian explanations, per-criterion applicability/weight/earned/reference values,
budget amounts/range/conversion, point totals and region penalty. No contacts,
addresses, notes, image references or source model instances are returned.
Load scalar fields normally and use select_related("region") on PropertyFile plus
prefetch_related("preferred_regions") on Customer for zero-query evaluation. Otherwise
there is at most one file Region read and one preference existence read; all_regions
skips the preference read. The evaluator does not populate input relation caches.

The pure evaluator remains free of runtime overrides and hard constraints. Live
request handling below adds those options without changing v1 formulas. Saved
recommendation lifecycle and score-free collaboration requests are separate below.

### Live Matching v1
`POST /api/v1/customers/<uuid>/matches/` and
`POST /api/v1/property-files/<uuid>/matches/` calculate non-persistent matches.
The source must be in the actor's existing operational scope: agency workspace,
consultant ownership, or union of a range manager's active managed Ranges. Existing
secretary/admin denials and 404 behavior remain. Candidates may belong to any
consultant in the same Workspace, across Range boundaries. Active record status and
compatible type are coarse filters; inactive assignees do not erase otherwise active
historical records. Workspace/assignee/location integrity filters fail closed.

The viewer's own MatchingProfile supplies defaults (lazy creation is reused).
The body accepts only `overrides` and `hard_constraints`, both optional objects.
Overrides may contain the twelve MatchingProfile setting fields, validated with the
same field limits and positive-weight invariant. They form an in-memory settings
object and never update the profile. Unknown keys and non-boolean hard toggles fail.
An empty body uses base settings with no hard constraints.

«خط قرمز» toggles are exact runtime checks, independent of scoring weights:
- area: inside the Customer's inclusive min/max bounds, without tolerance.
- bedrooms: exact equality.
- building_age: known age within all requested bounds. A fixed Customer source with
  no age bounds rejects the toggle. In File -> Customers, the toggle is inapplicable
  for candidate Customers without age bounds; no requirement is invented.
- region: explicit preferred membership (including retained inactive links), or an
  active same-Workspace Region for all_regions=True. Failure excludes the pair.
- parking/elevator/storage/balcony: the selected facility must be True; False/NULL fail.

Order: shared engine eligibility -> exact hard checks -> finalized evaluate_match ->
existing Region penalty/threshold. Eligibility is exposed for reuse, without changing
formulas or existing evaluator output. Only recommended, eligible, hard-passing pairs
are returned; there is no diagnostic mode. Results sort by full-precision final score
descending, then UUID ascending, never ownership. Both directions reuse the same pair
logic and score representation. Decimal results are serialized as strings, not floats.

Each row contains a restricted `candidate` plus the engine result: scores, version,
threshold, budget summary, criterion/reference explanations and Region penalty.
The same restricted serializers apply even to owned records. No Customer name/mobile,
contact fields, addresses, descriptions, private media/reasons, assignment identity or
user/security internals are included. Location IDs and matching requirements/financials
are exposed only within the Workspace.

At most 1,000 coarse candidates are evaluated per request. The query fetches at most
1,001 to detect overflow; overflow returns a clear 400 before scoring, rather than
silently returning a partial ranking. Within that bound all qualifying matches are
ranked, then paginated at 50 with `?page=N`, `count/next/previous/results`. Repeat the
same POST body on subsequent pages; results are live, not a saved snapshot. No client
page-size or evaluation-cap override exists. Out-of-range pages use DRF 404 behavior.
Region joins/prefetches prevent per-candidate queries. Requests follow Workspace ->
User -> Profile locking and reauthorize current actor state; only absent-profile lazy
creation may write. Source records, preferences, runtime options and results are never
persisted. No Tasks, notifications, pass workflow or frontend is included.

### MatchRecommendation foundation
MatchRecommendation is a persisted operational projection unique on
`(viewer, property_file, customer)`. The viewer must currently be an active consultant
in the same active Workspace, owning at least one side. Own-own pairs have one viewer;
two-owner pairs can have independent rows, scores and manual statuses for each owner.
Workspace is derived through the references, not duplicated. UUID and timezone-aware
created_at/updated_at/last_evaluated_at follow project conventions. PROTECT on all three
references retains business history; reassignment remains allowed.

`recommendation_services.refresh_recommendation(viewer, property_file, customer, change)`
is an internal single-pair command. It locks current data, evaluates using only the
viewer's persisted base MatchingProfile, and applies the verified result privately.
No caller-supplied score, runtime overrides or hard constraints are accepted. A first
non-recommended evaluation creates no row; existing rows remain even when no longer
recommended. Automatic generation below reuses this lifecycle's private application
function with batch persistence; the standalone command still evaluates one pair.

System flags are independent of manual status:
- is_source_valid: both source statuses are active. A budget/type/threshold failure
  does not itself expire otherwise active sources.
- is_currently_recommended: the authoritative unrounded engine recommendation decision.
- is_viewer_valid: the consultant still has a valid same-Workspace owner association.

Manual statuses are new, seen, done and rejected. `change_manual_status` accepts seen,
rejected or done, reversibly, only for the current viewer/owner. Seen represents an
explicit detail-open event, including reopening an already seen item. Done requires
current ownership of BOTH sides. Manual commands never invoke the engine. Source
expiry preserves manual status; expired is not a user-selectable status.

Frozen `ChangeContext(material_inputs_changed=False, sources_became_operational=False)`
is supplied by trusted generation. It must reflect actual events, not score
variation or updated_at. If recommended, rejected/done rows become new on a material
change or source restoration. Restoration observed in the previous persisted invalid
source flag is also recognized; the explicit context supports restoration between
evaluations. New/seen statuses are not reset by refreshed scores or these events.
No reactivation timestamp/count is stored because this lifecycle needs neither.

Reassignment never blocks on recommendations. If the viewer loses both sides, keep
the historical row/status/score/view baseline, invalidate its association and current
recommendation during reconciliation, and deny manual actions immediately. The
`for_viewer`/`active_for_viewer` query scopes check CURRENT ownership, role, Workspace
and active account state even before reconciliation. Active scope also checks source
statuses and saved validity/recommendation flags; it does not override manual status.
Future feeds must use these scopes and their own restricted serialization, never raw
model relations. New owners are reconciled independently. A historical done status
survives ownership loss, but a new done action still requires ownership of both sides.

Scores use Decimal(33,30), flooring only for MySQL storage AFTER the engine decision.
The persisted minimum_score is evaluation metadata, not a copy of personal settings.
`score_at_last_view` changes only on an actual seen action, never on recalculation.
The derived has_improved_score flag requires seen status, valid/recommended state and
current_score > score_at_last_view >= the latest evaluated minimum_score. Unknown,
expired or below-threshold scores do not show improvement. Reopening details updates
the baseline and clears the indicator. Differences below storage precision are not
badged; threshold eligibility always comes from the full-precision engine result.

Workspace -> User -> File -> Customer locks serialize lifecycle work with operational
writes; projection/profile locks and the unique DB key protect repeated/concurrent
upserts. Model validation preserves immutable references and validates the effective
stored state for update_fields. Normal save, bulk_create, bulk_update, QuerySet.update
and deletion are service-guarded. Deliberate raw SQL/base QuerySet bypasses remain an
internal maintenance boundary: database uniqueness, scalar checks and foreign keys
still apply, but cross-table ownership cannot be guaranteed by a simple CHECK.

No contacts, names, notes, addresses, images, full result JSON or profile settings are
copied into recommendations. Sources are never mutated. No automatic generation,
Celery processing, collaboration requests, daily feed APIs, notifications or frontend
were implemented by the foundation itself. Generation is implemented separately below.
Existing live Matching remains non-persistent.

### Automatic recommendation generation
Normal PropertyFile/Customer/model and API saves compare persisted Matching input
values before/after the aggregate transaction, including update_fields and Customer
preferred Regions. Known scalar inputs are enumerated in generation_events.py; phones,
notes, images, valuable reasons, names and updated_at do not trigger work. Profile
settings changes/reset use the same comparison. Region activation affects files using
that Region and all_regions Customers; explicit historical Region links retain their
existing engine semantics. City activation does not alter the finalized engine formula.
Direct preference save/delete and M2M add/remove paths also capture changes; nested
aggregate operations avoid duplicate events. Raw SQL/QuerySet bulk source updates are
maintenance-only and require explicit reconciliation; they bypass ordinary model hooks.

Each committed change records a RecommendationWork outbox row containing Workspace,
target kind/ID and bounded continuation state, never source contacts or model payloads.
Publication uses transaction.on_commit. Rollbacks discard both data changes and work.
Broker failures leave durable pending work and do not undo committed business writes.
Celery runs the work asynchronously; operational requests never evaluate candidates.

File/Customer events first reconcile existing affected recommendations, including
inactive/type-incompatible sources and former owners, then discover new active,
same-Workspace, compatible pairs with valid assignment/location links. A pair has
one viewer for own-own ownership or up to two current consultant-owner viewers.
Every viewer uses their own base profile, loaded in a batch; missing profiles are
lazily created only for valid viewers actually evaluated. No third-party viewer or
runtime live-Matching option enters generation. Profile events reconcile/discover only
that viewer's universe, leaving the other owner's row untouched. Region work remains
Workspace-local. Account/Workspace entitlement changes trigger ordinary reconciliation,
not material reactivation, without broadening operational permissions.

The pure evaluate_match function remains the only scoring authority. Its unrounded
recommendation decision and existing lifecycle application are reused. Source expiry,
below-threshold state, reversible manual statuses, own-own DONE restriction and viewed
score baselines retain foundation semantics. Known Matching input changes may reactivate
rejected/done only when recommended and entitled; new/seen are preserved. No score-only
or metadata-only inference of material change is made. Reassignment never blocks and
former-owner history stays inaccessible through current-owner scopes; new owners get
independent evaluation. Seen score improvements never overwrite the viewing baseline.

Each task processes at most 25 existing rows, or one File with at most 25 Customers
(at most 50 owner-specific evaluations). UUID keyset cursors continue across tasks;
there is no full Cartesian load or silent truncation. Region joins, preference prefetch,
bulk user/profile/row loading and a private validated bulk persistence sink keep relation
queries constant as candidates increase. Missing-profile creation is bounded by the
current chunk. All normal source writes and jobs follow Workspace-first locking.

Work IDs are monotonically allocated under the Workspace lock. Each projection stores
last_generation_event. Jobs reload authoritative current records/settings under that
same lock: older events cannot overwrite rows processed by newer events. Durable step
tokens make duplicate delivery/continuation a no-op; chunk writes and cursor advancement
commit atomically. Database failures retry with backoff (five retries), and exhausted
work remains recoverable. Material-event high-water marks per relevant target preserve
unconsumed material context when a newer ordinary recovery overtakes older work.
Completed outbox rows retain these markers; do not purge them without a future safe
compaction policy. No full status history or result JSON is stored.

Internal recovery: recover_pending(after_id=0, limit=100) republishes a bounded page
of pending work; recover_recommendations chains those pages. reconcile_workspace(id,
viewer_id=None) schedules a bounded ordinary repair/discovery after maintenance, optionally
for one viewer. Recovery has no customer-facing endpoint and does not continuously scan
all Workspaces. It preserves statuses unless applicable unconsumed material events or
the existing source-restoration rule warrant reactivation.

Deployment requires applying matching/0003_recommendation_generation.py, installing
requirements and providing CELERY_BROKER_URL (default local Redis). Start a worker with
`celery -A config worker -l INFO`; local Windows development uses `--pool=solo`.
There is no result backend or Beat requirement. Following broker/worker outages, an
operator may enqueue `apps.matching.tasks.recover_recommendations.delay()` from a
trusted Django shell. Task payloads contain only work ID and step. Tests isolate broker
publication and execute real DB task bodies; Redis service provisioning is operational
setup. Celery wiring follows its [Django integration documentation](https://docs.celeryq.dev/en/stable/django/first-steps-with-django.html).

Generation itself does not add a feed, frontend, manual reminders/tasks or deal workflow.
CollaborationRequest and its minimal recipient event are implemented separately below.

### CollaborationRequest v1
A request connects two consultants around one PropertyFile/Customer pair for manual
coordination. It tracks no visit, negotiation, commission, outcome or free-text notes.
Only active consultant owners of opposite sides in the same active Workspace may
submit. Own-own pairs and unrelated viewers cannot collaborate; agency/range roles,
Workspace ownership and staff/superuser flags confer no additional permission.

CollaborationRequest has UUID identity, protected immutable File/Customer and original
requester/recipient references, canonical participant_a/participant_b and timezone-aware
timestamps. The database enforces participant_a < participant_b, orientation matching
that participant set, valid manual status, and uniqueness of File + Customer + unordered
participants. Both directions, including accepted/rejected requests, reuse the same row
without resetting status. A different later owner pair may create a different row.

Creation services hold the existing Workspace-first lock throughout authoritative source
reload, canonical lookup and insertion. Opposite submissions therefore serialize before
lookup, with database uniqueness as an additional safeguard. The request and its one
CollaborationEvent commit together or both roll back. Normal direct/bulk writes and
hard deletion are guarded, including partial saves; raw SQL/base QuerySet are privileged
maintenance boundaries, not supported product write paths. Foreign keys use PROTECT.

Endpoints:
- POST `/api/v1/match-recommendations/<uuid>/collaboration-request/`: empty object;
  own saved recommendation, valid viewer/source flags and current cross-owner sources.
  No recipient recommendation/profile or recalculated score is required.
- POST `/api/v1/collaboration-requests/from-live-match/`: `{ "reference": "..." }`.
- GET `/api/v1/collaboration-requests/<uuid>/`: participant-only restricted detail.
- POST `/api/v1/collaboration-requests/<uuid>/status/`: only `status`, one of seen,
  accepted, rejected. Recipient-only, current-validity checked, idempotent.
No list, DELETE, unrestricted PATCH or arbitrary pair-ID creation endpoint exists.
Creation returns 201; duplicate reuse returns 200 with `created=false`, existing ID/URL,
other consultant professional identity and a Persian already-exists message.

Eligible returned cross-owner Live Matching rows include `collaboration_reference` only
for a consultant who owns one side. This Django timestamp-signed reference expires after
300 seconds and binds actor, Workspace, File and Customer, plus a keyed ownership
fingerprint. Signing alone is not encryption: the fingerprint deliberately hides the
other owner's UUID until collaboration exists. No score, profile settings or contacts
enter the token. Redemption verifies signature/age/actor/Workspace, reloads current
assignments, active accounts/sources, type compatibility and location/preference integrity.
Assignment mismatch rejects redemption. Repeat redemption is idempotent. Existing
Matching scoring, candidate restrictions, ordering and pagination are unchanged.

Manual status is new/seen/accepted/rejected. Only the recipient responds; opening a new
valid incoming detail marks it seen. Requester opens and accepted/rejected opens preserve
status. Seen/accepted/rejected are reversible without text, confirmation or scoring.

System `is_valid` is derived from current authoritative records, not a persisted stale
flag: active Workspace/consultants, current two-owner association, active sources,
compatible types and same-Workspace location integrity. Requests remain stored when
invalid, preserving manual status. Invalid historical detail exposes participant/status
metadata but returns null for both source details; manual actions fail. Workspace changes
fail closed entirely. Same original participants returning to validity reuse history and
preserve status without another event. No generation job is needed to revoke access.

Both participants see IDs, usernames, first/last names and roles. They receive the same
conservative pair representation: codes, location IDs, property characteristics,
requirements and financial values. Customer name/mobile, owner/visit phones, owner name,
addresses, private notes, media, valuable reasons and consultant phones are never exposed.
Collaboration has NO score, threshold, formula breakdown, profile or runtime settings,
in storage, responses or events. Operational access remains separately authorized.
Joined sources/users and prefetched Regions with Cities bound relation queries, including
repeated validity checks and serialization with multiple preferred Regions.

CollaborationEvent is one UUID/timestamp record with a unique protected request reference.
Its recipient is the immutable request recipient. It is a durable in-app alert foundation
for the Daily Tasks feed, not a generic notification system. Duplicate submissions,
status changes, opens and system revalidation do not emit another creation event.

Frontend and external push/email/SMS delivery,
visit scheduling, negotiation, commission splitting and collaboration outcome tracking
remain unimplemented.

## Daily Tasks / Suggested Program API v1

The `GET /api/v1/daily-tasks/` endpoint is a role-aware read projection over
MatchRecommendation, CollaborationRequest and ManualTask rows, with no DailyTask table.
Consultants receive their own recommendations, participant collaborations and personal
tasks. Other active customer roles receive only their own ManualTasks. It neither
scores pairs nor enqueues generation. Recommendation cards expose only the current
viewer's persisted score, source codes, validity/recommendation/improvement flags and
status/Jalali display timestamps. Collaboration cards expose direction, participant professional
identity, manual status and current validity; source codes are null when invalid.
Collaboration cards/details/actions never contain scores, thresholds or profiles.

Filters: `type=all|matching|collaboration|manual` (default all) and
`status=active|new|seen|rejected|done|accepted|expired|pending|cancelled|all` (default active).
Matching+accepted and collaboration+done are invalid. Combined accepted selects only collaborations; rejected selects recommendations and collaborations. Combined done
includes done recommendations and manual tasks; pending/cancelled select only manual tasks.
Manual accepts active/pending/done/cancelled/all, rejecting other status combinations. Recommendation active/new/seen require
current viewer access, valid active sources and currently recommended state. Rejected/done
are manual history; expired means source/viewer invalid, not merely below threshold.
Former owners lose all recommendation access immediately, even with status=all.
Collaboration active requires current validity and new/seen; named manual-status filters
include that status independently of validity. Expired selects invalid participant history.
Invalid collaboration history exposes metadata, never newly unauthorized source details.

Default priority: new incoming collaboration, overdue manual tasks, today manual tasks,
new recommendations, seen recommendations, seen incoming collaboration, sent collaboration.
Manual tasks sort by scheduled time ascending then UUID; future days are absent from active
feed, but remain available through explicit pending/all filters and Calendar.
Recommendations sort by persisted viewer score descending then UUID; collaborations by
creation time descending then UUID. A database UNION projects normalized ordering keys,
counts and slices a 50-item page, then loads only that page's domain rows. No full-history
Python sort/load occurs. Page requests use `?page=N`; ordering is deterministic for
unchanged data. Workspace-first locking keeps index and cards consistent per request.

Endpoints:
- `GET /api/v1/daily-tasks/matching/<uuid>/`
- `POST /api/v1/daily-tasks/matching/<uuid>/status/`
- `GET /api/v1/daily-tasks/collaboration/<uuid>/`
- `POST /api/v1/daily-tasks/collaboration/<uuid>/status/`

Recommendation detail locks/reloads one authorized pair, evaluates with the viewer's
current base profile and updates that row through the existing lifecycle. NEW becomes
SEEN; REJECTED/DONE remain unchanged on opening. Actual viewing records the current-score
baseline and clears improvement. No bulk generation occurs. Strict manual status payloads
accept seen/rejected/done without scoring; DONE requires current ownership of both sources.
Collaboration detail/actions reuse the finalized participant service: only recipient NEW
opens become SEEN, accepted/rejected remain, requester cannot change recipient status,
and invalid requests deny mutation. No contact/private operational fields are returned.

### Weak matching preview

`POST /api/v1/customers/<uuid>/matches/weak/` and
`POST /api/v1/property-files/<uuid>/matches/weak/` reuse live Matching source authorization,
viewer base profile, temporary overrides/hard constraints, restricted serializers and
formula. They return only eligible scored pairs below the effective threshold; ineligible,
hard-constraint failures and recommended pairs are omitted. Scores sort descending then
UUID, pages contain 50, and more than 1,000 coarse candidates rejects the request before
scoring. Existing recommended-only `/matches/` behavior remains unchanged.

Preview writes no recommendation, work, alert, override or result; only an absent viewer
profile may be lazily initialized, without scheduling generation. Cross-owner consultant
rows carry the existing five-minute actor/Workspace/pair/ownership-bound collaboration
reference. Redemption uses the existing score-free CollaborationRequest path, revalidates
current ownership and deduplicates reverse/repeat requests without a fake recommendation.
Same-owner rows have no collaboration reference. Preview scores never enter collaboration.

Frontend, visit scheduling, negotiation/deals, commission,
chat and generic push/email/SMS notification delivery remain future work.

## Pass
"Pass" is cross-consultant collaboration around a candidate file/customer match.

It must preserve:
- Original file owner
- Original customer owner
- Participants
- Future transaction/commission attribution

Do not implement commission calculations until specified.

## Manual Tasks / Reminder + Calendar v1

ManualTask is the only persistence concept for personal one-time reminders. Calendar and
Daily Tasks are projections, not additional task/event models. Fields: UUID, protected
Workspace and owner, title (required, trimmed API input, maximum 200 characters), canonical
aware scheduled_for, is_all_day, pending/done/cancelled, created_at, updated_at and nullable
completed_at. No File/Customer relation, description requirement, recurrence or delivery job.
Workspace/owner are immutable. Application writes go through transactional services locking
Workspace -> User -> task. Direct save/update_fields/bulk/delete bypasses are guarded.
Internal raw SQL remains a maintenance boundary: database checks enforce status/completion
consistency, while cross-table owner/Workspace validation belongs to model/services.

Only done has completed_at. Entering done sets now; leaving done clears it. All transitions
are reversible and repeating the same status preserves timestamps. GET never changes a
ManualTask's status. No hard-delete API exists. Workspace and owner FKs use PROTECT.
Indexes cover (workspace, owner, status, scheduled_for) and (workspace, owner, scheduled_for)
for pending feeds and all-status Calendar ranges; UUID breaks time ties.

### Jalali boundary and timezone

**All customer-facing Homban date/calendar presentation is Persian/Jalali. Gregorian dates
are internal implementation details only.** This applies now to ManualTask, Calendar and
all Daily Tasks card/detail/action timestamps, including Matching and Collaboration. Raw
created_at/updated_at/last_evaluated_at values are replaced by corresponding *_display
fields. Other historical APIs are unchanged until their presentation contracts are revised.

`common/jalali.py` centralizes parsing, conversion, date ranges, local-day boundaries and
formatting. Dependency `jdatetime==6.1.0` (with jalali-core 1.0.0) supplies calendar conversion,
leap-year validation and arithmetic; no calendar algorithm is implemented locally.
See https://pypi.org/project/jdatetime/ . Database datetimes remain canonical and aware;
no duplicate Jalali strings are persisted. The configured business timezone is settings
TIME_ZONE (currently Asia/Tehran), independent of request-local timezone activation.
No user timezone setting is added. Historical nonexistent/ambiguous timed inputs are
rejected in Persian; all-day boundaries use the earliest real instant of the local day.

Input dates are strictly Jalali YYYY/MM/DD, times HH:MM (24-hour). ASCII, Persian and Arabic
Indic numerals are accepted and normalized. Machine parsing fields jalali_date/time use
ASCII digits; date_display and every timestamp *_display use Persian digits. Month names:
فروردین، اردیبهشت، خرداد، تیر، مرداد، شهریور، مهر، آبان، آذر، دی، بهمن، اسفند.
Weekdays: شنبه، یکشنبه، دوشنبه، سه‌شنبه، چهارشنبه، پنجشنبه، جمعه.
Actual weekdays are calculated by the adapter, not inferred from example labels.

Responses expose id/title/status, jalali_date/date_display/weekday/month_name/time,
is_all_day/is_overdue, created_at_display/updated_at_display/completed_at_display.
They omit scheduled_for and raw Gregorian/ISO timestamps entirely. For all-day work,
time is null. Timed tasks require time; all-day input omits time and rejects a supplied time.
PATCH may preserve existing date/time. Switching timed -> all-day clears the time;
all-day -> timed requires an explicit time. Title-only PATCH preserves the schedule.

Today is the current business-timezone Jalali day, never UTC's date. Timed pending tasks
are overdue strictly before now; all-day pending tasks become overdue only once their
local Jalali day has ended. Completed/cancelled tasks are never overdue. Daily active feed
includes only pending tasks due today or overdue; future days do not appear there.

### APIs and calendar

- POST/GET `/api/v1/manual-tasks/`
- GET/PATCH `/api/v1/manual-tasks/<uuid>/`
- POST `/api/v1/manual-tasks/<uuid>/status/` with status only
- GET `/api/v1/calendar/tasks/?from=1405/07/01&to=1405/07/30`
- GET `/api/v1/daily-tasks/manual/<uuid>/`
- POST `/api/v1/daily-tasks/manual/<uuid>/status/`

Create accepts title, jalali_date, optional time and is_all_day (default false). PATCH accepts
only those mutable fields. Owner/Workspace/status/timestamps/Gregorian input and unknown
fields are rejected. List defaults pending; status=all|pending|done|cancelled and optional
inclusive Jalali from/to bounds are supported. Calendar requires both bounds, defaults all,
and permits at most 366 inclusive Jalali days. Reversed/invalid/leap-day ranges fail.
Both return flat task rows inside conventional count/next/previous/results pagination,
50 per page, scheduled time ascending then UUID. Frontend groups by Jalali date. SQL handles
range filtering and pagination; the three-way Daily Tasks UNION likewise hydrates only its
50-row page. No recommendation scoring or generation occurs during list/calendar reads.

### Future frontend contract

Frontend is not implemented. It must use one centralized Jalali utility and one Jalali-aware
picker, Persian locale/weekdays/months, RTL, Persian display digits and a 24-hour clock.
Form values use Jalali YYYY/MM/DD. Native Gregorian input type="date", Gregorian grids,
default JavaScript Date.toLocaleDateString()/weekday rendering, English month/day names,
ISO strings shown in forms and calendar-type toggles are forbidden. Any JS library must be
reviewed when the frontend is built; no JS dependencies are added by this backend feature.
Recurrence, external calendars, push/SMS/email/browser notification delivery and Celery
reminder delivery remain unimplemented.

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

## Notification Center v1

`apps.notifications.Notification` is the shared personal in-app event summary, separate
from Daily Tasks and source-domain state. UUID, protected Workspace/recipient, kind,
Persian title/message, safe internal action_url, optional source_type/source_id, event_key,
nullable read_at and canonical created_at are the only stored fields. No payload JSON,
score, profile settings, contact, address, notes or media are copied. Source metadata is
not a GenericForeignKey and reads never load source objects. Existing deep-link endpoints
remain responsible for current source authorization, including reassignment.

Kinds: match_recommendation and collaboration_request have producers. chat_message,
import_review and voice_review are reserved valid service values, with no such producers.
`publish_notification` is the one trusted internal publication entry point; it derives
Workspace from a reloaded recipient under Workspace -> User locks. Inactive, moved or
workspace-less recipients cause a no-op `(None, False)`, never manager fallback.
Customer requests cannot publish, select recipients or change event content.

The database enforces unique (recipient, event_key) and valid kind. Workspace consistency,
field lengths, immutable summary/reference fields and internal path validation are checked
in the service/model. URLs accept only slash-delimited internal route segments, no external
host, query/fragment, encoded slash, dot traversal or scripts. Normal direct/partial/bulk
writes and deletion are guarded; trusted base QuerySet/raw SQL maintenance is outside
application guarantees. Recipient/Workspace history uses PROTECT.

Publication and the authoritative source transition commit in the SAME transaction.
An insertion failure rolls back the source transition (and generation cursor); the existing
background retry/recovery can retry the whole operation. There is no notification queue,
outbox, delivery worker or GET-time repair scan. Workspace serialization plus the unique
DB key makes concurrent normal duplicate publication return one row and one created flag.
First publication wins: retry does not replace content or reset read state.

Recommendation creation publishes once to its viewer. Only the existing lifecycle's actual
rejected/done -> new reactivation publishes again. The shared lifecycle marks that transition;
generation publishes after its batch write with the durable work ID in the event key.
Direct single-pair lifecycle commands use the persisted last_evaluated_at transition marker,
not a fresh publication timestamp. Created events use a fixed recommendation-ID key. No new
reactivation definition, score-change inference or recommendation counter is introduced.
Ordinary evaluation, seen score improvement, detail opens and manual actions emit nothing.
Two owner-viewers retain independent notifications, never sibling scores or identities.
Batch publication stays within existing 25-pair/50-viewer bounds; ordinary refreshes add no
notification queries. Notification insertion is synchronous within the background chunk.

Collaboration publication bridges the existing one-time CollaborationEvent in the creation
transaction, keyed by request ID + created. Only the original recipient is notified. Duplicate,
reverse, status/open and restoration paths do not emit events or notifications. Text is generic
Persian and contains neither requester contacts nor source details. No second event lifecycle
is introduced. Historical events are not backfilled by GET or this schema migration.

Read state is only read_at: null means unread. Explicit read/unread commands are reversible
and same-state idempotent. Read-all updates only the locked recipient's currently unread rows
in one SQL UPDATE and reports affected_count. Reading a notification never marks a source
seen, and opening a source never marks its notification read. Notifications are not inserted
into Daily Tasks, and the two unread/action counts are intentionally independent.

Endpoints:
- GET `/api/v1/notifications/`: status=unread (default), read or all; optional kind; page.
- GET `/api/v1/notifications/<uuid>/`: no mutation.
- POST `/api/v1/notifications/<uuid>/read/`: empty object.
- POST `/api/v1/notifications/<uuid>/unread/`: empty object.
- POST `/api/v1/notifications/read-all/`: empty object, no optional kind filter in v1.
- GET `/api/v1/notifications/unread-count/`: efficient COUNT, no source loading.

List ordering is created_at descending then UUID descending, DB-paginated at 50.
Indexes cover recipient/read_at/created_at and recipient/kind/created_at; the unique key
also begins with recipient. API output contains id/kind/title/message/action_url/is_read,
created_at_display, created_date, created_time and read_at_display using common.jalali.
No raw canonical dates, event keys, recipient/Workspace internals or source payload appear.
Jalali dates, Persian display names/digits and 24-hour time reuse the centralized adapter.

Chat, push/email/SMS, device tokens, delivery receipts, preferences/quiet hours, broadcasts,
importer/crawler, voice review, Draft/Staging and Deal workflows are not implemented here.
