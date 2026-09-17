# Homban Current Implementation State

This document describes the workspace-scoped customer authentication implementation on 2026-09-17.

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

## Tests
All four apps use `tests/` packages with correctly named `__init__.py` files.
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
Business record
APIs, role/object policies, and the customer frontend remain future work. Production
deployment is incomplete (`ALLOWED_HOSTS` is empty). HTTPS, client token storage,
login rate limiting, and signing-key operations need deployment decisions. This
foundation uses Django's environment-backed secret as SimpleJWT's default signing key.

## Not yet implemented / not confirmed as implemented
Treat these as future work unless repository inspection proves otherwise:
- Full permission framework
- User-management API
- File model/API
- Customer model/API
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
