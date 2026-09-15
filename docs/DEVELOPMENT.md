# Homban Development Workflow

## Local environment
Project folder:
```text
HombanCRM
```

Python:
```text
3.10.11
```

Virtual environment:
```text
.venv
```

## Common commands
Activate virtual environment on Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Django check:
```powershell
python manage.py check
```

Run tests:
```powershell
pytest
```

Run coverage:
```powershell
pytest --cov=apps --cov=common --cov-report=term-missing
```

Run server:
```powershell
python manage.py runserver
```

Create migrations:
```powershell
python manage.py makemigrations
```

Apply migrations:
```powershell
python manage.py migrate
```

## Git workflow
Recommended:
- `main` remains stable
- One feature branch per logical feature

Examples:
```text
feature/user-management
feature/location-api
feature/file-model
```

Before coding:
```powershell
git status
git switch main
git pull   # only if a remote is configured
git switch -c feature/<name>
```

Before commit:
```powershell
python manage.py check
pytest
git status
git diff
```

Commit with a focused message.

## Codex workflow
Codex is the implementation agent.

For every task:
1. Read `AGENTS.md`
2. Read linked domain docs
3. Inspect current code and migrations
4. State a short implementation plan when the task is non-trivial
5. Implement code
6. Add/update tests
7. Run checks/tests
8. Review diff
9. Summarize:
   - files changed
   - migrations
   - tests
   - notable decisions
   - remaining ambiguity

## Human/Codex responsibility split
Product owner + ChatGPT:
- Product behavior
- Domain design
- Feature scope
- Permission policy
- Architecture review
- UX principles

Codex:
- Repository changes
- File creation
- Implementation
- Refactoring
- Migrations
- Tests
- Local commands
- Diff summaries

Codex should not silently invent major product behavior.
