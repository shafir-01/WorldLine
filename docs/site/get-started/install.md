# Install

WORLDLINE is a Python 3.12 package with a `worldline` command, plus an optional static viewer and an optional HTTP verifier.

## Prerequisites

| Tool | Version | Needed for |
|---|---|---|
| Python | 3.12 or newer | everything |
| [uv](https://docs.astral.sh/uv/) | any recent release | environments and running commands |
| Node.js | 20 or newer | the viewer only |
| git | any | cloning, and the commit digest that releases are bound to |

## Install

```bash
git clone https://gitlab.com/shafir2/WorldLine.git
cd WorldLine
uv sync --extra dev
```

The `dev` extra adds pytest and Hypothesis. To run the HTTP verifier locally, also add the `service` extra:

```bash
uv sync --extra dev --extra service
```

## Check the install

```bash
uv run pytest -q
uv run worldline --help
```

The test suite covers units, properties, integration, and the CLI. All tests should pass. `worldline --help` lists the commands, which are documented in the [CLI reference](../reference/cli.md).

## Configuration

You don't need any environment variables to run WORLDLINE locally. The demo generates a throwaway signing key, so the whole chain runs on a laptop.

Two kinds of configuration matter later:

- **On GitLab CI**, the protected variable `WORLDLINE_KERNEL_KEY` holds the kernel's Ed25519 private key, which you generate with `uv run worldline keygen`. Commit only the matching public key, `policies/kernel_public.pem`. See [GitLab workflow](../guides/gitlab-workflow.md).
- **For the verifier service**, `WORLDLINE_DATA_DIR`, `WORLDLINE_UI_DIST`, `WORLDLINE_POLICY_DIR`, and `GIT_COMMIT` override where it finds data, the viewer, the policy, and the reported commit. See [Operations](../guides/operations.md).

## The viewer (optional)

```bash
cd ui
npm ci
npm run build      # copies ../experiments/out (or the fixtures) into dist/data
npm run preview
```

Next: [First run](first-run.md).
