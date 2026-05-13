# PaperPilot — Model Evaluation

## Overview

Four pipeline configurations were tested against a labeled set of 92 papers to measure Pass 1 recall, Pass 2 scoring accuracy, and end-to-end precision at different thresholds.

---

## Dataset

- **92 papers** labeled from a personal reference library (single research area, one keyword set)
- **Labels:** `relevant` (43), `borderline` (36), `irrelevant` (13)
- Labeling was done manually by the researcher prior to running any pipeline config
- Raw papers (titles, abstracts) are not included in this repo; only anonymized per-paper scores are provided in `eval_results_sanitized.csv`

Note: 2 papers appear twice in the labeled set (duplicate entries); scores for these use the first occurrence only.

---

## Configs tested

| Config tag | Pass 1 model | Pass 2 model |
|---|---|---|
| `llama3.2_both` | `llama3.2:latest` (local) | `llama3.2:latest` (local) |
| `llama3.2_p1_OCRFast_p2` | `llama3.2:latest` (local) | `baidu/qianfan-ocr-fast:free` (OpenRouter) |
| `llama3.2_p1_Ring_p2` | `llama3.2:latest` (local) | `inclusionai/ring-2.6-1t:free` (OpenRouter) |
| `OCRFast_p1_Ring_p2` | `baidu/qianfan-ocr-fast:free` (OpenRouter) | `inclusionai/ring-2.6-1t:free` (OpenRouter) |

All configs ran with `temperature=0.1`, `seed=42` (local Ollama only), `max_tokens=300` for Pass 2.

---

## Sanitized results: `eval_results_sanitized.csv`

Each row is one paper. No titles, abstracts, or authors are included.

**Columns:**

| Column | Description |
|---|---|
| `paper_id` | Integer 1–92 (row index in original labeled set, no other meaning) |
| `true_label` | Human-assigned label: `relevant`, `borderline`, or `irrelevant` |
| `{config}_pass1` | Pass 1 decision: `YES`, `MAYBE`, or `NO`. Papers where Pass 1 returned `NO` were not sent to Pass 2. |
| `{config}_p2_score` | Pass 2 relevance score (1–10). Empty if filtered by Pass 1. |

Papers with no p2 score in any config (n=17) were consistently filtered by Pass 1 — these are the most clearly off-topic papers in the set.

---

## Summary results

| Config | P1 recall (relevant) | E2E recall @t=4 | Irrelevant pass-through | Run time |
|---|---|---|---|---|
| `llama3.2_both` | 100% | 97.7% @t=6 | 67% | ~5.4 min |
| `llama3.2_p1_OCRFast_p2` | 100% | 86.0% | 33% | ~4.4 min |
| `llama3.2_p1_Ring_p2` | 100% | 74.4% | 0% | ~6.6 min |
| `OCRFast_p1_Ring_p2` | 95.3% | 76.7% @t=6 | 8% | — |

**@t=N** = threshold: articles with Pass 2 score below N are discarded. Each config was evaluated at the threshold that maximized E2E recall while keeping irrelevant pass-through acceptable.

---

## Selected config

**`llama3.2_p1_OCRFast_p2` at threshold 4** — best precision/recall tradeoff: zero irrelevant papers above threshold, 86% recall on relevant papers, fastest cloud option.

`llama3.2_both` at threshold 6 is recommended as a fully local fallback (no API needed), but has high irrelevant pass-through due to limited score discrimination.

---

## Caveats

- Results are from a single research area and keyword set. Performance on other fields has not been tested.
- `llama3.2:latest` as Pass 2 has high recall but poor score discrimination — most papers score in a narrow band, making threshold selection less reliable.
- Pass 1 recall of 100% for llama3.2 configs was measured on this specific dataset and should not be taken as a general guarantee.
- Run times are approximate and depend on hardware and network conditions.

---

## Eval script

`tests/two_pass_eval.py` — runs all four configs against a labeled CSV and outputs per-paper scores + a comparison summary.
