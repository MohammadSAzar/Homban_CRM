# Homban Engineering Instructions

Homban is a Persian, RTL, mobile-first real-estate CRM web application for the Iranian market.

## Read first
Before implementing any non-trivial feature, read:
- `docs/PRODUCT.md`
- `docs/DOMAIN.md`
- `docs/ARCHITECTURE.md`
- `docs/PERMISSIONS.md`
- `docs/TESTING.md`
- `docs/DEVELOPMENT.md`
- `docs/CURRENT_STATE.md`

For architectural changes, also inspect `docs/decisions/`.

## Product rules
- Canonical product modes are Personal (one consultant) and Team (one or more Range teams).
- `customer_type` is purchaser/commercial metadata, not runtime mode or authority.
- `workspace_mode` stores personal/team; NULL is unresolved classification, not a third mode. No default or heuristic inference applies.
- `is_workspace_owner` identifies the purchaser/primary account, not target operational authority. Legacy owner privileges remain pending focused refactors.
- Each customer operates inside an isolated `Workspace`.
- Never leak data across workspaces.
- The organizational hierarchy may include:
  - Agency manager
  - Range manager
  - Consultant
  - Secretary
  - Admin
- Target Team requires operational Ranges: one Range uses its Range Manager as Workspace authority; multiple Ranges use a Workspace-level Agency Manager above managed teams.
- Personal has no operational team structure. Current schema permits legacy states; central Workspace Authority and topology enforcement remain future work.
- Classify features as Core, Team-only, or Topology-dependent Team. Team-only features require backend capability enforcement when migrated; mode storage and read-only exposure alone do not gate features.
- Files are either sale or rent.
- Customers are either buyer or tenant.
- Files and customers are assigned to consultants.
- Own matching is Core; cross-consultant matching/pass and Chat are Team-only targets. Central mode-based capability migration is not yet complete.
- Contact information on records not owned by the requesting consultant must not be exposed unless explicitly permitted.

## Language and UI rules
- Python, Django, DRF, database identifiers, class names, function names, field identifiers, URL names, variable names, and internal code must be English.
- User-facing labels, messages, choices, validation text, and CRM content must be Persian.
- Use Django i18n tools such as `gettext_lazy`, `verbose_name`, and `verbose_name_plural`.
- Frontend must be 100% RTL.
- The product is mobile-first, but desktop functionality must remain complete.
- UX should minimize typing and cognitive load. Prefer clicks, selectors, sliders, quick actions, drag/drop, presets, and short flows.

## Backend rules
- Backend is Django + Django REST Framework.
- API-first architecture; do not build feature-specific server-rendered HTML views unless explicitly requested.
- MySQL is the primary database target.
- Prefer clean, modular, reusable services over duplicated logic.
- Avoid fat views and duplicated business logic.
- Querysets must be efficient.
- Prevent N+1 queries; use `select_related()` / `prefetch_related()` when appropriate.
- Model relationships and database indexes must be deliberate.
- Avoid unnecessary schema duplication.
- External integrations must be isolated behind service/client layers.
- Never hard-code secrets. Use environment variables.

## Access-control rules
- Access control is a first-class concern.
- Enforce authorization in backend/API, not only in frontend.
- Use role + organizational scope + object ownership where relevant.
- Add object-level checks for sensitive operations.
- Tenant/workspace isolation must be enforced before role-level filtering.
- Sensitive contact fields must be omitted or masked at serializer/API level where access is not allowed.

## Testing rules
Every testable change must include tests.
Use `pytest` + `pytest-django`.

Test as applicable:
- Models
- Constraints and validation
- API endpoints
- Authentication
- Permissions
- Cross-workspace isolation
- Sensitive-field visibility
- Services/business logic
- Signals
- Tasks/Celery jobs
- Integrations
- Query-count/performance-sensitive paths where justified

Before finishing a coding task, run:
```bash
python manage.py check
pytest
```

Do not consider a task complete with failing tests.

## Git rules
- Inspect `git status` before and after changes.
- Prefer one logical feature per branch/commit set.
- Do not mix unrelated refactors with feature work.
- Do not rewrite or amend existing commits unless explicitly requested.
- Summarize files changed, migrations created, tests added, and any architecture decisions.
- Do not commit secrets, `.env`, credentials, local databases, generated media, or IDE state.

## Change discipline
- Do not invent product behavior when docs are explicit.
- If product behavior is ambiguous and affects architecture, stop and report the ambiguity rather than silently deciding.
- Small implementation details may be chosen autonomously when they preserve documented behavior.
- Do not redesign existing architecture without explaining why.
- Keep migrations deterministic and reviewable.
