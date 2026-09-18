# Homban Permissions

## Principles
Permissions are one of the most critical parts of Homban.

Authorization is evaluated in this order:
1. Authentication
2. Workspace isolation
3. Role
4. Organizational scope
5. Object ownership/assignment
6. Action
7. Field-level sensitivity

Never expose restricted data and merely hide it in the frontend.

Customer login must first resolve an active workspace, then validate credentials
only within it. Never fall back to a global username search. Development may select
the workspace by `X-Workspace-Slug`; deployed host configuration takes precedence.
This selector is not authorization. Inactive users/workspaces and workspace-less
users are rejected. Staff/superuser flags do not bypass customer membership checks.
JWT and `/me/` use UUID identity and current database membership; roles are not
trusted from claims. See [ADR 0003](decisions/0003-workspace-scoped-username.md).

## Agency manager
Scope:
- Entire workspace

Expected broad capabilities:
- Manage organizational structure
- Create/manage range managers
- Create/manage consultants
- Manage ranges
- Manage workspace regions/configuration
- View/manage files and customers across workspace
- Manage deals
- Delegate admin access
- Manage general-group availability
- Manage announcement posting permissions

Exact delete/archive policies will be defined per entity.

## Range manager
Scope:
- Own range for management actions

Within own range:
- Broad create/view/update/manage permissions as later specified

Outside own range:
- May view file/customer information needed for matching/pass
- Must not see restricted contact information such as phone numbers
- Must not manage records from other ranges unless a future explicit permission grants it

May create consultants for own range.

## Consultant
Scope:
- Own assigned operational records

Can:
- Create files/customers
- Automatically receive assignment to newly created manual records
- Manage records assigned to self, subject to future status rules
- View matching candidates belonging to other consultants as needed for pass/collaboration

Cannot:
- Access contact phone numbers or other sensitive contact data for records not assigned to self
- Manage another consultant's records by default

## Secretary
Not fully specified yet.

May receive delegated capabilities from agency manager.

Do not assume secretary has broad data access until specified.

## Admin
Product-level delegated workspace admin role.

Exact permission bundle should be configurable/delegated by agency manager rather than treated as unrestricted superuser.

## Workspace owner
`is_workspace_owner` identifies the purchaser/primary user.

It is not itself a job role.

Ownership may affect workspace-level settings and account administration but should not replace role/scope permissions.

## Organizational user API policy
This feature's explicit policy takes precedence over broad future capabilities:

| Actor | Visible users | Creation | Updates and activation |
| --- | --- | --- | --- |
| Agency manager | Entire own workspace | Range manager, consultant, secretary, admin | Same-workspace users except protected targets |
| Range manager | Self and consultants in exactly one active own range | Consultant automatically assigned to that range | Only those consultants, except workspace owners; no range selection |
| Consultant, secretary, admin | None through management endpoints | Denied | Denied |

Protected targets are the acting user, all agency managers, and workspace owners.
They may be viewed within the actor's scope but not edited/deactivated through this
API. Ownership and Django staff/superuser status never grant management permissions.
Self-service `/auth/me/` retains its existing read-only behavior.

Workspace is always derived from the authenticated user. Body fields cannot override
it; workspace headers/query parameters cannot change management scope. Foreign and
missing user UUIDs return the same 404 response for permitted actors. Range IDs are
resolved only in the actor's workspace; foreign/unknown/inactive ranges share a
generic validation error. An invalid/ambiguous range-manager scope allows only self
visibility and prevents consultant management.

Updates allow first_name, last_name, phone_number, is_active, and consultant range
assignment by agency managers. Username, role, workspace, is_workspace_owner,
password, Django flags, groups, and permissions are rejected on update rather than
silently ignored. Creation uses an explicit field allowlist; extra fields are rejected.
No hard delete or password-reset endpoint is included. Protected lifecycle operations
are deferred to an explicitly designed administrative workflow.

User/password/range writes are transactional services that recheck current actor
state and scope. Workspace-row locking serializes these management writes within a
workspace; row locks protect the actor, target, and selected range/membership where
needed. This is not a universal guarantee for arbitrary direct ORM writes elsewhere.

Responses include only id, username, first_name, last_name, phone_number, role,
Persian role_display, is_active, is_workspace_owner, consultant range (id/name or
null), and managed_ranges (id/name). No password/hash, email, Django permission
internals, backend identity, or token data is returned. Related ranges are scoped
to the same workspace even when reading inconsistent legacy relationships.

## Location API policy

All authenticated active customer users with an active workspace may read location
settings and available cities/regions in their workspace. Management requires
`role == agency_manager OR is_workspace_owner`. The owner exception applies to all
operational roles, including consultant/range manager/secretary/admin. Non-owner
range managers, consultants, secretaries, and admins have read access only.
Django staff/superuser flags do not grant location management capabilities.

Location managers may read inactive records. Read-only users see active cities and
active regions with active cities only, including on detail endpoints. Filters can
narrow this scope but cannot broaden it. Deactivation never deletes or cascades
changes to child records. The stored region active flag is independent of city state.

Workspace comes only from the authenticated user's current database membership.
Foreign object UUIDs and missing UUIDs return the same 404. Foreign/missing city
assignments share a generic validation error. Write serializers and services reject
workspace, source, external identifiers, and other fields outside their allowlist.
External-source records cannot be edited or deactivated through ordinary APIs.

Mutation services recheck current actor permission inside a transaction, locking
workspace before actor and affected records. This follows the existing management
write pattern; arbitrary ORM writes outside these services are not covered by it.
Mode changes are non-destructive and never invoke external synchronization.

## Sensitive fields
Examples:
- Owner phone
- Customer phone
- Contact person phone
- Potential future personal identifiers

Serializer/API output must be permission-aware.

Where a user lacks permission:
- Prefer omission of the field when practical
- Or explicit masked representation if UX requires it
- Never send the raw value to the frontend

## Cross-workspace isolation
This must be tested aggressively.

No user from Workspace A may:
- Retrieve Workspace B records
- Guess an ID and access Workspace B detail endpoint
- Update/delete Workspace B objects
- View Workspace B contact fields
- Create an object linked to Workspace B entities

## Future permission design
Prefer reusable permission/service/query-scope components over role checks duplicated across views.

Candidate abstractions:
- Workspace scoping mixin
- Role permissions
- Object scope policies
- Contact visibility policy
- Range scope service

Do not prematurely build a highly generic permission framework before real use-cases exist.
