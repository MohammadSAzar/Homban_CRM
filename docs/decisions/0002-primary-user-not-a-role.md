# ADR 0002 — Primary purchaser is not a role

## Status
Accepted

## Context
The person who buys a Homban workspace may be:
- Agency manager
- Range manager
- Consultant

Therefore "owner" does not describe their operational job role.

## Decision
Represent the purchaser/primary user separately from operational role.

Current implementation uses a concept such as:
```text
is_workspace_owner
```

Operational role remains one of the role choices.

## Consequences
A consultant can be both:
- role = consultant
- workspace owner = true

This avoids inventing an artificial "owner" role that conflicts with the person's real operational permissions.
