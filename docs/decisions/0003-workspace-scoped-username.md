# ADR 0003 — Workspace-scoped customer usernames

## Status
Accepted for implementation on 2026-09-17; pending feature review.

## Decision
Customer login identity is Workspace + username + password. `User.username` is no
longer globally unique. An unconditional database `UniqueConstraint` on
`(workspace, username)` enforces uniqueness for non-null workspaces on MySQL.
Username comparisons retain the database's existing collation semantics; this
change does not introduce case-sensitive usernames or change normalization.

`User.id` remains the globally unique UUID and JWT identity. Django's
`USERNAME_FIELD` is now `id`; the customer serializer explicitly accepts `username`.
The custom authentication backend queries workspace and username together and
never searches all customer workspaces by username.

## Workspace resolution
`resolve_customer_workspace(request)` is the sole login context resolver:
1. Validate the request host through Django `get_host()` / `ALLOWED_HOSTS`.
2. Resolve a configured exact host from `CUSTOMER_WORKSPACE_HOSTS`, whose values
   are existing workspace slugs. Host names are lowercased and ports removed.
3. Only when the host map is empty and `CUSTOMER_ALLOW_WORKSPACE_HEADER=True`,
   accept `X-Workspace-Slug`. This flag is enabled in development settings only.
4. Reject missing/unknown/inactive workspaces, unknown hosts when a map exists,
   and a header that conflicts with a mapped host. Never use a body workspace ID.

The header is a development tenant selector, not proof of authorization. A valid
password for the selected workspace's user is still required. Deployment must
configure allowed hosts and the map; base/production settings disable the fallback.
A future subdomain resolver can replace this function without rewriting credential
validation. Do not trust forwarded host headers without a deliberate proxy policy.

Example development login:
```http
POST /api/v1/auth/login/
X-Workspace-Slug: agency-a
Content-Type: application/json

{"username": "ali", "password": "example-password"}
```

Host mapping example: `CUSTOMER_WORKSPACE_HOSTS = {"agency-a.example.test": "agency-a"}`.
The map is server configuration, not client input.

## Workspace-less users and compatibility
Keep the nullable workspace FK. SQL nullable composite uniqueness permits repeated
usernames among workspace-less rows; their unique identity is the UUID, not username.
They cannot obtain customer tokens, refresh them, or access customer endpoints.
For existing Django admin operations, the backend accepts UUID/password only for
active workspace-less staff. No company/control-plane endpoints or role policies
are introduced. Customer staff must use customer authentication, not this internal path.

Django admin's identity field uses the UUID's 32-character maximum: enter the UUID
as 32 hexadecimal characters without hyphens. Programmatic authentication accepts
UUID strings. `createsuperuser --noinput` now requires `--id`, `--username`, and
`--email`; supply the password through `DJANGO_SUPERUSER_PASSWORD`, not command text.
The existing user manager remains compatible with named arguments. Username-only
`authenticate()` and `get_by_natural_key()` calls must not be used for customers.
Changing the configured backend invalidates sessions tied to the former backend;
internal operators must sign in again.

Existing access/refresh tokens still resolve users by UUID. `/me/` and refresh
continue to read current membership/role from the database, without requiring a
workspace header. Login context selects credentials; this feature does not bind
tokens to hostnames. Future tenant-host routing of business APIs must also enforce
request-tenant and current-user membership agreement.

## Migration
Accounts `0003_workspace_scoped_username` removes global username uniqueness and
adds composite uniqueness without changing IDs, passwords, membership, or other data.
The development database had zero users when inspected; no cleanup is needed.
No data is deleted. Reversing the migration after creating duplicate usernames across
workspaces requires resolving those duplicates first; automatic destructive rollback
is not provided. MySQL DDL is not transactional, so deploy schema changes deliberately.
