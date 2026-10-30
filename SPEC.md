## 0. How I want you to work (read first)

- Plan before coding. Start by writing `PLAN.md` with the milestones below broken into tasks, then wait for my OK.
- Work milestone by milestone. At the end of each milestone: run the tests, commit with a clear message, and give me a 3-line summary.
- Commit small and often. This repo is judged partly on commit history; never squash.
- Never invent numbers. Every metric in the README or my post must come from a script in this repo that I can rerun.
- Check current docs before using an API: TabPFN (docs.priorlabs.ai), Ollama, the GitHub GraphQL API, and the npm registry API. Library versions change; don't rely on memory.
- If a design decision is expensive to reverse (data schema, label definition, model choice), stop and ask me.
- Never commit model weights, tokens or caches (see section 15).
- Deadline: Oct 31, 11:59 PM PDT. Prefer a smaller thing that works over a bigger thing that half works.

---

## 1. Mission

Abandoned dependencies are ghosts in the machine: code whose maintainers are gone but which still runs in production. Developers usually find out only after something breaks or a vulnerability goes unpatched.

**DepForecast** is a weather forecast for your dependencies. It predicts which of a project's dependencies are likely to be abandoned in the next 12 months. It explains why, suggests what to do, and runs entirely on the developer's own machine or CI.

**One line:** Renovate keeps you current. DepForecast tells you when "current" is about to stop existing.

## 2. Goals and non-goals

**Goals (MVP)**
1. A pip-installable Python **CLI**: `depforecast scan` reads a project's dependencies and prints a risk report.
2. A **prediction engine** using TabPFN (open weights, run locally) trained on real historical outcomes, with an honest backtest against baselines.
3. A local **explainer** using Gemma via Ollama that turns a risk score into evidence-based advice.
4. A **GitHub Action** that comments on PRs adding or upgrading risky dependencies.
5. Themed but informative output: graveyard report, obituaries for dead packages, and an "ofrenda" section with ways to help keep at-risk packages alive.

**Non-goals (do not build)**
- No web dashboard, no database server, no user accounts.
- No automatic code migration.
- Ecosystem: **npm only** for the MVP. Design the code so PyPI can be added later, but don't build it.

## 3. Form factor

- Python 3.11+ package with a CLI entry point (`depforecast`), built with Typer and Rich.
- Packaged with `pyproject.toml`; installable via `pipx install .`.
- A GitHub Action in `action/` that installs and runs the same CLI. There is one code path for local and CI use.

## 4. Architecture

```
                      ┌──────────────────────────────────────────────┐
  package.json   ───► │ 1. Manifest reader                           │
  package-lock.json   │    direct deps (default), --transitive option│
                      └───────────────┬──────────────────────────────┘
                                      ▼
                      ┌──────────────────────────────────────────────┐
                      │ 2. Collectors (with on-disk cache)           │
                      │    npm registry: versions + publish dates,   │
                      │      deprecated flag, repo URL, funding      │
                      │    GitHub GraphQL: commit counts by window,  │
                      │      authors, issues opened/closed, archived │
                      └───────────────┬──────────────────────────────┘
                                      ▼
                      ┌──────────────────────────────────────────────┐
                      │ 3. Feature builder  features(pkg, as_of_date)│
                      │    same function for training and inference  │
                      └───────────────┬──────────────────────────────┘
                                      ▼
            ┌─────────────────────────┴─────────────────────────┐
            ▼                                                   ▼
┌───────────────────────────┐                     ┌───────────────────────────┐
│ 4. Predictor (TabPFN)     │                     │ 5. Evidence bundle        │
│    in-context training set│                     │    recent issue titles,   │
│    = data/train.csv       │                     │    README/deprecation text│
│    → P(abandoned in 12mo) │                     │    last release notes     │
└─────────────┬─────────────┘                     └─────────────┬─────────────┘
              └──────────────────────┬──────────────────────────┘
                                     ▼
                      ┌──────────────────────────────────────────────┐
                      │ 6. Explainer (Gemma via Ollama, local)       │
                      │    strict JSON; may cite only the evidence   │
                      │    alternatives verified against npm registry│
                      │    falls back to rule-based text if offline  │
                      └───────────────┬──────────────────────────────┘
                                      ▼
                      ┌──────────────────────────────────────────────┐
                      │ 7. Reporters: terminal (Rich) | markdown |   │
                      │    json    →  CLI output / PR comment        │
                      └──────────────────────────────────────────────┘

  Offline pipeline (run once by me, results committed to repo):
  seed repos → package universe → snapshot features at T0 → labels at T1
  → data/train.csv, data/test.csv → eval script → reports/backtest.md
```

**Design rule:** collectors, features, predictor, explainer and reporters are separate modules with typed interfaces (Pydantic models). Each can be tested alone with fixtures.

## 5. The dataset (the most important part — be rigorous)

### 5.1 Package universe
- Pick ~150–300 popular, real-world JavaScript/TypeScript repos on GitHub (apps and libraries) and read their `package.json` files.
- Take the union of their **direct** dependencies. This makes the population "packages developers actually depend on", not random npm junk. Write the seed list to `data/seed_repos.txt` so it's reproducible.
- Keep packages that resolve to a GitHub repo. Log and skip the rest, with counts.

### 5.2 Time windows (temporal validation, no leakage)
- **Window A (training):** features as of `T0 = 2024-10-01`; outcome measured over `[2024-10-01, 2025-10-01)`.
- **Window B (test):** features as of `T0 = 2025-04-01`; outcome over `[2025-04-01, 2026-04-01)`.
- Train on A, evaluate on B. Report both the B results and a random-split result, and explain the difference.

### 5.3 Inclusion rule
Only include a package in a window if it was **alive at T0**: at least one commit to the default branch or one npm release in the 12 months before T0.

### 5.4 Label: abandoned = 1 if any of these hold
- The GitHub repo was archived during the outcome window (use GraphQL `archivedAt`), or
- Zero commits to the default branch **and** zero npm releases during the whole outcome window.

Write the exact label definition into `reports/backtest.md`. The npm `deprecated` flag has no timestamp, so record it as a separate column and do **not** use it as a label.

### 5.5 Features — computed strictly from data before T0
Implement `features(pkg, as_of)` and use it for both training and live scans:
- commits in last 3, 6 and 12 months
- trend: commits in last 6 months ÷ commits in the 6 months before that
- distinct commit authors in last 12 months
- top author's share of commits in last 12 months (bus factor)
- days since last commit; days since last npm release
- releases in last 12 months
- issues opened vs issues closed in last 6 months (use GraphQL search `issueCount` with `created:`/`closed:` date ranges)
- open PRs older than 90 days (if cheap to get; otherwise skip)
- repo age in years; whether the package has a funding field

**Forbidden features (leakage):** current star count, current downloads, current open-issue count, or anything else that reflects the present instead of T0. Add a test asserting that no collector call made for a window uses data after its T0.

### 5.6 Practicalities
- Prefer GitHub GraphQL; batch queries; respect rate limits (read `rateLimit { remaining resetAt }`, sleep until reset, retry with backoff).
- Cache every HTTP response on disk (`~/.cache/depforecast/`, keyed by URL + query), so reruns are free and work offline.
- Target 1,500–4,000 rows per window. Check TabPFN's current recommended row and feature limits in its docs and subsample if needed.
- Save the final CSVs to `data/` and commit them. The CLI ships with `data/train.csv` as TabPFN's in-context training set, so predictions are reproducible without re-collecting.

## 6. Model and evaluation

- **Predictor: TabPFN-3.5 (Base)**, run locally with the `tabpfn` package (pin the version in `pyproject.toml`; it is the package default, select it explicitly with `ModelVersion`). Prior Labs recommends the latest version for this challenge, and 3.5 is strongest on temporal data, which is exactly this problem.
  - Keep the in-context training set **under 5,000 rows** so it runs on CPU without overrides (the package's CPU guardrail). Subsample with a fixed seed if needed.
  - `--model fast` option uses **TabPFN-3.5-Fast** for quicker scans in CI.
  - Optional, only if time allows: one extra backtest run with **TabPFN-3.5-Thinking** via the hosted API, reported as a separate row. Never required to run the tool.
  - Weights need a one-time license acceptance and `TABPFN_TOKEN` (see section 15).
- **Fallback predictor:** if TabPFN weights are unavailable (no token, offline first run), use the gradient-boosting baseline trained on `data/train.csv`, clearly labelled `model: fallback` in every report. The tool must never fail just because TabPFN is missing.
- **Report the model used** (name, version, package version) in every scan output and in `reports/backtest.md`.
- **Baselines (must be in the eval):**
  1. Rule: no release in the last 12 months.
  2. Rule: no commit in the last 6 months.
  3. Logistic regression and a gradient-boosting model on the same features.
  4. If historical OpenSSF Scorecard scores are obtainable for T0 without leakage, include them as a feature-only baseline; if not, say so.
- **Metrics on Window B:** ROC-AUC, PR-AUC, precision and recall at the threshold used by the CLI, and a confusion matrix. Also "of the packages that actually died, how many did we flag" in plain words.
- **Error analysis:** list the 10 worst false positives and 10 worst false negatives with their features. I'll use these in the write-up.
- Output: `reports/backtest.md` plus charts in `reports/`, all generated by `python -m depforecast.eval`.

## 7. Explainer (Gemma, local)

- Call Ollama's local HTTP API. Default to the latest small Gemma model available in Ollama; check the exact tag with `ollama list` and make it configurable.
- **Input:** the risk score, the top contributing features (simple per-feature comparison against the dataset median is fine), and an evidence bundle: last ~20 issue titles, README text that mentions deprecation or maintenance, and the latest release notes.
- **Output:** strict JSON validated by Pydantic:
  `{summary, reasons: [{text, evidence_url}], alternatives: [pkg], ways_to_help: [text]}`
- **Guardrails:**
  - Every reason must cite a URL from the evidence bundle; drop reasons that don't.
  - Verify every suggested alternative exists on the npm registry and was released in the last 12 months; drop ones that don't.
  - On invalid JSON, retry once, then fall back to the rule-based explanation.
- If Ollama isn't running, use a deterministic rule-based explanation and print a one-line notice. Never crash.

## 8. Output and theme

Terminal report (Rich), sorted by risk:

```
🪦 RESTING     request        2011–2020  archived. Obituary: ...
👻 HAUNTING    xyz-parser     82% chance of abandonment within 12 months
                              why: one maintainer (94% of commits); no release in 15 months; issues 40 opened / 3 closed
                              help: sponsor · 3 "help wanted" issues · consider: abc-parser
🌼 ALIVE       fastify        6% risk
```

- **Obituary** (for already-dead packages): a short, respectful paragraph covering what the package did, who maintained it, how long it lived, and what replaced it. Facts only, from the evidence bundle.
- **Ofrenda** (for at-risk packages): ways to help keep it alive, such as the funding link, "help wanted" or "good first issue" counts, and a contributing guide link.
- Keep the theme in the labels and these sections. The numbers and reasons stay plain and serious.
- `--format json` and `--format markdown` must contain the same information as the terminal view.

## 9. CLI

```
depforecast scan [PATH]            # default: direct deps of PATH/package.json
    --transitive               # include lockfile transitive deps
    --threshold 0.6            # risk level that counts as HAUNTING
    --format terminal|markdown|json
    --explain auto|gemma|rules|off
    --diff BASE_REF..HEAD_REF  # only deps added/changed between git refs (used by the Action)
    --fail-on-risk             # exit code 1 if any dep exceeds threshold
depforecast explain PACKAGE        # deep dive on one package
depforecast doctor                 # checks GitHub token, cache, TabPFN token/weights, Ollama
    --model base|fast|fallback  # (on scan) TabPFN-3.5, TabPFN-3.5-Fast, or gradient boosting
```

Developer-only commands live under `python -m depforecast.dataset` and `python -m depforecast.eval`.

## 10. GitHub Action

- `action/action.yml` as a composite action:
  1. set up Python
  2. restore the cache
  3. `pip install` depforecast
  4. `depforecast scan --diff ${{ base }}..${{ head }} --format markdown --explain rules`
  5. post or update **one** sticky PR comment, using a hidden HTML marker to find and edit it
- Use `GITHUB_TOKEN`. Read `TABPFN_TOKEN` from a repository secret; if it is missing, use the fallback predictor and say so in the comment. Exit successfully if no dependencies changed.
- Cache `~/.cache/tabpfn/` between runs with `actions/cache` so weights download once.
- Default to `--explain rules` in CI, since runners have no GPU; Gemma stays optional via an input.
- Include `.github/workflows/demo.yml` that runs the action on this repo's PRs, plus a demo PR that adds a risky dependency.

## 11. Robustness ("it doesn't break")

- No single package failure fails the scan: each result has `status: ok | partial | error` plus a reason.
- Every network call has a timeout, retry with exponential backoff, and rate-limit awareness.
- Disk cache, with `--offline` to use only cached data.
- Missing GitHub token: run with lower limits and print a clear hint (`depforecast doctor`).
- Non-GitHub repos or missing repo URL: score from npm-only features if possible, otherwise `status: partial`.
- Fix random seeds; record TabPFN version and dataset hash in report output for reproducibility.
- Tests with pytest. Record HTTP fixtures for collector tests so they run offline. Add unit tests for feature math, label logic and the leakage guard, and a snapshot test for the markdown report.
- CI workflow runs lint (ruff), type check (mypy on core modules) and tests on every push.

## 12. Repo layout

```
depforecast/
  cli.py            manifest.py        models.py   (Pydantic types)
  collectors/       npm.py  github.py  cache.py
  features.py       predictor.py       explainer.py
  reporters/        terminal.py  markdown.py  json.py
  dataset.py        eval.py
data/               seed_repos.txt  train.csv  test.csv
reports/            backtest.md  *.png
action/             action.yml
tests/              fixtures/  test_*.py
.github/workflows/  ci.yml  demo.yml
README.md  SPEC.md  PLAN.md  pyproject.toml  LICENSE (MIT)  THIRD_PARTY.md  .gitignore
```

## 13. Milestones (deadline Oct 31)

- **M0 — Setup (Oct 27):** `PLAN.md`, repo skeleton, CI, `depforecast doctor`, manifest reader with tests.
- **M1 — Collectors and cache (Oct 27–28):** npm and GitHub collectors, rate limiting, fixtures.
- **M2 — Dataset (Oct 28):** seed list, universe, features at T0, labels, CSVs, leakage test. **Checkpoint: show me class balance and 10 sample rows before moving on.**
- **M3 — Model and backtest (Oct 29):** TabPFN, baselines, `reports/backtest.md`, error analysis. **Checkpoint: show me the results table.**
- **M4 — CLI and reports (Oct 29–30):** scan, terminal/markdown/json reporters, obituary and ofrenda sections.
- **M5 — Explainer (Oct 30):** Gemma via Ollama with guardrails and fallback.
- **M6 — Action and demo (Oct 30):** action, demo workflow, demo PR.
- **M7 — Polish (Oct 31):** README with install, usage, screenshots, backtest summary, limitations, and a "why open source" section; run on 2–3 real public repos and save the outputs in `reports/`.

**Cut order if time runs short:** transitive deps → `depforecast explain` → Gemma (keep the rule-based explanation) → Action. Never cut the backtest.

## 14. README "why open source" section (draft for me to edit)

- Your dependency list is a map of your attack surface; DepForecast never sends it to a third-party service.
- It's free to run on every PR, so teams don't ration checks.
- A tool that warns about abandoned software shouldn't itself depend on a service that can disappear.
- Features, labels and data are in the repo: inspect, retrain on your ecosystem, verify the claims.
- Caveat: TabPFN's weights are open but under a non-commercial license; Gemma's are under Google's Gemma terms. Say so plainly.

## 15. Licensing and what never goes in the repo

**Our code:** MIT (`LICENSE`). This covers only code written in this repo.

**Third parties** — list in `THIRD_PARTY.md` and the README:
- `tabpfn` package code: Apache 2.0 (a normal dependency; compatible with MIT).
- TabPFN-3.5 / 3.5-Fast **model weights**: TABPFN-3-5 License, **non-commercial**. Each user accepts it at platform.priorlabs.ai and downloads the weights themselves. Predictions from TabPFN are for non-commercial use; teams using DepForecast commercially need a Prior Labs commercial license or should use `--model fallback`.
- Gemma weights: Gemma terms of use; pulled by the user via Ollama.
- Data from the GitHub and npm APIs: subject to their terms; we commit only derived feature tables.

**`.gitignore` must include** (create it in M0, before the first download):
```
*.safetensors
*.ckpt
*.pt
*.pth
*.gguf
.cache/
tabpfn_cache/
.env
.env.*
```
- Never commit TabPFN weights (they live in `~/.cache/tabpfn/` or `TABPFN_MODEL_CACHE_DIR`), Ollama/Gemma model files, the HTTP cache, or any token (`TABPFN_TOKEN`, `GITHUB_TOKEN`, DEV API key).
- Tokens come only from environment variables or GitHub secrets. Never print them; redact them in logs.
- Add a CI check that fails if any file over 50 MB or with a weight extension is committed.
- Committing `data/*.csv` (derived features and labels) and `reports/` is fine.