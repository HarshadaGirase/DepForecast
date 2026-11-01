# DepForecast: A Weather Forecast for the Ghosts in Your `node_modules` (work in progress)

*This is a submission for the [Hacktoberfest Open-Source AI Challenge Week 4: Ghost in the Machine](https://dev.to/challenges/hacktoberfest-week4-2026-10-26)*

## What I Built

Abandoned dependencies are ghosts in the machine: code whose maintainers are gone but which still runs in production. Developers usually find out only after something breaks or a vulnerability goes unpatched.

**DepForecast** is a weather forecast for your dependencies. It's a local, open-source CLI that will predict which of a project's npm dependencies are likely to be abandoned in the next 12 months, explain why, and suggest ways to help keep them alive.

> Renovate keeps you current. DepForecast tells you when "current" is about to stop existing.

The theme shows up in the output design: 🪦 **RESTING** for dead packages (with a short, factual obituary), 👻 **HAUNTING** for at-risk ones, and 🌼 **ALIVE** for healthy ones. There's also an *ofrenda* section with ways to help: funding links, "help wanted" issues and contributing guides.

**Status: work in progress.** I built the data foundation first, because a prediction is only as honest as the data behind it. What's done and tested:
- Collectors for the npm registry and the GitHub GraphQL API, with a disk cache, retries and rate-limit handling
- A **leakage guard** that blocks any training query from looking at data after its snapshot date
- A real dataset universe: **250 popular JS/TS repos → 5,118 distinct dependencies → 3,719 GitHub repos**

The TabPFN predictor, the backtest, the `scan` command and the Gemma explainer are designed in `PLAN.md` and are next.

## Demo

No deployed link yet, since it's a local CLI. Here's what runs today:

![Image description](https://dev-to-uploads.s3.us-east-2.amazonaws.com/uploads/articles/y3pwwhzf13fk1t7cgtma.png)

![Image description](https://dev-to-uploads.s3.us-east-2.amazonaws.com/uploads/articles/g7nl88eimu1ngvk126g1.png)

The real dataset

![Image description](https://dev-to-uploads.s3.us-east-2.amazonaws.com/uploads/articles/ql6dec0ycmxi8fizieq1.png)

The leakage guard working

![Image description](https://dev-to-uploads.s3.us-east-2.amazonaws.com/uploads/articles/uchagngaetr6qij8pg9h.png)

## Code

{% github HarshadaGirase/DepForecast %}

## How I Built It

**Open-source AI (planned core):**
- **TabPFN-3.5 (open weights, run locally)** is the predictor. It's a tabular foundation model that learns in-context from `data/train.csv`, which suits a small, honest dataset where training a deep model from scratch would overfit. There's a gradient-boosting fallback, so the tool never fails without the weights.
- **Gemma (`gemma3:4b`) via Ollama, run locally**, will turn a risk score into a plain-English explanation. Its guardrails: every reason must cite a URL from the evidence (issue titles, README, release notes), and any suggested alternative must exist on npm with a release in the last 12 months. If Ollama is offline, it falls back to rule-based text.

**What's built (Python 3.11, Typer, Rich, Pydantic, httpx, pytest):**
- **Temporal validation, no leakage:** train on snapshots at 2024-10-01 and test on 2025-04-01, each with a 12-month outcome window. Even the seed repos' `package.json` files are read *as they were* on 2024-10-01, so the population itself doesn't leak the future.
- **The label, defined honestly:** abandoned = the repo was archived during the window, **or** it had zero commits **and** zero npm releases in the whole window. npm's `deprecated` flag has no timestamp, so it's kept as a column, never as the label.
- **Features:** commits in the last 3/6/12 months, trend, distinct authors, bus factor (the top author's share of commits), days since last commit and release, issues opened vs. closed, stale PRs, repo age and funding. All are computed strictly from data before the snapshot date.
- **Efficient and free:** batched GraphQL costs about 1 point per batch of up to 10 repos, and every response is cached on disk, so reruns are free and work offline.
- **Monorepos:** collapsed to one row per repo (209 of them), and `@types/*` is excluded.

I built it with an AI coding agent (Claude Code), working in small steps: 22 commits, each with tests.

## Why Does Open Innovation Matter?

- **Your dependency list is a map of your attack surface.** DepForecast runs locally and never sends it to a third-party service.
- **It's free to run on every PR**, so teams don't ration checks. The whole stack is free: the GitHub API, the npm registry, local TabPFN, local Gemma and GitHub Actions.
- **A tool that warns about abandoned software shouldn't itself depend on a service that can disappear.** Open weights on your own machine can't be shut down or repriced.
- **Verifiable claims:** the features, the label definition and the data tables are in the repo. You can inspect them, retrain on your ecosystem and check every number.
- **Honest caveat:** TabPFN's weights are open but under a **non-commercial** license, and Gemma follows Google's Gemma terms. Both are documented in `THIRD_PARTY.md`.

## My Agent Session

[Link or embed your DevRelay session here]

## The Spirit of It

While testing the collector, I asked GitHub about `request`, one of the most famous packages in npm history. The answer was quiet: last commit **February 11, 2020**. Zero commits in the year before my snapshot date, and zero after. It was never archived. The repo is still there and still looks like a living project, but npm now says *"request has been deprecated."*

That's the ghost: code that looks alive while nobody is home. DepForecast exists so the next `request` shows up as 👻 HAUNTING in your terminal *before* it becomes 🪦 RESTING in your production logs. And the ofrenda section is there so developers can do something about it while the maintainers are still around.

## Prize Categories

- **Best Use of TabPFN.** DepForecast is designed around **TabPFN-3.5 (open weights, run locally)** as its predictor: it learns in-context from historical snapshots and outputs P(abandoned within 12 months). The pipeline that produces TabPFN's training set is built and tested: temporal windows, the label definition, the leakage guard and the 3,719-repo universe. `depforecast doctor` already verifies the TabPFN license and token setup. *Status: the TabPFN predictor and its backtest against baselines are the next milestone.*
- **Best Use of Gemma.** **`gemma3:4b`, running locally via Ollama**, is the planned explainer. It will turn a risk score into evidence-cited advice, with a guardrail that drops any reason without a source URL. `depforecast doctor` verifies that Ollama is running and `gemma3:4b` is available. *Status: the explainer integration is planned.*
- **Best Use of GitHub Copilot (GitHub Actions).** **GitHub Actions** automates the project: every push runs ruff, mypy, all 125 tests, and a guard that blocks committing model weights or files over 50 MB (`.github/workflows/ci.yml`). The planned DepForecast Action will comment on PRs that add risky dependencies. *Note: I built this with Claude Code rather than Copilot, and I'm entering for the GitHub Actions automation.*
