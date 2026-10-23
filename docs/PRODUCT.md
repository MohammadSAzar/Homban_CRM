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

This is the approved direction, not completed capability enforcement. Current policies
remain domain/role-based; Collaboration and cross-consultant Matching are not centrally
Team-gated. Chat uses the temporary manager-presence rule below. Purchaser/owner identity
must not grant target operational authority; legacy owner privileges remain in some domains.

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
In the Team target, consultants may collaborate across assigned records. The existing
Collaboration implementation is not yet workspace_mode-gated.

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

Chat is Team-only in the target. Current availability is a compatibility heuristic,
not workspace_mode enforcement: an active Agency Manager or Range Manager must exist.
Announcement publishing/delegation administration uses current Workspace Chat Authority:
Agency Managers take precedence; otherwise Range Managers inherit it. Explicit delegation
may allow eligible Range Managers/Secretaries to publish. This is not the planned
repository-wide topology-based Workspace Authority resolver.

General group:
- One optional Workspace-wide group, Team-only; all active customer roles participate
  automatically when enabled. No manual membership or custom groups.
- Current Workspace Chat Authority enables/disables it: active Agency Managers take
  precedence; active Range Managers inherit authority only when none exist.
- Without either active manager role, Chat is unavailable; this does not classify workspace_mode.
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
