# Installing WORLDLINE

WORLDLINE ships three ways: a Python package in this project's GitLab **PyPI package registry**, a
**container image** in its GitLab container registry, and the source checkout. Every tagged release
publishes the first two after the kernel release gate has AUTHORIZED the tag (`.gitlab/ci/package.yml`);
a DENIED tag publishes nothing.

| Way | Best for | Command |
|---|---|---|
| `uvx` (no install) | trying the CLI | `uvx --index https://gitlab.com/api/v4/projects/88121109/packages/pypi/simple worldline --help` |
| `uv tool` / `pipx` / `pip` | keeping `worldline` on PATH | see below |
| Docker | zero Python on the host, full `demo` | `docker run --rm registry.gitlab.com/shafir2/worldline/cli:latest demo` |
| Source checkout | development, CI, the viewer | README "Getting started" |

Requirements: Python 3.12 or newer for the package; Docker 20+ for the image.

## From the GitLab PyPI registry

The project is public, so the registry needs no token. Its simple index is:

```
https://gitlab.com/api/v4/projects/88121109/packages/pypi/simple
```

### uv (recommended)

```bash
# run once, nothing installed permanently
uvx --index https://gitlab.com/api/v4/projects/88121109/packages/pypi/simple worldline --help

# a specific version
uvx --index https://gitlab.com/api/v4/projects/88121109/packages/pypi/simple worldline@0.1.3 --help

# install as a tool (puts `worldline` on PATH)
uv tool install --index https://gitlab.com/api/v4/projects/88121109/packages/pypi/simple worldline
```

`--index` is searched before PyPI, and uv stops at the first index that has a package, so `worldline`
always comes from this project while its dependencies (typer, rich, pyyaml, cryptography) come from PyPI.

### pip / pipx

```bash
pip install --index-url https://gitlab.com/api/v4/projects/88121109/packages/pypi/simple worldline
pipx install --index-url https://gitlab.com/api/v4/projects/88121109/packages/pypi/simple worldline
```

With `--index-url` the GitLab registry is the only index; it forwards requests for packages it does not
hold (the dependencies) to pypi.org, which is GitLab's default. Do not use `pip --extra-index-url` for
this: pip picks the highest version across all indexes, so an unrelated `worldline` on pypi.org could win.

### Check the install

```bash
worldline --version             # worldline 0.1.3 (commit) python 3.12.x <platform>
worldline resources             # where the bundled scenarios, invariants, policy and public key live
worldline resources --json      # the same, machine-readable
```

The wheel bundles `scenarios/`, `invariants/`, `policies/release.yaml` and `policies/kernel_public.pem`
under `worldline/_data/` (never the private key). Run a bundled scenario using the paths `resources` prints:

```bash
worldline run "<scenarios_dir>/port_alert.yaml" --invariants "<invariants_dir>" --out wl-out
```

or materialise a working directory where every CLI default (`scenarios/...`, `invariants`,
`policies/release.yaml`) resolves, then work from it:

```bash
worldline resources --copy-to wl && cd wl
worldline explore scenarios/port_alert.yaml --budget 150 --seed 42 --out experiments/out
worldline demo --skip-tests
```

`demo --skip-tests` from a package install ends DENIED on the `tests:unit` requirement, because the unit
tests that back that claim are not part of the wheel. That is the release policy working as written; use
the container image or a checkout for the AUTHORIZED-then-DENIED demo.

## Container image

```bash
docker run --rm registry.gitlab.com/shafir2/worldline/cli:latest demo
docker run --rm registry.gitlab.com/shafir2/worldline/cli:v0.1.3 --version
# keep the evidence on the host
docker run --rm -v "$PWD/out:/app/experiments/out" registry.gitlab.com/shafir2/worldline/cli:latest demo
```

The image (`Dockerfile.cli`, `python:3.12-slim`) contains the source tree installed in editable mode
plus pytest, so `demo` runs the unit tests for its `tests:unit` claim and prints `AUTHORIZED`, then
`DENIED` on a deliberately tampered bundle. It generates a throwaway kernel key inside the container; only
the committed public key is baked in. Entry point is `worldline`, default command `--help`.

Build it yourself:

```bash
docker build -f Dockerfile.cli -t worldline .
docker run --rm worldline demo
```

## How releases are published

`scripts/release.py` bumps the version in `worldline/__init__.py` (the single source; `pyproject.toml`
reads it through hatch), writes the CHANGELOG entry from `git log` since the last tag, commits those two
files and creates the annotated tag. It never pushes.

```bash
uv run python scripts/release.py patch --dry-run   # preview
uv run python scripts/release.py patch             # 0.1.3 -> 0.1.4, commit "Release v0.1.4", tag v0.1.4
git push gitlab HEAD v0.1.4
```

The tag pipeline then runs `test -> explore -> kernel-verify -> authorize`, and in the `release` stage:

| Job | What it does |
|---|---|
| `release-assets`, `release` | evidence bundle and authorization to the generic registry; GitLab release page (docs/RELEASES.md) |
| `package` | checks the tag matches `__version__`, `uv build` (wheel + sdist), smoke-installs the wheel, `uv publish` to `${CI_API_V4_URL}/projects/${CI_PROJECT_ID}/packages/pypi` with the job token; `dist/` kept as artifacts |
| `package-image` | kaniko (unprivileged, works on gitlab.com shared runners) builds `Dockerfile.cli` and pushes `$CI_REGISTRY_IMAGE/cli:<tag>` and `:latest` |

Tags must match the protected-tag rule `v*` (the kernel key is a protected variable). Treat published
versions as immutable: to fix a release, cut a new version rather than re-pushing the same one.

### Building locally

```bash
uv build                         # dist/worldline-<v>-py3-none-any.whl and dist/worldline-<v>.tar.gz
uv run pytest -q tests/test_packaging.py   # builds, installs the wheel in a fresh venv, runs it
```

### Publishing by hand (maintainers)

```bash
uv publish --publish-url https://gitlab.com/api/v4/projects/88121109/packages/pypi \
  --username <your GitLab username> --password <personal access token with api scope> dist/*
```
