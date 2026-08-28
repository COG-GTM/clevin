---
name: revenue-report-hardening
description: Playbook for hardening a reporting module in COG-GTM/clevin whose numbers
  are distrusted or whose runtime is slow. Use for any ticket about the monthly revenue
  report, the reportkit package, or when asked to run the "revenue-report-hardening"
  playbook.
---

# Revenue report hardening playbook

Follow these steps in order for a "the report cannot be trusted" ticket.

1. Read `/mnt/memory` for the repository's verified setup and test facts before
   running anything. Confirm each command still works; correct entries you prove wrong.
2. Read `experiments/J/fixture/README.md` and `experiments/J/fixture/AGENTS.md` and
   treat them as the contract. Standard library only.
3. Reproduce the current state first: run the fixture checks and record which check
   fails and why, before changing any code.
4. Diagnose all three classes of defect before editing: field parsing (delimiters and
   quoting), numeric accuracy (binary floating point on money), and empty or missing
   groups. Do not stop at the failing test.
5. Money is never accumulated in `float`. Use `decimal.Decimal` for accumulation and
   keep string inputs exact. Presentation rounding is a policy decision — if the
   repository does not record the policy, ask the operator rather than choosing one.
6. Grouping must be a single pass over the rows; do not rescan the rows per group.
7. Add a test for every behaviour you change, and never weaken an existing expectation.
8. Re-run the fixture checks until green, then get an adversarial review of the diff
   before opening the pull request.
9. Write back to memory only verified, reusable facts: how to run the checks, the
   conventions you had to obey, and defects that were easy to miss.
