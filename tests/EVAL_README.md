# PaperMatcher — Model Evaluation

## Overview

Two rounds of evaluation against a labeled set of 92 papers, testing Pass 1 screening recall and Pass 2 scoring accuracy across multiple local and cloud models.

---

## Dataset

- **92 papers** labeled from a personal reference library (single research area, one keyword set)
- **Labels:** `relevant` (43), `borderline` (36), `irrelevant` (13)
- Labeling was done manually by the researcher prior to running any pipeline config
- Raw papers (titles, abstracts) are not included in this repo; only anonymized per-paper scores are provided in `eval_results_sanitized.csv`

Note: 2 papers appear twice in the labeled set (duplicate entries); scores for these use the first occurrence only.

---

## Round 1 — Original benchmark (2026-05-12)

Four configs tested, mixed local and cloud Pass 2 models.

| Config tag | Pass 1 model | Pass 2 model |
|---|---|---|
| `llama3.2_both` | `llama3.2:latest` (local) | `llama3.2:latest` (local) |
| `llama3.2_p1_OCRFast_p2` | `llama3.2:latest` (local) | `baidu/qianfan-ocr-fast:free` (OpenRouter) |
| `llama3.2_p1_Ring_p2` | `llama3.2:latest` (local) | `inclusionai/ring-2.6-1t:free` (OpenRouter) |
| `OCRFast_p1_Ring_p2` | `baidu/qianfan-ocr-fast:free` (OpenRouter) | `inclusionai/ring-2.6-1t:free` (OpenRouter) |

All configs ran with `temperature=0.1`, `seed=42` (local Ollama only), `max_tokens=300` for Pass 2.

Note: OCRFast and Ring both went paid after this benchmark. Replaced by `deepseek/deepseek-v4-flash:free` in the recommended config.

---

## Round 2 — Local model survey (2026-05-19)

### Pass 1 candidates

All models tested against the full 92-paper set using `--pass1-only`.

| Model | Size | P1 Recall | FNs | Avg time/paper | Verdict |
|---|---|---|---|---|---|
| `llama3.2:latest` | 2.0 GB | **100%** | 0 | ~0.3s | ✅ Best — baseline |
| `llama3.1:8b` | 4.9 GB | **100%** | 0 | 1.7s | Matches baseline, 6× slower |
| `mistral:7b` | 4.4 GB | **100%** | 0 | 1.8s | Matches baseline, 6× slower, overheating risk |
| `gemma3:4b` | 3.3 GB | 93.0% | 3 | 0.9s | Below 95% target |
| `granite3.3:2b` | 1.5 GB | 69.8% | 13 | 0.7s | Too many false negatives |
| `nemotron-3-nano:4b` | 2.8 GB | 40.0% | 3 | 9.8s | Poor recall + slow |
| `phi4-mini-reasoning:3.8b` | 3.2 GB | 60.0% | 2 | 18.0s | Too slow for screening |
| `qwen3.5:0.8b` | 1.0 GB | 0% | 5 | 10.2s | All UNCLEAR — failed entirely |

**Winner: `llama3.2:latest`.** Nothing local beats it. Models that match its recall (llama3.1:8b, mistral:7b) are 6× slower with no benefit for a yes/no screening task.

### Pass 2 candidates

All tested with `llama3.2:latest` Pass 1, using `--reuse-pass1`. Results evaluated at multiple thresholds.

| Model | Size | t=3 E2E/Irr | t=4 E2E/Irr | t=5 E2E/Irr | t=6 E2E/Irr | t=7 E2E/Irr | Time |
|---|---|---|---|---|---|---|---|
| `llama3.2:latest` | 2.0 GB | 100%/67% | 100%/67% | 98%/67% | **98%/67%** | 77%/33% | ~5.4m |
| `gemma3:4b` | 3.3 GB | 100%/100% | 98%/100% | 93%/67% | 91%/67% | 74%/33% | 6.6m |
| `granite3.3:8b` | 4.9 GB | **88%/33%** | 81%/33% | 81%/33% | 72%/33% | 70%/33% | 15.1m |
| `llama3.1:8b` | 4.9 GB | 100%/100% | 98%/67% | 84%/33% | 72%/33% | 58%/33% | 10.2m |
| `mistral:7b` | 4.4 GB | 100%/67% | 74%/33% | 74%/33% | 72%/33% | 65%/33% | 12.2m |
| `qwen3.5:4b` | 3.4 GB | — | — | — | — | — | — |
| `qwen3.5:9b` | 6.6 GB | — | — | — | — | — | — |

**E2E/Irr** = end-to-end recall on relevant papers / irrelevant pass-through rate, at each threshold t.
`qwen3.5` models returned empty scores in smoke tests — did not follow the scoring format.

---

## Recommended configs

| Use case | P1 | P2 | Threshold | E2E | Irr pass-through | Time |
|---|---|---|---|---|---|---|
| **Cloud (recommended)** | llama3.2 | deepseek/deepseek-v4-flash:free | t=4 | 86% | 33% | ~4.4m |
| **Local, max recall** | llama3.2 | llama3.2 | t=6 | 98% | 67% | ~5.4m |
| **Local, less noise** | llama3.2 | granite3.3:8b | t=3 | 88% | 33% | ~15m |

The cloud config is recommended as the default. The local max-recall config (llama3.2 both) is the best fully-offline option — its 98% E2E at t=6 is the highest of any config tested, at the cost of more irrelevant papers reaching the human review queue. The granite config matches cloud precision but is 3× slower.

---

## Sanitized results

`eval_results_sanitized.csv` — anonymized per-paper scores for the Round 1 configs. No titles, abstracts, or authors included.

| Column | Description |
|---|---|
| `paper_id` | Integer 1–92 (row index in original labeled set) |
| `true_label` | Human-assigned label: `relevant`, `borderline`, or `irrelevant` |
| `{config}_pass1` | Pass 1 decision: `YES`, `MAYBE`, or `NO` |
| `{config}_p2_score` | Pass 2 relevance score (1–10). Empty if filtered by Pass 1 |

Papers with no p2 score in any config (n=17) were consistently filtered by Pass 1 — these are the most clearly off-topic papers in the set.

---

## Caveats

- Results are from a single research area and keyword set. Performance on other fields has not been tested.
- Pass 1 recall of 100% for llama3.2 configs was measured on this specific dataset and should not be taken as a general guarantee.
- `llama3.2:latest` as Pass 2 has high recall but limited score discrimination — most papers score in a narrow band, making the 67% irrelevant pass-through unavoidable at any threshold that preserves recall.
- Run times are approximate and depend on hardware (tested on Apple Silicon, 24GB RAM).
- `qwen3.5` models (0.8b, 4b, 9b) were not successfully evaluated: 0.8b returned all UNCLEAR responses; 4b and 9b did not follow the scoring format in smoke tests.

---

## Eval script

`tests/two_pass_eval.py` — supports `--pass1-only`, `--reuse-pass1`, `--smoke`, and auto-stop on model failure (UNCLEAR streak or slow calls). See `tests/TEST_COMMANDS.md` for full command reference.
