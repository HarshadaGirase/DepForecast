# DepForecast 🌦️

**A weather forecast for your dependencies.** DepForecast predicts which of a project's npm dependencies are likely to be abandoned in the next 12 months. It explains why and runs entirely on your own machine or CI.

> Renovate keeps you current. DepForecast tells you when "current" is about to stop existing.

## Status: work in progress

The data foundation (milestones M0–M1) is complete and tested. The dataset build (M2) is underway. The model, the `scan` command, the explainer and the GitHub Action are planned (see [`PLAN.md`](PLAN.md)).

| Milestone | Status |
|---|---|
| M0: Setup: package, CI, `depforecast doctor`, manifest reader | ✅ Done |
| M1: Collectors: npm + GitHub GraphQL, disk cache, retries, rate limits, leakage guard | ✅ Done |
| M2: Dataset: 250 seed repos selected (`data/seed_repos.txt`); package universe building | 🚧 In progress |
| M3: TabPFN model + backtest against baselines | ⏳ Planned |
| M4: `depforecast scan` with terminal/markdown/json reports | ⏳ Planned |
| M5: Local Gemma explainer (Ollama) with rule-based fallback | ⏳ Planned |
| M6: GitHub Action PR comments | ⏳ Planned |

**Checks:** 125 tests pass; ruff and mypy are clean. CI runs them on every push (`.github/workflows/ci.yml`).

## What works today

```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'

depforecast --version
depforecast doctor          # checks GitHub token, cache, TabPFN, Ollama (tokens never printed)
pytest -q                   # 125 tests, fully offline (recorded real API responses)

# Developer-only dataset steps
python -m depforecast.dataset seeds      # top JS/TS repos -> data/seed_repos.txt
python -m depforecast.dataset universe   # their deps -> GitHub repos -> data/universe.csv
```

Tokens go in a `.env` file, which is gitignored: `GITHUB_TOKEN=...` and `TABPFN_TOKEN=...`.

## How it works (built so far)

- **Manifest reader:** reads `package.json` (all dependency sections) and `package-lock.json` v1–v3. It handles npm aliases and skips `file:`/`workspace:` dependencies.
- **npm collector:** release dates, per-version funding, the deprecated flag and the repo URL. It normalizes every GitHub URL style.
- **GitHub collector:** batched GraphQL (about 1 point per 10 repos) for:
  - commits in the 3, 6 and 12 months before a date
  - the bus factor
  - issues opened and closed
  - stale PRs
  - archive date
- **Leakage guard:** every feature query is checked before it's sent. A date after T0, or a "present-only" field such as stars, is blocked (SPEC §5.5).
- **Robust HTTP:** on-disk cache (reruns are free and work `--offline`), timeouts, exponential backoff, and GitHub rate-limit waits. Tokens are never cached or logged.

## Dataset design (SPEC §5)

- **Seeds:** the top 250 JavaScript/TypeScript repos by stars. Each `package.json` is read **as it was on 2024-10-01**, so the universe itself doesn't leak. Stars are used only to choose seeds, never as a feature.
- **Windows:** train on T0 = 2024-10-01 and test on T0 = 2025-04-01, with 12-month outcomes.
- **Label:** abandoned = archived in the outcome window, **or** zero commits **and** zero npm releases in it. npm `deprecated` is kept as a column, never as the label.
- **Monorepos:** one row per GitHub repo. `@types/*` is excluded.

## Why open source

- Your dependency list is a map of your attack surface, and DepForecast never sends it to a third-party service.
- It's free to run on every PR, so teams don't ration checks.
- A tool that warns about abandoned software shouldn't itself depend on a service that can disappear.
- Features, labels and data are in the repo, so you can inspect them, retrain and verify.

## License

MIT for this repository's code (see `LICENSE`). TabPFN weights are **non-commercial** (TABPFN-3-5 License), and Gemma follows Google's Gemma terms. See [`THIRD_PARTY.md`](THIRD_PARTY.md). Only free and local services are used.
