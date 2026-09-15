# Homban Testing Standard

## Mandatory rule
Every testable feature must ship with tests.

A coding task is not complete unless:
```bash
python manage.py check
pytest
```
pass successfully.

## Stack
- pytest
- pytest-django
- pytest-cov

## Test layout
Prefer per-app test packages:

```text
apps/<app>/tests/
├── __init__.py
├── test_models.py
├── test_api.py
├── test_permissions.py
└── ...
```

Do not keep both `tests.py` and `tests/` in the same app.

## Required categories

### Model tests
Test:
- Defaults
- Constraints
- Validation
- Relationships
- Deletion behavior
- Cross-workspace consistency rules
- Choice/state behavior where meaningful

### API tests
Test:
- Authentication required
- Success response
- Validation errors
- Not found
- Forbidden
- Pagination/filtering where applicable
- Response shape
- Sensitive field exposure

### Permission tests
At minimum:
- Allowed role
- Disallowed role
- Same-workspace scope
- Cross-workspace denial
- Same-range vs other-range behavior
- Owner vs non-owner behavior where relevant

### Service tests
For business services:
- Main success path
- Important alternate path
- Invalid state
- Transaction rollback when appropriate
- Side effects

### Integration tests
Use when a workflow crosses modules.

Examples:
- Register deal -> file/customer statuses update
- Matching -> recommendation feed entry
- Import approval -> file created in mapped internal region

### Scheduled task tests
Future Celery/task code:
- Task calls service correctly
- Idempotency where needed
- Failure/retry behavior
- Does not duplicate imports unexpectedly

### Security tests
Prioritize:
- Tenant/workspace leakage
- ID-guessing access
- Contact-field leakage
- Unauthorized mutations
- Privilege escalation

## Database tests
Pytest-Django creates a test database.

Development MySQL user must have permission to use the configured test database.

Do not run tests against production/customer data.

## Coverage
Coverage is a signal, not the goal.

Prefer meaningful coverage of:
- Business logic
- Permissions
- Data isolation
- State transitions
- Critical queries

Avoid meaningless tests solely to increase the percentage.

## Performance-sensitive tests
Where relevant:
- Use query-count assertions
- Prevent N+1 regressions
- Test list endpoints with multiple related records

Do this selectively for important endpoints.
