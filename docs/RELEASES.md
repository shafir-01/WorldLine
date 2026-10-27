# Releases: proof-carrying by construction

Every tagged release of WORLDLINE carries the evidence that authorized it. The tag pipeline runs the
same chain as every push (`test -> explore -> kernel-verify -> authorize`), then two more jobs:

| Job | Image | What it does |
|---|---|---|
| `release-assets` | `python:3.12` | Uploads `bundle.json`, `authorization.json`, `genome.json`, `minimal_schedule.json`, `challenge.json`, `benchmark.json` (when produced) and `index.json` from `experiments/out/` to the project's **generic package registry** under `worldline-evidence/<tag>/`; writes `release_notes.md` (kernel decision, requirements met, artifact digest, bundle root, signer key id, SHA-256 of every asset, the offline verification commands) and `assets.json` (release asset links). Exits non-zero unless the decision is `AUTHORIZED`, so a DENIED tag never becomes a release. |
| `release` | `python:3.12` | Calls the Releases API with the job token: name, tag, `release_notes.md` as description and the `assets.json` links; the release page shows the notes and links to each evidence file. |

Both run only on tags (`rules: if: $CI_COMMIT_TAG`). The kernel jobs run on tags too (`.rules-kernel`),
but the signing key `WORLDLINE_KERNEL_KEY` is a **protected** variable, so the tag must match a
protected-tag rule or `kernel-verify` fails with "requires a protected ref". That is the intended gate:
only tags an Owner allowed can be sealed.

## Cutting a release

```powershell
# once per project (Settings > Repository > Protected tags, or the API):
glab api -X POST projects/88121109/protected_tags -f name="v*" -f create_access_level=40

git tag -a v0.2.0 -m "WORLDLINE v0.2.0"
git push gitlab v0.2.0
```

Then watch the pipeline for the tag. When `release` is green, **Deploy > Releases** lists `WORLDLINE v0.2.0`
with the notes and the asset links. `index.json` in the assets is the same file the Pages viewer loads.

## Verifying a release offline

Nothing in the verification needs GitLab to be trusted, only the public key committed in the repository
at that tag.

```powershell
$tag = "v0.2.0"
$base = "https://gitlab.com/api/v4/projects/88121109/packages/generic/worldline-evidence/$tag"
curl -LO "$base/bundle.json"
curl -LO "https://gitlab.com/shafir2/WorldLine/-/raw/$tag/policies/kernel_public.pem"
curl -LO "https://gitlab.com/shafir2/WorldLine/-/raw/$tag/policies/release.yaml"
$commit = (git rev-list -n 1 $tag)        # or copy the "Commit" line from the release notes

uv run worldline authorize bundle.json --policy release.yaml --public-key kernel_public.pem --artifact-digest $commit
```

Expected: `decision: AUTHORIZED` with the same `artifact_digest` as the release notes. The command

1. checks the Ed25519 signature of the bundle checkpoint against `kernel_public.pem`,
2. recomputes the hash chain and compares its root to the checkpoint root (printed in the release notes),
3. evaluates `release.yaml` over the typed evidence records for exactly that artifact digest.

Any of these failing is a denial with a named finding (`signature_invalid`, `root_mismatch`,
`chain_broken`, `stale`, ...). Try it: edit one character in `bundle.json` and run the command again;
`uv run worldline demo` does the same locally on a fresh bundle.

To check that the downloaded files are the ones the pipeline produced, compare their SHA-256 with the
table in the release notes (`Get-FileHash bundle.json` on Windows, `sha256sum bundle.json` elsewhere).

## What a release does and does not claim

- The release says: for commit `<sha>`, the kernel found the policy's requirements satisfied by records of
  the stated kinds (`PROVED_WITHIN_MODEL`, `EMPIRICALLY_VALIDATED`, `ATTESTED`), under the assumptions
  listed in each record. That is the whole claim; the notes repeat it as the claim ceiling.
- It does not say the system is correct in worlds the scenarios do not model. `UNKNOWN` verdicts stay
  `UNKNOWN` in the bundle and are visible in `index.json`.
- Agents (Duo flows, humans, scripts) can add records to the chain but never with kind `ATTESTED`; only
  `kernel-verify`, holding the private key on a protected ref, seals a checkpoint.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `kernel-verify` fails on the tag with "requires a protected ref" | tag not covered by a protected-tag rule | add `v*` under Settings > Repository > Protected tags and re-run |
| `release-assets` exits "kernel decision is not AUTHORIZED" | the kernel denied the release | read `authorization.json` in the job artifacts; a denied tag is not a release |
| `release` fails with 403 | `CI_JOB_TOKEN` cannot create releases on this project | Settings > CI/CD > Job token permissions; or check that the tag is protected (job tokens on protected refs have release rights) |
| asset link 404 | the generic package was not uploaded for that tag | look at the `release-assets` log; files that the pipeline did not produce are skipped and listed there |
