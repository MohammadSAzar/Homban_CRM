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
│   └── ranges/
├── common/
├── config/
│   └── settings/
├── docs/
├── requirements/
├── manage.py
└── pytest.ini
```

Future apps may include:
- properties/files
- customers
- matching
- tasks
- deals
- chat
- notifications
- imports/integrations
- audit/logging

Do not create apps only because they are listed here. Create them when the domain boundary is ready.

## Settings
Settings are split into:
- `base.py`
- `development.py`
- `production.py`

Secrets belong in environment variables.

## Workspace isolation
Homban uses one codebase with multiple customer workspaces.

Every workspace-owned business entity must be isolated by workspace.

The backend must never rely on frontend filtering for tenant isolation.

Typical query flow:
1. Determine authenticated user's workspace
2. Scope queryset to workspace
3. Apply role/organizational scope
4. Apply object-level restrictions
5. Apply field-level serialization restrictions

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
