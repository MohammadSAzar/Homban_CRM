# Homban Current Implementation State

This document describes the implementation after the baseline cleanup on 2026-09-15.

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
The default suite contains 18 test cases, including parameterized cases.

Tests already cover at least:
- Workspace creation
- User belonging to workspace
- Region cannot use a city from another workspace
- Workspace deletion regression around protected range manager relationship
- Deletion assertions use UUIDs preserved before deletion
- Same-workspace range-region assignment in both relation directions
- Cross-workspace rejection and rollback for add/set/set(clear=True)
- Repeated assignment and removal/clearing of region links

API and permission test modules are placeholders; there is no API/permission test
coverage yet. The existing suite does not comprehensively cover all model rules.

## API and deployment status
Only Django's `/admin/` route exists. Application admin/view modules are placeholders;
business APIs, serializers, and the custom customer-facing frontend are not implemented.
DRF defaults to authenticated access, but workspace/role/object API policies are future
work. Production settings have an empty `ALLOWED_HOSTS`; deployment remains incomplete.

## Not yet implemented / not confirmed as implemented
Treat these as future work unless repository inspection proves otherwise:
- JWT authentication API
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
