# WORLDLINE documentation

This folder holds the project's written documentation. Most of it is also published as a documentation site under the GitLab Pages site at `/docs/`, next to the viewer. The site is built from `docs/site/` by `scripts/build_docs.py`.

## Build the site

```bash
pip install uv                                  # if uv is not installed yet
python scripts/build_docs.py                    # -> public/docs (strict: broken links fail)
python scripts/build_docs.py --out site_preview # any output directory
python scripts/build_docs.py --stage-only       # stage + generate only
uvx --with mkdocs-material mkdocs serve         # live preview after staging
```

The script copies the documents below into `docs/site/` and rewrites their relative links. Links to other included documents point at their site page, screenshots point at the site's assets, and links to any other file in the repository point at GitLab. It also generates the CLI reference from `uv run worldline <command> --help` and the invariants reference from `invariants/*.yaml`, then runs `mkdocs build --strict` with mkdocs-material through `uvx`. This adds no project dependency. In CI, the `docs` job in `.gitlab/ci/docs.yml` builds into `public_docs/`, and the `pages` job copies that into `public/docs/`.

## What is here

| File | What it is | On the site as |
|---|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | The module contracts: data shapes and signatures between packages. | Reference > Module contracts |
| [THESIS.md](THESIS.md) | The problem, the falsifiable contributions, prior art, and the defining experiment. | Research > Thesis and prior art |
| [REPORT.md](REPORT.md) | The measurements, the limitations, and open questions. | Research > Technical report |
| [ROADMAP.md](ROADMAP.md) | The research program, with the experiment that settles each item. | Research > Roadmap |
| [RELEASES.md](RELEASES.md) | Proof-carrying releases: how to cut one and how to verify one offline. | Guides > Releases and verification |
| [GITLAB_SETUP.md](GITLAB_SETUP.md) | Project setup on GitLab.com: protected refs, variables, Duo agents and flows, and Pages. | Guides > GitLab setup (full) |
| [WORKFLOW.md](WORKFLOW.md) | The issue, MR, kernel, and release loop on GitLab. | Guides > GitLab workflow |
| [INSTALL.md](INSTALL.md) | Installing a published release from the package or container registry. | Get started > Install a release |
| [INVARIANTS.md](INVARIANTS.md) | The catalogue of the extended invariants, with parameters and witnesses. | Reference > Invariant catalogue |
| [REAL_DATA.md](REAL_DATA.md) | The USGS scenario, whose truth stream is real recorded data. | Guides > Real-data scenarios |
| [REAL_SERVICE.md](REAL_SERVICE.md) | Running WORLDLINE against a real ASGI service. | Guides > Real services |
| [HACKATHON_REQUIREMENTS.md](HACKATHON_REQUIREMENTS.md) | Hackathon requirements as researched. | not published |
| [SUBMISSION.md](SUBMISSION.md) | The hackathon submission text. | not published |
| [screens/](screens/) | The viewer screenshots used by the README and the site. | Home (60-second tour) |
| [duo/](duo/) | The GitLab Duo agent prompts, flow definitions, and catalog ids. | linked from the GitLab guides |
| [demo/](demo/) | The demo video shot list. | not published |
| [site/](site/) | The documentation site's sources: hand-written pages (Home, Get started, Concepts, Operations, and the schema reference), plus `_partials/` fallbacks and templates. Staged copies and generated pages are gitignored. | the site itself |

Documents outside this folder that are also on the site: [`service/README.md`](../service/README.md) (Reference > Verifier API), [`ui/README.md`](../ui/README.md) (Reference > Viewer and data contract), and [`CHANGELOG.md`](../CHANGELOG.md) (Changelog).

## Adding a page

- **A new document in `docs/`**: add it to `STAGED` in `scripts/build_docs.py` and to `nav` in `mkdocs.yml`, then list the staged path in `docs/site/.gitignore`.
- **A new hand-written site page**: add it under `docs/site/` and to `nav`.
- Optional sources, which the build tolerates being absent, are marked `False` in `STAGED`. When one is missing, its nav entry is dropped, or its hand-written fallback in `docs/site/_partials/` is used.
