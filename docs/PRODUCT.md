# Homban Product Definition

## Overview
Homban is a commercial web-based CRM for real-estate businesses in Iran.

The product is designed to be sold to one of three buyer types:
1. Agency manager
2. Range manager
3. Individual consultant

The same core product must support all three cases.

## Workspace model
Each purchase creates an isolated Homban workspace with:
- A dedicated address/subdomain or equivalent route
- An initial admin/primary-user credential
- Unlimited internal users, subject to product/business policy
- Independent data and organizational structure

Homban is not currently a central multi-agency CRM from the customer's perspective.

A future company-level control plane may manage provisioning, billing, support, operational status, and lawful/privacy-aware cross-customer analytics. This must remain separate from ordinary customer data access.

## Core organizational structure
A full agency may contain:
- Agency manager
- Optional ranges/teams
- One range manager per range
- Multiple consultants per range
- Optional secretary
- Optional delegated admin users

A range is a working team. All ranges perform similar real-estate work.

Range structure is optional. An agency may directly manage consultants without ranges.

## File and customer definitions
Files:
- Sale file
- Rent file

Customers:
- Buyer
- Tenant

A file/customer is assigned to a consultant, who is responsible for it.

## Pass/collaboration concept
Consultants may collaborate across their own assigned records.

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
1. No region breakdown
2. Custom regions defined by the primary user
3. Divar-compatible city/region structure

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
- Match across consultants within the workspace
- Support pass/collaboration logic
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

Announcement posting:
- Agency manager by default
- Agency manager may delegate to range managers and/or secretary

General group:
- Everyone can participate
- Agency manager can enable/disable it

Content:
- Text
- Voice
- Image
- Video
- Emoji

No cross-workspace chat.

## Notifications
At least three distinguishable notification categories:
1. Matching / suggested-program updates
2. Private chat message received
3. Auto-import discovery completed and records await review

Notifications should deep-link to the relevant destination/action.

## Deal registration
Completed real-estate deals must be recorded.

Deal data includes:
- File
- Customer
- Price/financial data
- Date
- Tracking code where applicable
- Other required transaction fields

After deal registration, related file/customer statuses are updated automatically.

Examples:
- Sale file -> sold
- Buyer customer -> purchase completed
- Rent file -> rented
- Tenant customer -> rental completed

## Admin panel
Homban requires a custom product admin panel.
Django's default admin is not the customer-facing admin experience.

Agency manager has primary access and may delegate access to secretary/admin or other approved users.

The panel should manage product processes subject to permissions.

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
