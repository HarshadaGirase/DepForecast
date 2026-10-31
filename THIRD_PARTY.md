# Third-party components

DepForecast's own code is MIT-licensed (see `LICENSE`). That license covers only code written in this repository. The components below have their own terms.

| Component | What we use | License / terms |
|---|---|---|
| `tabpfn` Python package | Prediction engine (normal pip dependency) | Apache 2.0 |
| TabPFN-3.5 / TabPFN-3.5-Fast model weights | Downloaded by each user to `~/.cache/tabpfn/` (or `TABPFN_MODEL_CACHE_DIR`) | TABPFN-3-5 License, **non-commercial**. Each user accepts it at platform.priorlabs.ai. For commercial use, get a Prior Labs commercial license or run with `--model fallback`. |
| Gemma model weights | Optional local explainer, pulled by the user via Ollama | Gemma Terms of Use (Google) |
| GitHub API data | Repository activity used to build features | GitHub Terms of Service. Only derived feature tables are committed. |
| npm registry data | Package versions, publish dates and metadata | npm Terms of Use. Only derived feature tables are committed. |

No model weights, HTTP caches or tokens are ever committed to this repository.
