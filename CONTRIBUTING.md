# Contributing to WORLDLINE

Thank you for looking under the hood. A few rules keep the evidence honest.

## Before you open a merge request

- `uv run pytest -q` must pass. Do not delete, skip or xfail a failing test; fix the cause or explain in the MR why the test was wrong.
- Numbers in docs come from files written by code that ran. Never type a benchmark number by hand.
- Scenarios directly under `scenarios/` are laboratory specimens and are frozen. Repairs land in `scenarios/product/`. See `AGENTS.md`, section "The repair contract".
- `invariants/`, `policies/`, `worldline/evidence/` and the kernel CI jobs are protected by `CODEOWNERS`. Changes there need an owner review and must never weaken a check.
- Every claim you make states its evidence kind (`EMPIRICALLY_VALIDATED`, `FAILED`, `UNKNOWN`). Only the kernel emits `ATTESTED`.

## Good first contributions

- A new scenario in a new domain that you believe the port-alert genome should or should not reproduce on. Either outcome is useful; say which you expect.
- A new invariant kind in `worldline/invariants/checker.py` with PASS, FAIL (exact witness) and UNKNOWN tests.
- A new intervention operator with its mutator and abstract/concrete round trip test.

## Reporting a bug

Open an issue with the scenario, seed and fault schedule. If the explorer found it, paste the branch id and trace digest; they are reproducible on any machine.

## Agents

GitLab Duo agents work in this repository under the same rules. If you are an agent, read `AGENTS.md` first.
