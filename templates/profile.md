# Factory profile

Project rules for the software factory. Every agent reads the "All roles"
section and the section named for its role; the harness also appends them to
the agent's instructions. Where these rules and the plugin's generic guidance
conflict, the stricter one wins. Delete a section you do not need; an empty
section adds nothing. Write rules the way a senior reviewer on this project
would: specific invariants, exact commands, named services to keep away from.

## All roles

- Verification: <exact offline commands, e.g. `pytest -q`, `npm test`; which
  suites a change in each area must run>
- Never run as a routine check: <live services, production databases, paid
  APIs, real user accounts>
- Evidence: synthetic or redacted only; never paste <the project's sensitive
  data: customer records, tokens, emails>.

## spec-writer

- <invariants and scope a spec must preserve; planned features that must not be
  described as implemented>

## builder

- <conventions: language version, dependency policy, money/units, error
  handling, where new modules go>

## reviewer-code

- <the project's correctness contracts to check on every diff>

## reviewer-security

- <auth model, scopes, data boundaries, provider terms, deletion guarantees>

## reviewer-ux

- <user-facing wording rules, error behavior, handoff payloads>

## reviewer-ui

- <design system, supported widths, accessibility bar; or "no UI surface yet">

## reviewer-data

- <data contracts: identity, units, nulls, provenance, training/evaluation
  separation, accuracy measurement>

## approver

- <anything extra the final gate must confirm>

## observer

- <commands the observer may run to verify end to end, and anything it must
  never touch>
