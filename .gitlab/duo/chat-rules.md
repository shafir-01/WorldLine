<!-- Project-level custom rules for GitLab Duo Chat, agents and flows.
     https://docs.gitlab.com/user/duo_agent_platform/customize/custom_rules/
     Rules apply to new conversations only. Project facts live in AGENTS.md. -->

- Read `AGENTS.md` and `docs/ARCHITECTURE.md` before proposing any code change; keep the documented signatures.
- Never edit `invariants/`, `policies/`, `worldline/evidence/`, the kernel jobs in `.gitlab-ci.yml`, or anything under `.gitlab/duo/`. If a task needs that, say so and stop.
- Never weaken an invariant, delete or skip a test, or turn a FAIL into UNKNOWN.
- Use `uv run ...` for every Python command; never call `python` or `pip` directly inside the project.
- Determinism first: no wall-clock time, no randomness outside `DeterministicRNG`, no LLM calls inside the simulated world.
- When you report a failure, quote the invariant id, the witness event ids and the trace digest. Do not paraphrase witnesses.
- Label every claim with its EvidenceKind (PROVED_WITHIN_MODEL, EMPIRICALLY_VALIDATED, ATTESTED, FAILED, UNKNOWN). Agents never produce ATTESTED.
- Say "within the shared Event v1 substrate" whenever you describe genome transfer; never claim transfer across codebases.
- Prefer one small MR per invariant. Put the before/after digests and the regression scenario path in the MR description.
- Write plainly: no emojis, no filler, no marketing language, British or American spelling consistently within a file.
- Python: type hints, frozen dataclasses for value objects, `canonical_json` for anything hashed, tests with pytest and hypothesis.
- TypeScript (ui/): strict mode, no `any`, components render from committed fixture JSON with no backend.
