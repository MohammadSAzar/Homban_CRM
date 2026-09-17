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
