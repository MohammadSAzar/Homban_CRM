# ADR 0001 — Workspace-based customer isolation

## Status
Accepted

## Context
Homban is sold to multiple independent customers:
- Full agencies
- Range managers
- Individual consultants

Each customer requires an isolated CRM experience and dedicated address.

The company may later need a central operational/control plane.

## Decision
Use a shared Homban codebase with customer data separated by `Workspace`.

Business data is scoped to a workspace.

Do not deploy a completely separate source-code fork per customer.

## Consequences
Benefits:
- One codebase to maintain
- Easier updates
- Centralized deployment
- Easier operational support
- Compatible with a future company control plane

Risks:
- Tenant isolation becomes security-critical
- Every business query/API must be carefully scoped
- Support access requires audit/privacy design

## Rule
Workspace isolation is enforced server-side and covered by tests.
