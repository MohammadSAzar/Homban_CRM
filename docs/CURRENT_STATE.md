# Homban Current Implementation State

This document describes workspace-scoped authentication, organizational user management,
location configuration, Range Management, PropertyFile and Customer APIs, and PropertyFile/Customer
domain foundations on 2026-09-24.

## Repository
Root folder:
```text
HombanCRM
```

Git is initialized and commits already exist.

## Runtime
- Python 3.10.11
- Django 5.2.x
- Django REST Framework installed
- djangorestframework-simplejwt 5.5.1 (PyJWT 2.14.0) installed
- django-filter installed
- python-dotenv installed
- mysqlclient installed
- pytest installed
- pytest-django installed
- pytest-cov installed

## Database
MySQL is configured.

Development database:
```text
homban_db
```

A dedicated MySQL application user exists.

A test database permission was configured for:
```text
test_homban_db
```

Secrets/passwords are stored in `.env` and must not be committed.

## Settings
Settings are split approximately as:
```text
config/settings/
├── __init__.py
├── base.py
├── development.py
└── production.py
```

Development settings use MySQL and the dedicated pytest test database configuration.

## Existing apps
Known apps:
- `organizations`
- `accounts`
- `locations`
- `ranges`
- `properties`
- `customers`

## Workspace
A `Workspace` model exists.

Known concepts include:
- UUID primary key
- name
- slug
- customer type
- active state
- region mode
- timestamps

Buyer/customer types include:
- agency manager
- range manager
- consultant

## User
Custom Django user model exists.

Known fields/concepts:
- UUID primary key
- workspace FK
- role
- `is_workspace_owner`
- phone number
- timestamps

Roles:
- agency manager
- range manager
- consultant
- secretary
- admin

Company-level/support users may eventually exist without customer workspace membership.

Customer usernames now have database uniqueness on `(workspace, username)`, not
globally. Django's `USERNAME_FIELD` is `id` (UUID); customer login still uses username
plus password within resolved workspace context. Workspace-less names may repeat,
but customer access is denied. Internal staff authenticate by UUID only. See ADR 0003.

### Legacy owner role review
Accounts migration 0002 adds `is_workspace_owner` and removes the legacy `owner`
role choice without converting data. The development database was checked on
2026-09-15: this migration is applied, and there are zero users (zero legacy owner
rows). No data migration is needed for this baseline or an empty fresh installation.
Other databases were not inspected. Before upgrading any database containing legacy
owner rows, determine each user's operational role explicitly; ownership alone does
not determine that role. Existing migrations were left unchanged.

## Locations
Models exist for:
- City
- Region

Known behavior:
- Workspace-owned
- City -> Region relationship
- Manual / Divar source concept
- External ID/slug fields
- Workspace consistency validation
- Indexes/uniqueness constraints

## Ranges
Models exist for:
- Range
- RangeMembership

Known behavior:
- Range belongs to workspace
- Range manager relation is nullable and uses `SET_NULL`
- Range may have region restrictions
- Range may have sale/rent/all activity scope
- Consultant membership is optional
- Membership enforces consultant role and workspace consistency

A previous `PROTECT` relation on range manager caused Workspace deletion to fail; this was changed to `SET_NULL`.

Range-region assignments are validated server-side by an `m2m_changed` `pre_add`
receiver registered through `RangesConfig.ready()`, using a reusable queryset
validator. Both forward and reverse related-manager `add()`/`set()` operations are
covered, including primary-key assignments. Cross-workspace links raise a Persian
`ValidationError`; failed set operations preserve existing links transactionally.
No schema change is required.

This is an ORM relationship-manager guard, not a database constraint. Direct writes
to the automatic through table (including bulk writes/raw SQL) bypass it and are
unsupported. Changing an already-linked object's workspace is not protected by this
assignment hook. Broader workspace immutability and bulk-write policies remain unresolved.

## PropertyFile domain foundation
`properties` adds PropertyFile, PropertyFileImage and PropertyFileValuableReason.
Files include assignment, required Region/City, positive area and required bedrooms, address, separate owner/visit
contacts, description, structured characteristics and nullable facilities, sale/rent
Decimal prices in تومان, status, source, valuable flag, code and aware timestamps.
Images are ordered opaque references; each valuable reason has its own child row.
The full UUID-derived code is stable and database-unique. Model validation and database
checks protect the numeric and transaction invariants; relationship validation rejects
foreign-workspace assignments and City/Region mismatches. Referenced Regions cannot
change City inconsistently. Workspace/User/City/Region deletion is protected when files
reference them. No ordinary hard-delete operation is added.

An atomic internal service creates files and children together. New migration
`properties/0001_initial.py` creates only the new models, indexes and constraints;
old migrations are unchanged. No PropertyFile Matching, frontend, media storage,
imports or crawlers are implemented. See DOMAIN for defaults and integrity boundaries.

### Canonical PropertyFile completeness
Region, positive area and nonnegative bedrooms are required. Sale requires total_price;
rent requires deposit_amount and monthly_rent (zero allowed). Incompatible amounts
must be NULL. Stored sale price_per_square_meter is system-derived by flooring the
exact total_price/area ratio to whole millions of تومان, and is read-only to clients.
Normal full/partial model saves recompute from the resulting stored fields; rent clears
it. Newly selected Regions must be active; retained inactive links remain valid.
No permissions, read query shapes, Customer requiredness or Workspace policy changed.

Migration properties/0002_canonical_completeness validates all existing rows before
DDL, stops with category counts on incomplete/inconsistent data, and deterministically
backfills valid sale prices before enforcing NOT NULL/CHECK constraints. It fabricates
no business values and leaves old migrations unchanged. The development database on
2026-09-24 has no properties tables: properties/0001_initial is unapplied. Therefore
there are no existing PropertyFile rows to repair; development migrations remain
unapplied. Test migrations cover legacy valid/incomplete rows and deterministic reruns.
Incomplete future ingestion belongs in a future Draft/Staging layer, not canonical
PropertyFiles. No ingestion, Draft/Staging or Matching was implemented.

Verification: 189 focused PropertyFile cases passed across development runs, including
27 new completeness/migration cases. The full regression passed 740 tests with 99%
apps/common statement coverage. Django check and migration drift check pass. Existing
list/detail query-count assertions remain at 3/4 queries respectively. Schema changes
were exercised in the test database, not applied to development.

## Customer domain foundation
Customer, CustomerRegionPreference and CustomerValuableReason are implemented in
`apps/customers`, with initial migration `customers/0001_initial.py`.
Buyer/tenant money remains separate in تومان. Buyer budget_status is nullable with
the confirmed cash/cash_plus_property Persian choices; tenants cannot use it.
Models include explicit name/mobile/notes, assignment, code/status, required area
bounds and bedrooms, optional age bounds, explicit/all-Region geography and
customer-specific valuable reasons.
SQL CHECKs protect numeric/bound/type invariants. Model and forward/reverse M2M guards
protect workspace integrity and reject new inactive Region links. Existing inactive
links may remain. Region references and business parents are protected from deletion.
Atomic internal services create aggregates and replace preferences. The Customer REST
API now authorizes these operations; frontend, Matching and Deal logic remain future work.
PropertyFile remains unchanged by the Customer API feature.

### Canonical Customer completeness
Migration `customers/0002_canonical_completeness.py` requires min_area, max_area and
bedrooms, buyer budget, and both tenant financial amounts. Zero is valid; bounds and
buyer/tenant separation are enforced. Buyer budget_status stays nullable with no
forced choice. all_regions defaults to False: explicit geography requires one or
more Regions; True means all currently active Regions in this Workspace with no
stored links/backfill. Ambiguous payloads fail. Geography and financial transitions,
scalar fields, assignment and reasons are atomic. Model partial saves and direct
M2M/through writes preserve geography; bulk/raw writes remain unsupported.
Migration preflight rejects incomplete/zero-preference legacy rows rather than
inventing values. Development inspection found no Customer tables/rows, so no data
repair was needed. The migration remains an explicit deployment step.
PropertyFile behavior and Customer permissions are unchanged. Incomplete extraction
belongs to future Draft/Staging, which is not implemented.
Verification: 765 tests pass (740 existing plus 25 new cases), with 99% overall
apps/common coverage. Customer-focused checks passed, including migration refusal,
requiredness, geography transitions/deletion guards, partial saves and aggregate
rollback. List/detail query counts remain 3/4; Django checks and migration-drift
checks pass. No PropertyFile code or permission policy was changed.

## Tests
### File/Customer integration audit
The documented CRM field sets and approved Google Sheets concepts are represented,
including operational notes, contacts, address/images and multi-region preferences.
Matching inputs are a subset of these records, not the whole CRM domain. Current
workspace/FK/unique indexes are sufficient to begin API and Matching query design;
numeric indexes should follow real query plans. Future APIs must enforce actor scope
and contact-field visibility; Matching still needs explicit null/tolerance/scoring
rules. Neither is provided by model validation alone.

The audit reproduced and fixed two partial-save integrity gaps: PropertyFile City/
Region mismatch and CustomerRegionPreference cross-workspace links when unsaved
in-memory changes were excluded by update_fields. Both now validate the resulting
stored relationships. Regression tests cover each FK name and its _id form, rejected
writes preserving history, and valid updates. No schema/index change is needed.
Audit verification: 533 tests pass (525 existing plus 8 regression cases), with 99%
apps/common statement coverage. Django check passes and no migration drift exists.

All six apps use `tests/` packages with correctly named `__init__.py` files.
Customer verification: 525 tests pass (468 existing plus 57 new), with 99% overall
apps/common statement coverage and 100% for Customer models/services/guards.
Django check passes; migration dry-run reports no drift. Tests cover buyer/tenant
money and budget status, nullable bounds, SQL constraints, assignment, Region M2M
guards in both directions, inactive retention, atomic rollback, reasons, codes,
status preservation and protected deletion. The full regression ran once via coverage.
PropertyFile verification: 468 tests pass (395 existing plus 73 new), with 99%
apps/common statement coverage, including test/migration modules; properties models
and services have 100% statement coverage. Django check passes and the migration
dry-run reports no drift. New tests cover sale/rent storage, tenant/location and
assignee validation, numeric SQL constraints, nullable facilities, contacts/notes,
status preservation, code stability/uniqueness, child ordering/uniqueness, atomic
creation rollback, protected deletion and referenced-Region City changes.
`pytest.ini` collects `test_*.py`; there are no conflicting app-level `tests.py` files.
The suite includes the original authentication/baseline cases, updated for explicit
workspace context, plus workspace identity and resolver coverage.

Tests already cover at least:
- Workspace creation
- User belonging to workspace
- Region cannot use a city from another workspace
- Workspace deletion regression around protected range manager relationship
- Deletion assertions use UUIDs preserved before deletion
- Same-workspace range-region assignment in both relation directions
- Cross-workspace rejection and rollback for add/set/set(clear=True)
- Repeated assignment and removal/clearing of region links

Authentication API tests cover login, invalid credentials, current-user response
allowlisting, anonymous denial, current database role/workspace state, inactive users
and workspaces, removed membership/deleted users, refresh rotation/reuse rejection,
invalid/expired/wrong-type tokens, missing/invalid identity claims, and required fields.
The existing suite does not comprehensively cover all model rules or future role policies.
New coverage checks cross-workspace namesakes, database duplicate rejection and
membership moves, wrong-workspace credentials, workspace-less UUID identity,
createsuperuser/admin compatibility, development header selection, configured hosts,
unknown/inactive workspaces, disabled fallback, conflicting selectors, and untrusted hosts.

## API and deployment status
Customer endpoints:
- `GET/POST /api/v1/customers/`
- `GET/PATCH /api/v1/customers/<uuid>/`

Agency managers manage workspace Customers, consultants their own, and range managers
Customers of consultants across all their active managed Ranges. No ownership/staff
bypass or general cross-owner browsing exists. Future Matching visibility is separate.
Creation/reassignment requires an active scoped consultant; workspace/code/timestamps
remain server-owned. Contact data is scoped before serialization. Compact lists include
name; mobile, description, preferred Regions and reasons appear in authorized details.
PATCH replaces supplied Region/reason arrays transactionally; omission preserves them
unless switching to all_regions=True. Empty reasons clear; empty explicit geography
is rejected. Existing inactive Region links may remain, but new ones are rejected.
No hard delete is introduced. Domain validation is reused; canonical requiredness
is documented above.

django-filter supports type/status/assignee/preferred Region, area requirement bounds,
bedrooms, buyer budget, tenant deposit/monthly rent budgets, budget status and valuable
flag. Pagination uses 50 records with stable creation/UUID ordering. Reads join assignee
and workspace; only details prefetch scoped Regions and reasons. See DOMAIN and
PERMISSIONS for exact filtering and access semantics.

Customer API verification: 705 tests pass (620 existing plus 85 API cases), with 99%
apps/common statement coverage. Django check passes; migration dry-run reports no
changes. Query regression tests enforce 3 queries for lists and 4 for details across
all authorized roles as related data grows. Tests cover scope/contact isolation,
active-Range unions, assignment, filters, immutable fields, financial/bounds validation,
inactive Region retention, aggregate rollback, reasons, auth state and pagination.
No Customer schema, PropertyFile code or existing tests were changed.

PropertyFile endpoints:
- `GET/POST /api/v1/property-files/`
- `GET/PATCH /api/v1/property-files/<uuid>/`
- `POST/PATCH /api/v1/property-files/<uuid>/images/` (append reference / reorder all)
- `DELETE /api/v1/property-files/<uuid>/images/<image_uuid>/` (reference removal only)

Agency managers manage workspace files; consultants their own files; range managers
files of consultants in all their active managed Ranges. Zero active Ranges gives no
scope; the User Management exactly-one-Range rule is unchanged. There is no general
cross-owner browsing, even for owners. Secretary/admin users have no access. Future
Matching will own restricted candidate output. Details contain contacts only within
authorized scope; compact lists omit contacts and free-text operational data.

Creation/reassignment uses active scoped consultants. Workspace/code/source/timestamps
are server-owned. PATCH updates existing fields/status and atomically replaces reasons
when supplied. Image references can be appended, removed or reordered with a complete
UUID list; no file DELETE, uploads or media fetching exists. Domain validation and
protected history remain unchanged. django-filter narrows workspace/scoped lists by
type/status/assignee/location/area/bedrooms/sale price/source/valuable flag.

Property policies, serializers, filters, views and transactional API services live in
apps/properties, alongside the unchanged internal domain-creation service. Reads join
display relations; list pages omit child collections and detail prefetches them.
No schema changes, migrations or new dependencies are required. Matching remains future work.

PropertyFile API verification: 620 tests pass (533 existing plus 87 API cases),
with 99% apps/common statement coverage. Django check passes and no migration drift
exists. Query regression tests enforce 3 queries for lists and 4 for details across
authorized roles as related records grow. The existing JWT lifetime test now fixes
the token clock during issuance, preserving exact lifetime assertions without
changing authentication behavior or settings.

Range Management endpoints:
- `GET/POST /api/v1/ranges/`
- `GET/PATCH /api/v1/ranges/<uuid>/`
- `GET /api/v1/ranges/<uuid>/consultants/`
- `PUT/DELETE /api/v1/ranges/<uuid>/consultants/<user_uuid>/`

Only agency managers modify structural fields: name, manager (UUID/null),
activity_scope, regions (UUID list), is_active. PATCH is partial; Range PUT/DELETE
are unavailable. Ownership grants workspace-wide read access, not structural writes.
Range managers read assigned Ranges; consultants read their membership's Range.
Non-owner secretary/admin users have no Range API access. Inactive Range reads
retain this scope. Rosters are separate paginated minimal user summaries for
agency managers, owners, and assigned range managers.

Membership PUT assigns an unassigned consultant, or moves an existing membership
with explicit `from_range` matching the current Range UUID. Same-destination PUT is
idempotent. Relationship DELETE removes membership only. Agency managers can move
consultants; range managers can add unassigned/remove own consultants in exactly
one active managed Range. Workspace-owner consultants remain protected targets.
User Management and Location API behaviors are unchanged.

Policies, serializers, transactional services, views, URLs, and API tests live in
apps/ranges. Workspace locking follows the existing management-service order.
Read queries join manager, prefetch Regions, and annotate consultant counts without
fetching unbounded rosters. Lists/rosters paginate at 50, with stable name/UUID or
username/UUID order. Query tests cover fixed Range list/detail/roster costs.

No schema migrations or dependencies were needed. Range.manager remains nullable
and non-unique: multiple Ranges per manager are permitted by existing schema, while
ambiguous active ranges block range-manager membership writes. This unresolved
cardinality is documented for future product review, without a uniqueness migration.
New Region constraints require active Regions/Cities in the workspace; existing
inactive constraints may be retained and are visible to management readers.
Consultants see active constraints only, with has_region_constraints preserving
the distinction between hidden constraints and unrestricted geography.
Range deactivation preserves all relationships and user states; no hard delete exists.

Location configuration endpoints:
- `GET/POST /api/v1/cities/`
- `GET/PATCH /api/v1/cities/<uuid>/`
- `GET/POST /api/v1/regions/`
- `GET/PATCH /api/v1/regions/<uuid>/`
- `GET/PATCH /api/v1/location-settings/` (`region_mode` and Persian display)

City input is name and optional is_active. Region input additionally requires city
(UUID) on creation, and permits same-workspace city changes on PATCH. PATCH is
partial; PUT and DELETE are unavailable. Workspace, source, external ID/slug, and
unknown write fields are rejected. External identifiers are omitted from output.
Creation always uses manual source; external-source records are read-only.

All active customer members can read. Agency managers OR workspace owners can
manage, regardless of owner operational role. Managers see active/inactive records;
other users see active cities and active regions under active cities. Lists accept
`is_active=true/false`; regions also accept `city=<uuid>`. Filters never broaden the
actor's scope. Managers can configure inactive cities/regions for later activation.
Pagination uses 50 records with stable name/UUID ordering. Region queries join city
with select_related; query regression tests enforce 3 queries per nonempty list,
including JWT authentication and pagination count, independent of result size.

Location policies, read/write/filter serializers, and transactional mutation services
live in apps/locations. Services recheck current management eligibility while locking
the workspace, actor, and affected rows. Existing model uniqueness and workspace
validation are retained. The Region policy migration is described below; no dependencies were added.

Workspace Region policy now permits custom/divar as the only operational values;
NULL is the default incomplete-setup state. One taxonomy/collection belongs to the
Workspace, shared by every Range/user, with no cross-workspace Region sharing.
The settings API reads NULL and only accepts custom/divar writes. Existing agency
manager OR workspace-owner configuration authority is unchanged.
Migration organizations/0003_workspace_region_policy makes the field nullable,
converts legacy none to NULL, preserves custom/divar and adds a database CHECK.
Unknown stored values stop migration for review; no business mode is invented.
The development data check on 2026-09-24 found zero Workspace rows. Migration tests
exercise legacy rows and preserve their City/Region data. Old migrations are unchanged.
Verification: 180 focused tests and all 715 regression tests pass, with 99% apps/common
statement coverage. Django check passes and migration dry-run reports no drift.
The migration was exercised in the test database; applying it to development remains
an explicit deployment step. PropertyFile/Customer requiredness is unchanged.
Mode changes preserve all data and trigger no network requests or synchronization. City deactivation does not rewrite Region active flags.
Divar synchronization remains future work owned by a separate integration service.
Location tests cover role/owner access, non-destructive mode changes, source protection,
payload allowlists, workspace isolation, uniqueness, city reassignment, deactivation,
visibility, filtering, pagination, query counts, and current authentication state.
The approved Location baseline contained 91 Location API cases and 185 earlier
tests (276 total), with 98% apps/common statement coverage including test/migration
modules. Range API verification extends that baseline.

Organizational user management endpoints:
- `GET /api/v1/users/`: scoped list, ordered by username/UUID, 50 users per page
- `POST /api/v1/users/`: create user, with optional agency-selected `range_id`
- `GET /api/v1/users/<uuid>/`: scoped user detail
- `PATCH /api/v1/users/<uuid>/`: allowed profile fields, status, and consultant range assignment

`PATCH {"is_active": false}` deactivates and `true` reactivates eligible targets.
PUT and DELETE are not exposed. Role/username/workspace/ownership are immutable.
Agency managers see their workspace; range managers see themselves and consultants
in exactly one active managed range. Consultant, secretary, and admin roles are denied.
Self, agency-manager, and workspace-owner targets are read-only. Range-manager
creation assigns consultants automatically; agency managers may assign/clear consultant
membership and optionally assign a new range manager to an unoccupied active range.
Sensitive lifecycle operations remain a future explicit administrative workflow.

Policies live in `apps/accounts/policies.py`; multi-model mutations are transactional
functions in `services.py`. Dedicated input serializers reject unknown fields;
output serializers allowlist profile and scoped range data. Password validation uses
Django's configured validators; password storage uses `set_password()`. No model or
migration changes were required; existing workspace uniqueness and RangeMembership
constraints remain in use.

Read queries use select_related for consultant membership and prefetch_related for
managed ranges. Query regression tests enforce fixed JWT-authenticated list counts
(4 for agency managers, 5 for range managers) and detail counts (3 and 4 respectively).
Tests cover role/target restrictions, cross-workspace ID guessing, range consistency,
protected payload fields, password handling, transactional rollback, pagination,
deactivation/reactivation, and created-user login alongside existing auth regressions.

Customer authentication endpoints:
- `POST /api/v1/auth/login/`: resolved workspace + username/password -> access and refresh tokens
- `POST /api/v1/auth/refresh/`: refresh token -> access and rotated refresh tokens
- `GET /api/v1/auth/me/`: current-user profile only

DRF defaults to database-backed customer JWT authentication and `IsAuthenticated`.
Login and refresh are public (no prior authentication required). All three paths
require an active user with membership in an active workspace. Workspace-less
company/control-plane users are excluded, including staff/superusers without membership.
Access requests load the user and workspace together. Tokens contain identity and
standard token metadata only; roles and workspace data are read from the database.
The profile allowlist is id, username, first_name, last_name, phone_number, role,
role_display (Persian), is_workspace_owner, workspace_id, and workspace_name.

Access lifetime is five minutes; refresh lifetime is one day, renewed on rotation.
SimpleJWT's blacklist app rejects previously rotated refresh tokens. Its bundled
migrations must be applied using `python manage.py migrate` in each environment;
accounts migration `0003_workspace_scoped_username` adds scoped username uniqueness
without deleting data. Schedule `python manage.py flushexpiredtokens`
for production housekeeping. Clients should serialize refresh requests; rotation is
not an absolute session-duration limit or a concurrent replay-prevention system.

Django's `/admin/` uses session authentication with UUID/password login for active
workspace-less staff (enter the UUID as 32 hex characters). Customer login resolves
workspace via `CUSTOMER_WORKSPACE_HOSTS`, or `X-Workspace-Slug` when the map is empty
and `CUSTOMER_ALLOW_WORKSPACE_HEADER` is explicitly enabled (development only).
Missing context fails closed; body workspace IDs are ignored. Refresh and `/me/`
continue to use UUID and current database membership without a workspace selector.
See [ADR 0003](decisions/0003-workspace-scoped-username.md) for migration and compatibility details.
Future workflow policies and the customer frontend remain future work. Production
deployment is incomplete (`ALLOWED_HOSTS` is empty). HTTPS, client token storage,
login rate limiting, and signing-key operations need deployment decisions. This
foundation uses Django's environment-backed secret as SimpleJWT's default signing key.

## Not yet implemented / not confirmed as implemented
Treat these as future work unless repository inspection proves otherwise:
- Full permission framework
- Matching engine
- Pass/collaboration workflow
- Task/calendar
- Chat
- Notifications
- Deals
- External import workers
- Voice/AI creation
- Customer-facing custom admin panel
- Company control plane
- Production deployment

## Important instruction
Before implementing new work, inspect the repository.

This document may lag behind code; the repository is authoritative for implementation state, while product/domain docs are authoritative for intended product behavior.

## MatchingProfile foundation
`apps/matching` now contains persisted per-user base settings with migration
`matching/0001_initial.py`. UUID/timestamps and OneToOne ownership have no duplicated
Workspace field. Authenticated customer users of every current role manage only their
own settings through GET/PATCH `/api/v1/matching-profile/` and empty POST
`/api/v1/matching-profile/reset/`. Creation is lazy and serialized using existing
Workspace -> User locking; reset is atomic. No list, owner-ID route or DELETE exists.
Defaults: area 25, bedrooms 15, building_age 10, region 10, parking 4, elevator 3,
storage 2, balcony 1; minimum_score 50; sale budget gate 0.80–1.20; monthly-rent
conversion 3,000,000 تومان per fixed 100,000,000 تومان deposit. Weights and money
use Decimal; weights need not sum to 100, but at least one must be positive.
Model/API validation and database checks enforce weight, score, ratio and conversion
invariants. Budget has no weight. Only settings fields are returned/writable.
No scoring, candidate search, hard constraints, temporary overrides, Match/results,
notifications, Tasks, ingestion or frontend were implemented. Production deployment
remains future work; the new migration is an explicit deployment step.
