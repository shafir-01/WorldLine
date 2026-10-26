# Security

WORLDLINE is a research prototype. Nothing here is accredited or certified, and all scenarios are synthetic.

## What the kernel protects against

- An agent or pipeline step misreporting what the pipeline computed: the kernel recomputes claims before attesting them, and a mismatch denies the release.
- Tampering with an evidence bundle after sealing: edits, drops, reordering, truncation and wrong keys produce distinct findings and deny the release.
- A release authorized on stale evidence or for a different artifact digest.

## What it does not protect against

- A compromised CI runner or a leaked signing key. The private key lives only in a protected CI variable; the public key is committed at `policies/kernel_public.pem`. Rotate with `uv run worldline keygen` and update the variable.
- Weaknesses in the simulated world's model. A PASS is a statement about the declared scenario, not about any real system.

## Reporting

Open a confidential issue on the GitLab project, or email the author listed in the README. Please include a reproduction: scenario, seed, fault schedule, and the bundle or trace digest.
