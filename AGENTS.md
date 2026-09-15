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
- Homban is sold as a dedicated CRM workspace to a real-estate agency, a range manager, or an individual consultant.
- Each customer operates inside an isolated `Workspace`.
- Never leak data across workspaces.
- The organizational hierarchy may include:
  - Agency manager
  - Range manager
  - Consultant
  - Secretary
  - Admin
- Range/team structure is optional.
- A consultant may work without belonging to a range.
- Files are either sale or rent.
- Customers are either buyer or tenant.
- Files and customers are assigned to consultants.
- Matching must support cross-consultant collaboration ("pass").
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
