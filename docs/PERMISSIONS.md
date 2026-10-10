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

## Range API policy

| Actor | Range reads | Structural writes | Membership writes |
| --- | --- | --- | --- |
| Agency manager | All own-workspace Ranges | Allowed | Add, explicit move, remove |
| Non-agency workspace owner | All own-workspace Ranges | Denied | Operational role only |
| Range manager | Assigned Ranges | Denied | Add unassigned consultants/remove own consultants, exactly one active managed Range |
| Consultant | Own membership's Range | Denied | Denied |
| Non-owner secretary/admin | Denied | Denied | Denied |

Inactive Range reads use the same scope. Rosters are visible to agency managers,
workspace owners, and the assigned range manager, not ordinary consultants.
Roster/manager summaries expose only id, username, first_name, last_name; no phone,
email, credentials, permission internals, or tokens. Nested manager, Region, roster,
and count queries also enforce workspace consistency for legacy invalid relations.

Only agency managers change name, manager, activity_scope, is_active, or regions.
Ownership and Django staff/superuser flags never grant structural write access.
Membership services retain protected-target checks: self and workspace-owner users
cannot be reassigned/removed through ordinary membership operations. Consultant role
and same-workspace membership are mandatory. Range-manager ownership grants broader
read access only; membership writes remain restricted to their one active Range.

The new relationship endpoints permit range managers to add unassigned consultants
and remove own consultants; `/api/v1/users/` permissions are unchanged. Other-Range
consultants and foreign/missing IDs are not revealed by membership writes. Agency
manager moves require an explicit matching `from_range`; changes are transactional.
Range.manager allows multiple Ranges per manager in the existing schema. That
cardinality is preserved; ambiguous active Range states block range-manager writes.

Existing inactive Region constraints are visible to agency managers, owners, and
assigned range managers. Ordinary consultants receive only active Regions under
active Cities plus `has_region_constraints`, which remains true for hidden constraints.
New Region assignments reject inactive Regions/Cities. Existing assignments can be
retained/removed. Range deactivation changes no memberships or child/user active flags.
There is no Range hard delete; relationship DELETE only removes RangeMembership.

All writes derive workspace from current authenticated membership. Strict payload
allowlists reject workspace/user privilege overrides. Services use the existing
workspace-then-actor locking order and recheck role, target scope, manager/member
relationships, and Region validity. Direct bulk/SQL writes outside these services
remain unsupported; no new database constraints or signals are introduced.

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
Region configuration is one Workspace-wide custom OR divar taxonomy, never per Range
or consultant. NULL denotes incomplete setup only; operational configuration rejects
none and NULL. Agency managers and workspace-owner purchasers (including range managers
and individual consultants) configure their own Workspace using the existing authority.
No unrelated operational permissions are broadened; all Regions remain Workspace-owned.

## Sensitive fields
### Customer API policy

Agency managers read/manage all valid Customers in their Workspace. Consultants
read/manage only their own assigned Customers. Range managers read/manage Customers
assigned to consultants in the union of all ACTIVE Ranges they manage. Zero active
Ranges gives no scope. This does not change User Management's exactly-one-Range guard.
Secretary/admin roles have no Customer API access; workspace ownership or Django
staff/superuser flags do not expand operational scope.

Consultants automatically assign new Customers to themselves and may only submit
their own assignee UUID. Agency/range managers must explicitly select an active
same-workspace consultant in their scope. Reassignment follows the same rule and
immediately changes visibility. Authorized managers may still manage existing records
of inactive consultants. All Customer statuses remain readable within actor scope.

Workspace is derived from authenticated server context. Missing, foreign-workspace
and out-of-scope Customer UUIDs return the same 404. There is no cross-owner browsing
surface: name/mobile/description are never returned outside authorized scope. Lists
include name, while mobile and notes are detail-only. Live Matching uses its
own restricted representation. Assignee summaries contain only UUID, username,
first_name and last_name; no phone, password or permission internals are exposed.

Strict payload allowlists reject workspace/code/timestamps and privilege overrides.
Services lock Workspace, current actor and Customer, recheck scope, and reuse domain
validation and preference services. Preferred Regions must belong to the same
Workspace (including their City); new inactive links are rejected, retained inactive
links remain visible. Preferences and reasons are replaced transactionally with the
parent update. all_regions=True stores no explicit links; False requires at least
one. Switching to all clears links atomically; switching to explicit requires supplied
Regions. This does not broaden actor scope or expose cross-owner data.
No Customer hard-delete endpoint exists.

### PropertyFile API policy

The general PropertyFile API is not a workspace-wide Matching browsing surface.
Agency managers read/manage all valid workspace files; consultants read/manage only
their own assigned files. Range managers read/manage files assigned to consultants
in the union of all ACTIVE Ranges they manage, with same-workspace membership checks.
Zero active Ranges gives empty scope; multiple active Ranges are valid here. The
exactly-one-active-Range guard for User Management automatic placement is unchanged.
Inactive/sold/rented/archived files remain accessible within the same authorized scope.

Secretary/admin roles have no access. Workspace ownership and Django staff/superuser
flags do not expand operational permissions. Domain assignment remains consultant-only,
so range-manager users cannot directly receive files. New assignment/reassignment
requires an active consultant in the actor's authorized scope. Existing files of an
inactive consultant remain manageable by an authorized manager without activating them.

Consultant creation auto-assigns self. Agency/range managers explicitly provide
assigned_to; consultants may supply only their own UUID. Agency managers may reassign
within the Workspace; range managers within their active-Range union. Reassignment
immediately changes visibility. Workspace is derived from current authentication,
never body/query/header authority. Out-of-scope/missing files and child IDs return 404.

Authorized detail responses include owner_name, owner_phone and visit_contact_phone.
List responses omit contacts, address, notes, images and reasons. There is no restricted
cross-owner detail/list response in this feature: out-of-scope readers receive no file.
Live Matching uses its own restricted candidate representation. Consultant
summaries expose only UUID, username, first_name and last_name.

Strict write allowlists reject workspace, code, timestamps, source and privilege fields.
Ordinary creation uses manual source. Transactional services lock Workspace before the
current actor and parent file, recheck role/scope and validate assignment/location through
the existing domain. Nested writes use the same parent scope; reason replacement and
image operations are transactional. No file DELETE endpoint exists.

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

### MatchingProfile settings
All active authenticated customer users in an active Workspace may GET/PATCH/reset
only their own base MatchingProfile, including agency managers, range managers,
consultants, secretaries and admins. No additional operational role restriction applies.
Workspace-less internal users are denied; owner/staff/superuser flags grant no bypass
or access to another profile. No profile-owner route or collection is exposed.
Ownership/workspace/timestamps and unknown fields are rejected in payloads; query
parameters cannot select another owner. Services recheck current database membership
and active state while holding Workspace/User locks. Settings disclose no other
user's data and do not change PropertyFile/Customer operational permissions.
Restricted live Matching candidate visibility is separate, as documented below.

### Live Matching v1
Matching POSTs require operational authorization on the fixed Customer/PropertyFile
source, rechecked against current database state. Agency managers retain workspace
scope, range managers the union of their active managed Ranges, and consultants own
sources only. Secretary/admin denial remains; owner/staff/superuser flags do not expand
scope. Out-of-scope/foreign sources return 404 without creating a profile.
After authorization, candidate discovery is intentionally Workspace-wide across
consultants and Ranges. Every candidate receives the same restricted Matching output,
including owned candidates. Customer names/mobile, all contacts/addresses/notes,
images, valuable reasons and assignee identity are omitted. No operational detail
serializer is reused. This grants no access to the corresponding operational detail API.
Viewer-owned base settings and validated in-memory overrides determine results.
Temporary «خط قرمز» and overrides never grant permissions or persist. No standalone
Workspace-wide browse endpoint exists. These live calls do not persist recommendations. See DOMAIN for
exact age applicability, criterion checks, pagination and the 1,000-candidate cap.

### Saved recommendation lifecycle
MatchRecommendation belongs to a consultant who owns at least one side of the pair
in the same Workspace. Agency/range-manager scope, ownership flags and staff/superuser
flags grant no extra saved-recommendation rights. Each owner of a two-consultant pair
may have an independent projection evaluated with their own base profile.
Manual actions are viewer-only and check current database ownership/account state.
Done requires ownership of BOTH sources; seen/rejected remain reversible. A seen
action is an actual detail-open event and records the score baseline without scoring.
Manual actions cannot supply score, validity, viewer, source IDs or evaluation metadata.

Losing both sources through reassignment does not block reassignment or delete history.
Reconciliation invalidates the viewer association while retaining manual status and
historical metadata. Current-owner query scopes exclude former owners immediately;
their guessed recommendation IDs fail closed and manual actions are denied. Retained
rows grant no access to newly unauthorized source/contact data. Internal services
return domain models, not customer-facing payloads; feed/detail callers must
use scoped queries plus restricted serialization. The Daily Tasks API below does so.
Automatic generation and participant-only collaboration are described below.
Operational and live Matching permissions are unchanged.

### Automatic recommendation workers
Source events are Workspace-scoped internal work. Only current consultant owners of
one or both sources are evaluated, each with their own profile; profile-change work
affects only that viewer. File/Customer changes may affect both owners. Range boundaries
do not restrict same-Workspace candidate discovery. No agency/staff/owner flag creates
third-party recommendations. Inactive or former viewers are invalidated, not deleted.
Active access checks current ownership immediately, even before queued reconciliation.
No source/contact data or profile settings are placed in queue payloads or outbox rows.
The batch persistence sink is private, reuses lifecycle transitions and runs under
Workspace/source locks; it is not an alternate API write path. Recovery is internal-only.
Consultants do not manage jobs, refresh modes or validity flags. Workers add no daily
feed, reminder/task, frontend or deal workflow. Collaboration has a separate boundary.

### CollaborationRequest v1
Only current active consultant owners of opposite sides of a same-Workspace active pair
may submit from their own saved recommendation or an authorized signed Live Matching
reference. Agency/range authority, Workspace owner, staff and superuser flags do not bypass
participation. Source reauthorization occurs under Workspace-first locking. Live references
expire after five minutes and bind the actor/Workspace/pair plus a keyed ownership
fingerprint; they disclose no other consultant identity, contacts or score.

The original requester and recipient share one unordered-pair request, including reverse
submissions and accepted/rejected history. Both see professional ID/username/name/role,
but never consultant phones. Only recipient controls seen/accepted/rejected; incoming new
detail opens mark seen. Requester cannot impersonate recipient. All inputs are strict.
No general collaboration listing, delete or unrestricted update exists; unrelated/foreign UUIDs fail closed.

Valid detail uses one restricted pair shape for both participants: no Customer name/mobile,
owner/visit contacts, addresses, notes, media or private reasons. No scores/profile/runtime
settings appear in any collaboration response or event. Collaboration never grants access
to another consultant's operational detail API. On reassignment/inactivity, history remains
but pair details are null and manual actions are denied immediately. Cross-Workspace
relationship changes deny the entire response. Restoration preserves recipient status.
One durable creation event is for the original recipient only; no repeat/restoration spam.
Daily Tasks reuses this authorization boundary. Frontend, external notification delivery and
visit/negotiation/commission/outcome workflows remain absent; personal reminders and in-app notifications are below.

### Daily Tasks / Suggested Program
All active customer roles in an active Workspace may use Daily Tasks for their own
ManualTasks. Only consultants receive Matching/Collaboration items and may use their
detail/actions. Other roles requesting those feed types receive no items; their detail
access remains denied. Agency/range authority, purchaser ownership and staff/superuser
flags never bypass these boundaries. Recommendations use current viewer ownership scope, including historical filters;
no sibling viewer score, status, profile or breakdown is accessible. Collaboration uses
original participant scope and current authoritative validity. Invalid participant history
retains professional identities/status but hides source data and denies response actions.
Incoming and sent cards are distinct; only the recipient changes collaboration status.

Recommendation detail recalculates only its own viewer's one pair with base settings and
records viewing. Manual actions cannot set scores, validity, sources, ownership or profile.
All recommendation and collaboration source representations remain restricted: no Customer
name/mobile, owner/visit contacts, address, notes, media, reasons or assignee internals.
Collaboration never exposes Matching scores/settings, even to an owner of either source.

Weak-preview endpoints retain operational source authorization and Workspace-wide candidate
scope of Live Matching. Only eligible below-threshold pairs are returned, using the same
restricted representation for owned and cross-owner candidates. No recommendation/work/event
is created by preview. Signed collaboration references retain the existing expiry, ownership
fingerprint and current participant revalidation; weak scores are not stored in collaboration.

### ManualTask / Calendar
Every active authenticated customer role may create/read/edit/change status only on their
own tasks in their current active Workspace. Hierarchy, purchaser ownership, staff and
superuser flags grant no access to anyone else's tasks. Workspace-less/internal users are
denied. Guessed foreign/non-owner UUIDs return not found. Owner and Workspace come from
current authenticated state under Workspace-first locking and cannot be overridden.
Manual task status changes use a dedicated strict endpoint; no arbitrary status PATCH or
delete exists. Calendar/list queries are owner- and Workspace-scoped before date filters.
Daily manual detail/status reuse these same services, without granting Matching access.
All timestamps on this surface use the centralized Persian/Jalali display boundary;
internal canonical datetime storage is never exposed as a calendar display field.

### Notification Center
All five active customer roles may list/retrieve/count and mark read/unread only their own
notifications in their current active Workspace. Workspace-less/internal users are denied.
Agency/Range hierarchy, purchaser ownership, staff and superuser flags grant no bypass.
Guessed foreign/non-recipient UUIDs return not found. Read-all is recipient/Workspace scoped.
No customer creation, editing, delete or archive endpoint exists. Domain producers alone
publish; recipient state is authoritatively rechecked under Workspace-first locking.

Persisted summaries are intentionally generic, without contacts, notes, addresses, media,
scores, profiles or other-owner identity. No source objects are loaded in notification reads.
A link grants no source access: the target endpoint revalidates current operational/viewer/
participant authorization. Notification read state is independent of source seen/status.
All dates use the shared Persian/Jalali boundary; event keys and internal ownership are hidden.

### Private Chat Phase 1
All five active customer roles may open private conversations with another active user in
the same active Workspace. Only the two current participants may list/read/send/mark read.
Agency/Range hierarchy, workspace ownership, staff and superuser confer no bypass.
Workspace-less users are denied; foreign, unrelated, or no-longer-valid conversation UUIDs
return not found. Both participant Workspace memberships and active states are checked on
every access. Deactivation/reassignment hides retained history rather than deleting it.
Clients cannot choose Workspace, sender, canonical participant slots, read markers or dates.
GET is non-mutating; explicit read changes only the caller's marker. No edit/delete exists.
Only approved professional identity and private chat content are returned to participants;
no phone/security/CRM fields. Generic recipient-only notifications omit message content and
reuse the existing independent read lifecycle. Chat dates use centralized Jalali presentation.

### Chat realtime delivery
The authenticated empty-body realtime-ticket POST reuses exactly the private Conversation
scope. A signed 90-second ticket selects only its issuing user, Workspace and Conversation;
it grants no hierarchy/owner/staff bypass. Browser origins require explicit allowlisting.
Connect and each delivered event recheck current database authorization through Chat services.
Inactive/moved users or invalid participants receive no Message payload. Only Message IDs
travel through the internal group; authorized sockets receive the existing allowlisted Chat
message representation, including text and Jalali display time. No JWT travels in the URL.
Tickets are short-lived bearer credentials, so WebSocket query strings must not enter logs.
The socket accepts only `chat.send` text commands, using the same authoritative service as
REST. Every send rechecks current user/Workspace/participant access; clients cannot choose
sender or Conversation. No hierarchy bypass or read-state commands exist. ACK confirms
durable creation only; Notification behavior and read markers are unchanged.
