# Homban Product Definition

## Overview
Homban is a commercial web-based CRM for real-estate businesses in Iran.

The canonical product modes are **Personal** and **Team**. Agencies, Range Managers
and individual consultants may be commercial purchasers, not three runtime variants.
Keep four concepts separate:

- Workspace: tenant/isolation boundary.
- Purchaser identity: `Workspace.customer_type`, historical/commercial/provisioning metadata.
- Product mode: `Workspace.workspace_mode`, personal or team.
- Operational authority/topology: roles, Ranges and planned central Workspace Authority.

### Personal — target architecture

One consultant operating alone, with own files, customers, matching, MatchingProfile,
Manual Tasks, Calendar and applicable Notification Center behavior. Personal has no
Internal Chat, General Group, Announcements, Collaboration, cross-consultant pass or
organizational team management. Future imports, Voice-to-CRM, Deal, personal-capable
Map and reporting are planned; defining the mode does not implement them.

### Team — target architecture

Team contains one or more real operational Ranges; each Range is a team. With exactly
one Range, its Range Manager is top Workspace operational authority; no Agency Manager
is required. With multiple Ranges, each has a Range Manager and one Workspace-level
Agency Manager sits above the teams. Growth from one to multiple teams stays within
Team mode. These topology/authority invariants are not yet enforced.

### Current transition

B1 stores personal/team with no default. NULL is temporary unresolved classification,
not a third mode. Existing rows were not auto-classified. New application provisioning
uses `organizations.services.create_workspace` with explicit mode, independent of
purchaser, owner, roles and Range counts. B2a GET `/api/v1/auth/me/` exposes read-only
`workspace_mode` as `"personal"`, `"team"` or `null`. This is informational: authentication,
permissions, authority and capabilities are unchanged. Final NOT NULL enforcement is
deferred. See [ADR 0004](decisions/0004-workspace-product-mode.md).

### Approved feature classification

| Classification | Features |
| --- | --- |
| Core — Personal + Team | PropertyFile, Customer, own Matching, MatchingProfile, ManualTask, Calendar, Notification Center, geography; future imports/staging, Voice-to-CRM, Deal, personal-capable Map/reporting |
| Team-only | Internal Chat, General Group, Announcements, Collaboration, cross-consultant matching/pass and cross-consultant workflows |
| Topology-dependent Team | User/Range management, Workspace administration, global announcement administration, reporting scopes, map territory assignment, future Custom Admin permissions and Workspace Authority actions |

Chat (F1) and Collaboration (F2) are centrally Team-only via capability policy:
Personal and NULL mode are denied. Cross-consultant Matching is also centrally Team-only
(F3); own Matching remains Core in Personal + Team. Daily Tasks is Core and composes capability-filtered source projections (F4). Capability entitlement is separate from authority, topology and privacy;
existing domain/participant checks still apply. Purchaser/owner identity grants no entitlement.

## Workspace model
Intended Workspace provisioning provides the following; the full production provisioning
control plane remains future work:
- A dedicated address/subdomain or equivalent route
- An initial admin/primary-user credential
- One consultant in Personal; Team users subject to product/business policy
- Independent data and organizational structure

Homban is not currently a central multi-agency CRM from the customer's perspective.

A future company-level control plane may manage provisioning, billing, support, operational status, and lawful/privacy-aware cross-customer analytics. This must remain separate from ordinary customer data access.

## Organizational implementation boundary

The target topology above supersedes the old optional-Range agency design. Current
models still permit zero Ranges, nullable managers and unassigned consultants. Later
topology enforcement must address legacy data explicitly. Secretary and future delegated
Admin behavior must not imply universal Workspace authority.

## File and customer definitions
Files:
- Sale file
- Rent file

Customers:
- Buyer
- Tenant

A file/customer is assigned to a consultant, who is responsible for it.

## Pass/collaboration concept
In Team, consultants may collaborate across assigned records. Collaboration now requires
the central collaboration capability (F2), in addition to existing participant rules.

Example:
- Consultant A owns a file
- Consultant B owns a matching customer
- The system can recommend a match
- The consultants can pass/collaborate
- Future deal commission logic may include both consultants

Commission formulas are not yet finalized.

## Location
A workspace can operate in any city, though initial focus is Tehran.

Location configuration modes:
1. Custom Workspace-wide regions
2. Divar-compatible Workspace-wide city/region structure

region_mode=NULL means incomplete setup, not a no-region operating mode. C3
configuration uses central Workspace Authority; Personal setup remains a later decision.
Product mode does not redesign the canonical geography model.

City exists conceptually before region.

## File/customer creation levels
### Level 1 — Manual
Implemented first.
- Consultant creates file/customer through simple form
- Record is assigned to that consultant
- Region, source, and existing Google Sheets fields should inform the data model

### Level 2 — External discovery
Future.
- Repeating scheduled tasks
- API calls and/or scraping/crawling
- Sources may include Divar, Amlak Plus, Kashano, Peyvand, etc.
- Consultant defines search parameters
- System checks sources approximately once or twice daily
- New records are staged for consultant review
- Consultant approves/rejects
- Consultant maps source location to internal Homban region

### Level 3 — Voice/AI
Future.
- Consultant submits voice
- Speech-to-text
- AI extracts structured data
- System creates file/customer draft or record

## Matching
Matching is a central product feature and an advanced version of the earlier Google Sheets matching engine.

Requirements:
- Search file/customer compatibility automatically
- Own File-to-Customer matching in both modes
- Cross-consultant matching/pass in Team only as the target capability
- Produce explainable score
- Show recommendations in:
  - Task/recommendation page
  - File detail
  - Customer detail
  - List-page quick summary/modal
- Default Homban matching formula
- User-adjustable weights through simple sliders
- Reset to Homban defaults

## Suggested program / Tasks
One operational page combines:
- Automatic match recommendations, ranked by priority/score
- Manual user tasks/reminders that become due

A calendar lets a user schedule reminders for specific dates.

## Chat
Internal workspace chat:
- Private user-to-user chat
- Official announcement channel
- Optional general group

Chat is centrally Team-only via the chat capability (F1); Personal/NULL are denied.
Manager presence alone grants no availability. Announcement administration uses the
separate C1 Workspace Authority resolver; eligible explicit delegation remains
publishing-only. Existing participant privacy and activity checks still apply.

General group:
- One optional Workspace-wide group, Team-only; all active customer roles participate
  automatically when enabled. No manual membership or custom groups.
- Current C1 Workspace Authority enables/disables it, independently of capability.
- Team capability eligibility does not require manager presence; Personal/NULL deny access.
  Customer type and workspace ownership do not grant Chat authority.
- The Phase 4B foundation is text-only REST history/sending, disabled by default;
  disabling retains history. Phase 4B.1 adds realtime delivery and WebSocket text sending
  through the same service; REST history remains recovery. No group Notifications/read state.

Implemented content: private text plus one secure image/file or Ogg/Opus voice attachment;
Announcements and General Group are text-only. Private and General Group sockets send
text; attachment uploads use REST. Normal Unicode emoji is text. Video, multi-attachment,
reactions and recording frontend remain unimplemented.

No cross-workspace chat.

## Notifications
The in-app Notification Center supports matching, collaboration, private Chat and
announcements. Future external/import delivery is separate. Product direction includes:
1. Matching / suggested-program updates
2. Private chat message received
3. Auto-import discovery completed and records await review

Notifications should deep-link to the relevant destination/action.

## Deal registration — future
The planned Deal workflow will record completed real-estate deals.

Deal data includes:
- File
- Customer
- Price/financial data
- Date
- Tracking code where applicable
- Other required transaction fields

The future workflow is intended to update related statuses after registration;
this automation is not implemented.

Examples:
- Sale file -> sold
- Buyer customer -> purchase completed
- Rent file -> rented
- Tenant customer -> rental completed

## Admin panel — future
Homban requires a custom product admin panel; it is not implemented.
Django's default admin is not the customer-facing admin experience.

Target topology-dependent administration uses future central Workspace Authority with
explicit delegation. Purchaser/owner identity is not an operational bypass. No configurable
Admin permission bundle is implemented yet.

The panel should manage product processes subject to permissions.

## Global date presentation contract
All customer-facing Homban date/calendar presentation is Persian/Jalali.
Gregorian dates are internal implementation details only. New or revised presentation
contracts must use the shared Jalali boundary; historical unrelated APIs are migrated
when their presentation contract is revised.

## UX principle
This is the highest-priority product principle:

Homban must be extremely easy for users with low technical literacy and low tolerance for complex CRM workflows.

Therefore:
- Minimize typing
- Minimize steps
- Use quick actions
- Prefer selectors, toggles, sliders, drag/drop, presets
- Keep flows obvious and forgiving
- Hide technical complexity


### Phase F3 — Cross-consultant Matching capability

Matching uses `cross_consultant_matching` only for different-owner File/Customer pairs.
Personal/NULL exclude cross-owner live and weak candidates; Team retains existing scope
and privacy checks. Own Matching, MatchingProfile and own recommendation lifecycle remain
Core in Personal + Team. Pure scoring/formulas and restricted serializers are unchanged.
Generation excludes unentitled new pairs and reconciles retained historical rows as not
current; viewer scopes deny cross-owner history immediately while entitlement is absent.
Allowed live cross-owner results alone can carry collaboration references; F2 redemption
rules are unchanged. Chat + Collaboration + cross-consultant Matching are migrated;
Daily Tasks is Core and consumes capability-filtered Matching and Collaboration
projections (F4). No model fields, schema, migrations or authority semantics change.


### Phase F4 — Daily Tasks source capability composition

Daily Tasks remains Core; no new Daily Tasks capability class or Team-only gate is added.
Its Workspace-locked aggregation composes the authoritative recommendation viewer scope,
Collaboration participant scope plus its central capability, and ManualTask owner scope.
The same scoped querysets supply pagination and card hydration. Personal and NULL retain
own Matching + ManualTask where existing rules allow, with no Collaboration/cross-consultant
Matching cards. Team retains the existing mixed feed. Detail/actions continue to delegate
to source services, so source capability restrictions cannot be bypassed through Daily Tasks.
Ordering, Jalali timestamps, privacy, score isolation, read-only feed behavior and query
bounds remain unchanged. No model/migration, source lifecycle or ManualTask behavior changes.


### Phase F5 — Management capability enforcement

Customer User list/detail/create/update/activity paths require `user_management`.
Range list/detail/roster/membership and structural create/update/activity/manager-change,
explicit expansion and Agency replacement paths require `range_management` through the
existing customer policy/service boundaries. Personal and NULL have no Team management
surfaces. Team product eligibility alone grants no authority: C1, local Range Manager
scope, protected targets, D topology validation and atomic lock ordering remain unchanged.
Neutral/local operations in invalid Team topology retain their existing scope. No
owner/customer_type/staff/superuser capability bypass exists.

No distinct customer Workspace-administration consumer exists in this scope beyond
User/Range surfaces; `workspace_administration` remains a defined capability without a
fabricated endpoint. Geography/Chat administrative consumers are outside F5. Trusted
internal provisioning, topology transitions and reviewed repair retain their existing
contracts without customer capability gates. No model/schema/migration change.


### Phase F6 � Notification source capability revalidation

Notification Center remains Core in Personal and Team; existing Core notification
access also remains available for unresolved mode. Publication and all recipient
list/count/detail/read operations revalidate source entitlement. Collaboration uses
`collaboration`; private Chat notifications use `chat`, and Announcement notifications
use `announcements`.
Matching uses `cross_consultant_matching` and its authoritative viewer scope to keep
own recommendations accessible while excluding cross-consultant sources in Personal/NULL.
Import/voice kinds remain Core service placeholders; there are no ManualTask or General
Group notification producers. Historical Team-only rows are retained, hidden and excluded
from read-all while entitlement is unavailable. Source links still enforce source services'
authorization/privacy; notification entitlement grants no source permission or score access.
Checks use bounded queries without hydrating private source data. No model/schema change,
new notification producer, or duplicated source authorization is introduced.


Phase E covers the completed Capability Foundation only. Phase F consumers are:
F1 Chat family, F2 Collaboration, F3 cross-consultant Matching, F4 Daily Tasks,
F5 User/Range Management, and F6 Notifications. Chat-family entitlement uses distinct
IDs: private Chat `chat`, Announcements `announcements`, General Group `general_group`.
These product checks remain separate from authentication, C1 authority and participant access.


### Phase F7 - Existing administration consumers

`workspace_administration`: migrated existing PATCH `/api/v1/location-settings/`
through `set_region_mode`, under its Workspace lock and existing C1 authority.
Personal/NULL are denied without purchaser/owner/staff/superuser bypass; Team
eligibility does not grant authority. Read-only settings and City/Region APIs
retain their policies; these are not map territory assignment.
`custom_admin_permissions`: reserved at F7 closure; CA1 now implements its delegation consumer below.
`global_reporting`: no current consumer; reserved for reporting.
`map_territory_assignment`: no current consumer; reserved for the Map feature.
Framework `/admin/` remains internal/operator administration. Internal provisioning,
repair and F5 User/Range paths are unchanged. This supersedes F5's no-distinct-admin-
consumer note: F7 identifies the existing location configuration mutation.
No endpoint, model or migration is added. F7 completes planned Phase F consumer
migration, subject to closure review. Phase G has not started.


### Custom Admin CA1 - Permission foundation

Custom Admin is customer delegation, separate from Django/operator admin and C1.
The closed catalog is `manage_users`, `manage_ranges`, `manage_workspace_settings`.
`custom_admin_permissions` is now consumed by CA1: Personal/NULL fail closed;
Team eligibility still requires canonical C1 authority for grant management.
GET `/api/v1/custom-admin/permissions/` exposes IDs and translated labels to eligible
Team users. GET/POST `/api/v1/custom-admin/grants/` and DELETE
`/api/v1/custom-admin/grants/<uuid>/` are authority-only. POST accepts only `user_id`
and `permission`; Workspace, grantor and creation timestamp are server-owned.
No PATCH or recursive delegation exists. Active local users of existing customer
roles are eligible; authority self-grants are rejected. Grant responses include only
user ID/username/name, permission/display, grant ID and creation time.
CustomAdminGrant stores current state, not audit history; revocation deletes the row.
A schema-only migration adds UUID/FKs/timestamp and unique Workspace/user/permission
and closed-permission constraints. Model validation checks same-Workspace FKs;
supported writes serialize Workspace -> local Users ordered by PK -> grant rows.
Raw ORM/SQL bypass is not a customer write interface.
The read helper reloads actor state, requires active user/Workspace and capability,
then resolves C1: canonical authority is allowed, otherwise an exact local grant is
required. Invalid authority state fails closed. Inactive/moved users and mode changes
suspend access without deleting grants; grants remain current state across authority
handoff. Grantor membership must still match the Workspace. No purchaser/owner/staff/
superuser flag grants customer authorization. Future mutation consumers must recheck
under their existing transaction boundary and still enforce their own capability,
organizational scope, ownership, action and sensitive-field policies.
CA1 does not integrate grants into User/Range/Location or other consumers. CA2+ owns
that integration. Existing topology, repair/provisioning and Django admin are unchanged.


### Custom Admin CA2: scoped consumer integration

- `manage_users` remains in the CA1 catalog and grant storage, with no new operational authorization effect. Existing F5 local Range Manager User Management works without grants and retains its existing scope. Future explicitly-scoped User Management expansion is deferred to CA3 or later.
- `manage_ranges` permits only name, activity_scope, and regions metadata PATCH within the delegate's existing exactly-one-active-managed-Range scope. The exact grant and independent `range_management` capability are required. Other roles gain no Range scope.
- Range creation, activation/deactivation, manager replacement, expansion, Agency replacement, and all structural/authority transitions remain canonical-authority-only. Delegated metadata requests containing structural fields are denied, including unchanged structural values. Existing local membership operations are unchanged.
- `manage_workspace_settings` delegates only `set_region_mode` (PATCH /api/v1/location-settings/), independently requiring `workspace_administration`. City/Region management and read behavior are unchanged.
- Consumers reuse the CA1 current-state permission helper; grants do not redefine C1 authority. Workspace-first writes recheck authorization; revoked grants, inactive actors/workspaces, changed tenancy, lost scope, and Personal/NULL mode fail closed. No owner/customer_type/staff/superuser bypass is added.
- Custom Admin grant administration remains canonical-authority-only; no recursive delegation. Feature capabilities, scope, topology and action restrictions remain independent. No schema/migration change. CA2 tests await external verification.


### Custom Admin CA3: backend read contract

Custom Admin backend implementation is complete through CA3, pending external verification; frontend/UI remains future work. No schema or migration change.

The closed catalog retains permission/display and adds id/label/status/operational. manage_users is reserved/non-operational but remains grantable and stored; future activation requires explicitly defined safe scope. manage_ranges and manage_workspace_settings are active; their CA2 scope and structural restrictions are unchanged. Metadata is code-defined, never client-controlled or stored in the database.

GET /api/v1/custom-admin/me/ returns is_authority, granted_permissions, and operational_permissions. Authority is canonical C1 only, with no synthetic grants. Granted permissions are current valid local stored grants, in catalog order; operational permissions are their catalog-active subset, NOT a guarantee of action authorization or local scope. A stored manage_users grant appears only in granted_permissions.

The read endpoint reloads actor/workspace state and requires custom_admin_permissions; Personal/NULL, inactivity, and unresolved authority fail closed. Revocation is immediately reflected. Foreign/malformed tenant grants and unknown permission IDs never apply. No owner/staff/superuser/customer_type bypass. No writes or cross-request permission cache.

Grant-list fields remain unchanged and safe: id, minimal user identity, permission, permission_display, created_at. Labels share the central catalog; clients can join catalog metadata by permission ID. Grant administration remains authority-only. No new administrative powers or feature consumers.


### Draft/Staging DS1 foundation

DS1 provides a separate apps/staging service/model domain for incomplete or uncertain source data. Canonical PropertyFile/Customer validation is unchanged; PropertyFile still requires a non-null Workspace Region. No canonical conversion, duplicate detection, crawler/import/API/voice integration, or review hierarchy exists in DS1. DS2 will add canonical validation/conversion/review flow.

StagedRecord stores UUID, Workspace, entity_type (property_file/customer), source_type (crawler/api/import/voice), raw_payload JSON, normalized_data object, structured validation_issues (field/code/message), status, nullable created_by, and timestamps. There is no Region FK or provider-specific schema. Source provenance remains in raw_payload; no extra metadata field is needed yet. No secrets/tokens should be supplied as source payload.

Staging is Core: Personal and Team eligible; NULL fails closed. Services require an active Workspace and staging capability without purchaser/owner/staff/superuser bypass. Trusted internal calls require explicit workspace_id and may use created_by=None; creator is attribution, not a global ownership policy. Actor-originated service calls derive Workspace/creator from current actor and restrict mutations to that creator. No customer API, directory, list/detail projection, or raw-payload exposure is added. Review/read authorization is deferred to DS2.

Writes use Workspace-first transactions and scoped record locks. Services preserve raw_payload after creation, allow draft edits, draft->ready/rejected and ready->draft/rejected; rejected is terminal. Edits return ready records to draft. Readiness checks valid vocabulary/object data and no issues only; it does NOT certify canonical validity. Model/service validation enforces creator tenancy; closed vocabularies also have DB constraints. Schema migration: staging.0001_initial. External verification pending.


### Draft/Staging DS2: review and canonical conversion (external verification pending)

DS2 adds GET /api/v1/staging/ and /api/v1/staging/<uuid>/, PATCH detail, and POST detail actions validate/, convert/, ready/, reject/. No customer creation endpoint is added. Lists are paginated newest-first and exclude raw_payload, normalized_data and issue text; authorized detail includes the source payload. PATCH accepts normalized_data/validation_issues only; source identity and raw payload remain immutable.

Current active actor, active Workspace and staging capability are required. Personal review covers the own Workspace, including system-created rows. Team creators review their own rows; canonical C1 authority reviews all local rows including created_by=NULL. No Range reviewer hierarchy or owner/staff/superuser/customer_type bypass. Guessed foreign/out-of-scope IDs return 404. Review rights do not grant canonical creation or assignee rights.

Validation preview is read-only for draft/ready and returns valid, entity_type, issues (canonical), and staging_issues separately. Ready remains foundation-ready, NOT canonical-valid. Normalized input uses the existing PropertyFileWriteSerializer or CustomerWriteSerializer fields; unknown inputs are rejected. Shared canonical field resolution, model validation, customer region-set validation and child validation are reused. No Region inference/creation occurs. Canonical assignment follows the conversion actor's normal application scope, not the ingestion creator. Managers still supply an eligible assigned_to.

Conversion requires ready and empty staging issues, then revalidates under Workspace -> current User -> staging-row locks. Existing canonical save services retain matching event/relationship behavior. Canonical creation and converted status/linkage/audit commit atomically; errors roll back all writes and transactional callbacks. A concurrent loser observes terminal converted state. Post-commit broker delivery is not part of database rollback. No duplicate matching/merging is performed.

Staging migration 0002 adds converted status, protected converted_property_file/converted_customer links, converted_by and converted_at. DB checks require exactly one entity-correct target plus audit fields when converted, and no conversion fields otherwise; model validation enforces target/actor tenancy. Rejected and converted rows remain readable but terminal/immutable, with protected canonical history. DS1 records need no data migration. Canonical models and non-null PropertyFile Region requirement are unchanged.

No source integrations, import parser, crawler, Voice-to-CRM, duplicate engine, Map, reviewer assignment or frontend are included. DS2 backend completion awaits external verification; DS3 has not started.
