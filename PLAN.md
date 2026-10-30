# DepForecast: Plan

Source of truth: `SPEC.md`. This file breaks the milestones into tasks and lists the open questions I need answered before the expensive-to-reverse steps.

**Environment check (done):** Python 3.11.13 in `.venv` (empty). Ollama is installed with `gemma3:4b`. No `gh` CLI.

**Every milestone ends with:** `ruff` + `mypy` + `pytest` green → commit(s) (without claude line) → 3-line summary to you.

---

## M0: Setup
1. Write `.gitignore` (SPEC §15 list, plus `.venv/`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `dist/`). Commit it **first**, before anything gets downloaded.
2. `pyproject.toml`: package `depforecast`, Python ≥3.11, entry point `depforecast = depforecast.cli:app`. Dependencies: typer, rich, pydantic v2, httpx, pandas, scikit-learn. `tabpfn` pinned once I've checked the current version on docs.priorlabs.ai (M3). Dev extras: pytest, ruff, mypy, respx (HTTP fixtures), syrupy (snapshots).
3. Skeleton from SPEC §12: empty modules with docstrings, plus `LICENSE` (MIT) and a `THIRD_PARTY.md` stub.
4. `models.py`: the core Pydantic types (`Dependency`, `PackageMeta`, `RepoActivity`, `FeatureRow`, `Prediction`, `Explanation`, `ScanResult` with `status: ok|partial|error`).
5. `manifest.py`: read `package.json` (dependencies, devDependencies, optionalDependencies, peerDependencies, tagged by kind) and `package-lock.json` v2/v3 for `--transitive`. Tests use fixture manifests.
6. `depforecast doctor`: checks the GitHub token (rate limit query), cache dir, TabPFN token and weights, and Ollama reachability and model tag. Tokens are always redacted.
7. `.github/workflows/ci.yml`: ruff, mypy (core modules), pytest, and a guard that fails on files >50 MB or with weight extensions.

## M1: Collectors and cache
1. `collectors/cache.py`: on-disk cache in `~/.cache/depforecast/`, keyed by sha256(method + URL + body). `--offline` reads the cache only and raises a clear error on a miss.
2. A shared HTTP helper: timeouts, exponential backoff with jitter, retry on 429/5xx/secondary rate limits. Before writing it, I'll check the current GitHub GraphQL and npm registry docs.
3. `collectors/npm.py`: packument → versions with publish dates (`time`), the `deprecated` flag (latest version), repository URL (normalized to `owner/repo`, including monorepo `directory`), and `funding`.
4. `collectors/github.py`, using GraphQL with **date-bounded queries only**:
   - commit counts on the default branch via `history(since:, until:) { totalCount }`, using aliases to cover all the windows in one query
   - commit authors over the last 12 months (paginated `history` nodes, capped) for distinct authors and top-author share
   - `issueCount` from search with `created:`/`closed:` ranges; open PRs older than 90 days at T0 (`is:pr created:<T0-90d` minus those closed before T0)
   - `createdAt`, `archivedAt`, and `rateLimit { remaining resetAt }` on every query, with a sleep until reset when it gets low
   - batching: several repos per GraphQL request via aliases
5. Leakage guard: every collector function takes `as_of` and records the max date it queried. In window mode, a request whose upper bound is after T0 raises an error. (Label collection uses a separate, explicitly named path.)
6. Tests: recorded fixtures (respx) for npm and GitHub; rate-limit/backoff behavior; cache hit/miss/offline.

## M2: Dataset (checkpoint: class balance + 10 sample rows)
1. `data/seed_repos.txt`: ~200 popular JS/TS repos (apps and libraries). (D1)
2. Universe: union of direct deps from the seeds' `package.json` (fetched at a pinned commit as of ≤ the Window A T0, so the universe itself doesn't leak). Resolve to GitHub; log the skipped counts by reason.
3. `features(pkg, as_of)`, shared by training and inference: every feature in SPEC §5.5. The trend uses a +1 smoothing (D3).
4. Inclusion rule (alive at T0) and label (archived in the window, OR zero commits AND zero releases in the window). `deprecated` is kept as a separate column, never as the label.
5. `python -m depforecast.dataset build` → `data/train.csv` (A), `data/test.csv` (B), and a dataset hash.
6. Tests: feature math, label logic edge cases, the leakage guard.
7. **Stop and show you the class balance and 10 sample rows.**

## M3: Model and backtest (checkpoint: results table)
1. Check docs.priorlabs.ai for the current `tabpfn` version, `ModelVersion` names, the CPU row/feature guardrails, and the token flow. Pin the version.
2. `predictor.py`: a `Predictor` interface with `TabPFNPredictor(base|fast)` and a `FallbackPredictor` (gradient boosting). Fallback is automatic, and the model name, version and package version are recorded in every output.
3. `python -m depforecast.eval`: train on A, test on B, plus a random split for comparison. Baselines: the no-release-12mo rule, the no-commit-6mo rule, logistic regression, and gradient boosting. Scorecard: I'll check whether historical scores at T0 are obtainable; if not, the report says so.
4. Metrics: ROC-AUC, PR-AUC, precision and recall at the CLI threshold, a confusion matrix, and "caught X of Y packages that died". Charts (ROC, PR, calibration) go in `reports/*.png`.
5. Error analysis: the 10 worst false positives and 10 worst false negatives, with their features.
6. **Stop and show you the results table.**

## M4: CLI and reporters
1. `depforecast scan`: manifest → collectors → features (as_of = today) → predictor → explanation → reporter. Supports `--threshold`, `--format`, `--explain`, `--diff`, `--fail-on-risk`, `--model`, `--offline`, and `--transitive`.
2. Tiers: 🪦 RESTING (archived/deprecated, or already meets the dead definition), 👻 HAUNTING (≥ threshold), 🌼 ALIVE. Obituaries and the ofrenda come from evidence only.
3. Reporters: terminal (Rich), markdown, and json carry the same information. Snapshot test for markdown.
4. `--diff BASE..HEAD`: `git show` of both refs' `package.json` → added/changed deps only.
5. Per-package `status` and reason; no single failure fails the scan.

## M5: Explainer
1. Evidence bundle: the last ~20 issue titles, README lines mentioning deprecation or maintenance, and the latest release notes, each with a URL.
2. Ollama `/api/chat` with a JSON schema `format`. The model tag is configurable, defaulting to the newest small Gemma (currently `gemma3:4b` locally; I'll check the Ollama library for a newer one).
3. Guardrails: drop reasons without an evidence URL from the bundle; check alternatives against npm (they must exist and have a release in the last 12 months); retry once on invalid JSON, then fall back to the rules.
4. Rule-based explainer: deterministic text from the feature-vs-median comparison.

## M6: Action and demo
1. `action/action.yml` (composite): setup-python, cache `~/.cache/tabpfn` and `~/.cache/depforecast`, pip install, scan `--diff`, and a sticky comment via the REST API with `<!-- depforecast -->` as the hidden marker.
2. `.github/workflows/demo.yml` and a demo PR that adds a risky dependency. **This needs the repo pushed to GitHub (D5).**

## M7: Polish
1. README: install, usage, screenshots, backtest summary (numbers copied from `reports/backtest.md`, which is generated), limitations, "why open source", and licensing.
2. Scan 2–3 real public repos and save the outputs to `reports/`.

---

## Decisions (agreed)

- **D1: seed repos.** Picked by a fixed, rerunnable rule: the top N JS/TS repos by stars from GitHub search, filtered to those with a root `package.json`. Stars are used only to pick seeds, never as a feature.
- **D2: monorepos.** **One row per GitHub repo** (the most-depended-on package represents it), and `@types/*` is excluded, so giant monorepos don't dominate the "alive" class.
- **D3: trend.** `(c6 + 1) / (c_prev6 + 1)`, which avoids division by zero (0 and 0 → 1 = flat).
- **D4: CLI threshold.** Chosen on Window A only (never on B), saved as the default, and reported in `reports/backtest.md`. It can be overridden with `--threshold`.
- **D5: GitHub remote.** The user pushes the repo to GitHub (needed for M6).
- **D6: tokens.** `GITHUB_TOKEN` and `TABPFN_TOKEN` (plus `TABPFN_NO_BROWSER=1`) live in `.env`, which is gitignored. The TabPFN license is accepted at platform.priorlabs.ai/account/licenses; the key comes from /account/api-keys.
