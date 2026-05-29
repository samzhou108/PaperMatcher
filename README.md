<div align="center">

```
  📄 ──── ❤️  ──── 📄
     P A P E R
   M A T C H E R
  📄 ──── ✖️  ──── 📄
```

### *Like Tinder, but for journal articles*

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-macOS-000000.svg?logo=apple&logoColor=white)]()
[![Built with Claude](https://img.shields.io/badge/Built%20with-Claude%20Sonnet-blueviolet.svg)](https://claude.ai)
[![PubMed](https://img.shields.io/badge/Data-PubMed%20%2F%20NCBI-326599.svg)](https://pubmed.ncbi.nlm.nih.gov)

I created this project by myself in my spare time as a tool to help other trainee/early career reseachers like me. If you found it useful, please consider tipping me on Ko-fi to support this work! Thank you!
[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/U5A520EZ8P)

**A free or low-cost, privacy-first literature triage tool for biomedical researchers.**

</div>

---

## The Problem

The number of scientific publications being produced each year increases exponentially. Keeping track and staying up to date with the most relevant literature can be overwhelming. The current available tools leave much to be desired:

- Email subscriptions, keyword alerts, and Journal RSS often give an adequate overview of the field, but also produce a lot of noise (most articles are tangentially related to your topic(s) of interest).

- PubMed searches often produce too many irrelevant results, or if the query is too specific they provide no results. Scanning every abstract manually for relevance to your research question takes a lot of time.

- Existing AI tools either cost money or send your data to remote servers, or both (e.g. Perplexity). Furthermore, most consumer AI tools only search for around 10–20 sources claimed to be "high-quality", which does not provide an adequate overview of the field and potentially misses relevant articles. You can chain more searches, but your token usage/credits will be depleted quickly.

PaperMatcher takes a different approach: it performs a well-crafted PubMed search, runs a fast relevance filter locally, then for scoring it either uses a larger local model or escalates to a cloud model only with user permission, and keeps data (your research questions and literature curation) stored on your machine as much as possible.

---

## How It Works

```
Your keywords + research profile
        ↓
PubMed search (NCBI E-utilities + MeSH expansion)
        ↓
Pass 1 — local LLM screens for topic relevance (~0.3s/article, free)
        ↓
Pass 2 — configurable model scores 1–10 against your profile
        ↓
Articles above threshold → structured analysis + tags
        ↓
Tinder-style review queue → keep / reject
        ↓
SQLite database on your machine
```

**Pass 1** runs entirely locally via Ollama. It asks one question per article: *is the paper on-topic?* This process eliminates the majority of results before any cloud call is made.

**Pass 2** runs on whichever model you configure. The model scores each surviving article on a scale of 1–10 against either your research question or research profile and generates a structured analysis: summary, implications, methodology, conflict-of-interest flag, reproducibility estimate, and tags.

**Human in the loop review process:** The last step before saving the papers to your local database is a Tinder-like review interface, where you can read a summary, the abstract, and a relevance statement for each selected paper. You then make a decision to either accept or reject the paper. The decision is stored on your device for future searches with the same or similar keywords.

Only articles that pass your review are saved.

---

## Installation

### For End Users (Recommended)

See **[DISTRIBUTION.md](DISTRIBUTION.md)** for installation instructions.

**Quick start:** Download `PaperMatcher_v1.0.0.dmg` → drag to Applications → done. No coding required.

### For Developers

To build from source:

```bash
git clone https://github.com/samzhou108/PaperMatcher
cd PaperMatcher
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 main.py
```

For packaging your own build, see [PACKAGING.md](PACKAGING.md).

First launch opens a 3-step onboarding screen (Profile → PubMed Settings → LLM). Subsequent launches go straight to the main window.

---

## Zero-Cost Setup

**Local (fully offline after setup):**

1. Install [Ollama](https://ollama.ai) and pull `llama3.2:latest`
2. Run PaperMatcher — no API key required

**Cloud scoring (free tier, recommended):**

1. Create a free [OpenRouter](https://openrouter.ai) account and generate an API key
2. Enter the key in Settings → the free tier gives ~50 requests/day with `deepseek/deepseek-v4-flash:free`

> **Rate limit note:** The free tier is throttled heavily — in practice you may hit the limit after fewer than 10 articles scored. Adding a \$10 USD credit to your OpenRouter account raises the daily limit to 1,000 requests with far less throttling. For typical runs of 20–50 articles, even with paid models, \$10 lasts a very long time.

---

## LLM Configuration

### Pass 1 — Screening (local, always free)

| Model             | Size        | Notes                                                                        |
| ----------------- | ----------- | ---------------------------------------------------------------------------- |
| `llama3.2:latest` | 3B / 2.0 GB | **Recommended.** 100% recall on 92-paper benchmark at ~0.3s/paper.          |
| `gemma3:4b`       | 4B / 3.3 GB | 93% recall — usable but misses more papers than llama3.2.                   |
| `mistral:7b`      | 7B / 4.4 GB | 100% recall but 6× slower than llama3.2 with no benefit for a yes/no task.  |
| `llama3.1:8b`     | 8B / 4.9 GB | 100% recall but 6× slower than llama3.2 with no benefit for a yes/no task.  |

Models above ~5 GB are not recommended — they slow screening significantly on consumer hardware. No model tested improved on `llama3.2:latest` for this task.

### Pass 2 — Scoring + Summarization (configurable)

#### Online

**Best free option:**

| Model                             | Notes                                                                                      |
| --------------------------------- | ------------------------------------------------------------------------------------------ |
| `deepseek/deepseek-v4-flash:free` | No prompt training required, but no "Zero Data Retention" (ZDR) policy. Fast, 1M context. |

No training on prompt data can be enabled in OpenRouter > Guardrails > Model & Provider Access

**Other free options** (require enabling prompt training: OpenRouter → Settings → Privacy → Allow prompt training):

| Model                                                | Notes                                     |
| ---------------------------------------------------- | ----------------------------------------- |
| `openrouter/owl-alpha:free`                          | Requires prompt training consent, no ZDR. |
| `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` | Requires prompt training consent, no ZDR. |

**Paid options (no daily limit, no prompt training, and ZDR):**

| Model                        | Input / Output per 1M tokens (USD) |
| ---------------------------- | ---------------------------------- |
| `deepseek/deepseek-v4-flash` | \$0.126 / \$0.252                  |
| `deepseek/deepseek-v4-pro`   | \$0.435 / \$0.87                   |

> Free model availability changes frequently. Table current as of May 2026.

#### Local

Set API Base URL to `http://localhost:11434` and enter any Ollama model name. No API key, no rate limit, fully offline.

| Model             | Size        | Threshold | E2E   | Irr pass-through | Notes                                      |
| ----------------- | ----------- | --------- | ----- | ---------------- | ------------------------------------------ |
| `llama3.2:latest` | 3B / 2.0 GB | t=6       | 98%   | 67%              | **Best local recall.** High noise to review. |
| `granite3.3:8b`   | 8B / 4.9 GB | t=3       | 88%   | 33%              | Best local precision. Matches cloud noise level, 3× slower. Pull: `ollama pull granite3.3:8b` |
| `gemma3:4b`       | 4B / 3.3 GB | t=6       | 91%   | 67%              | Fast, but no advantage over llama3.2 locally. |

---

## Features

**Search & query**

- MeSH term expansion via NCBI E-utilities — keywords automatically mapped to canonical medical subject headings
- ✨ **LLM query generation** — describe your search focus in plain language; an LLM constructs a structured PubMed query with correct Boolean logic and MeSH terms. Uses your cloud model when configured, falls back to local llama3.2
- **Query validation** — warnings shown in the preview when the generated query has patterns likely to return zero results (too many AND conditions, long literal phrases)
- **Auto-broadening** — if a query returns zero results, AND constraints are automatically relaxed one at a time until results are found
- Advanced search panel — Must Include (`AND [Title/Abstract]`), Include to Expand (OR block), Do Not Include terms
- Publication type filter — Review, Original Research, Clinical Trial, Meta-Analysis, Systematic Review
- Configurable lookback period — entry + slider with piecewise scale (days/weeks → months → years up to 20)

**Pipeline**

- Two-pass LLM scoring with configurable threshold
- Feedback injection — past accepted/rejected article context is injected into the Pass 2 scoring prompt on subsequent runs
- Topic-scoped rejection memory — articles you reject are stored with the search keywords active at rejection time. A rejected article is only skipped in future runs if the new search has substantial keyword overlap with the original rejection context.

**Review popup**

- One article at a time, after each pipeline run
- Shows: summary, abstract with keyword highlights, why it's relevant, implications, methodology, reproducibility score (1–5), conflict-of-interest flag
- **Accept** — article saved to your database with explicit approval feedback
- **Reject** — article deleted from database immediately; stored with search context for future filtering
- **Skip** — article kept without explicit feedback; can be re-reviewed later
- Keyboard shortcuts: `→` accept, `←` reject, `↓` skip

**Results tab**

- Compact list — title, author(s), journal, year, score
- Click any row to expand: full summary, implications, methodology, reproducibility, relevance reason, tags, action buttons
- Edit, delete, copy, export CSV

**Settings**

- Pass 2 model dropdown auto-detects installed Ollama models and shows cloud presets based on your API URL
- 💡 Suggested Setup guide — Ollama model recommendations, OpenRouter setup steps, rate limit explanation
- Ollama model installer — download curated models directly from the app

**Profile**

- Research profile (role, keywords, fields of interest) personalises Pass 2 scoring
- MeSH cache manager — view and delete cached NCBI term lookups

---

## Design Decisions

**Why a two-pass pipeline?**
A 3B local model answering yes/no costs nothing and runs in ~0.3 seconds per article. It removes 60–80% of results before any API call is made, which matters on overall speed and saving costs (either RAM or \$) for the Pass 2 model.

**Why local-first?**
Researchers working on sensitive or unpublished work shouldn't need to send their research interests to a third-party server. Pass 1 is always local. Pass 2 cloud calls are opt-in. The full pipeline works offline with Ollama for both passes.

**Topic-scoped rejection instead of global blacklist**
A paper irrelevant to your research may be directly relevant to a different project. Rejection is stored with the search keywords that were active when you rejected it. Future runs only skip the paper if there's meaningful keyword overlap — otherwise it's treated as new.

**Auto-broadening instead of failing on zero results**
LLMs building PubMed queries sometimes over-constrain with too many AND conditions. Rather than returning nothing, the scraper strips AND clauses one at a time until results appear, then logs how many constraints were dropped.

**Why SQLite?**
No server, no setup. `~/.papermatcher/papermatcher.db` is a single file you can back up, inspect with any SQLite browser, or delete to start fresh.

**Benchmark results (92-paper test set, 7 Pass 1 and 5 Pass 2 models evaluated):**

| Config | Threshold | E2E Recall | Irr. pass-through | Time |
| ------ | --------- | ---------- | ----------------- | ---- |
| llama3.2 P1 + **DeepSeek P2** (cloud) | t=4 | 86% | 33% | ~4.4 min |
| **llama3.2 both** (local) | t=6 | **98%** | 67% | ~5.4 min |
| llama3.2 P1 + granite3.3:8b P2 (local) | t=3 | 88% | 33% | ~15 min |

All configs use `llama3.2:latest` for Pass 1 (100% recall on this dataset, ~0.3s/paper). No other local model improved on it — larger models matched recall but were 6× slower.

**Cloud is the recommended default** — best balance of speed and precision. **llama3.2 both at t=6** is the best fully-offline option with the highest recall of any config tested. See `tests/EVAL_README.md` for full methodology and per-threshold breakdown.

---

## What PaperMatcher Stores

Everything in `~/.papermatcher/` — nothing leaves your machine except the Pass 2 API calls you configure.

| File                 | Contents                                    |
| -------------------- | ------------------------------------------- |
| `config.json`        | Settings and research profile               |
| `papermatcher.db`    | Saved articles, rejection history, run logs |
| `mesh_cache.json`    | Cached NCBI MeSH term lookups               |

No telemetry. Pass 1 screening is always local.

---

## Acknowledgements

Summarization prompt structure adapted from [Fabric](https://github.com/danielmiessler/fabric) by Daniel Miessler (MIT License) — specifically `analyze_paper_simple` and `create_tags`.

---

## Built With

| Tool                                                          | Role                                                              |
| ------------------------------------------------------------- | ----------------------------------------------------------------- |
| [Kimi K2.6](https://platform.kimi.ai)                         | Initial codebase — single agent built the full app in one session |
| [Claude Sonnet 4.6](https://claude.ai)                        | Architecture, iteration, debugging, prompt design                 |
| [Hermes](https://github.com/InclusionAI/hermes) (InclusionAI) | Implementation agent for Python edits                             |
| [DeepSeek V4 Flash](https://openrouter.ai)                    | Default Pass 2 scoring model                                      |
| [Perplexity AI](https://perplexity.ai)                        | Research and feature validation                                   |

---

## Roadmap

- [ ] Citation export (RIS, BibTeX, NBIB, ENW)
- [ ] Journal RSS feed monitoring (ahead-of-print / early access)
- [ ] Iterative profile enhancement from saved articles
- [ ] Sorting of curated literature by keyword tags
- [ ] Embedding-based Pass 1 screening for large result sets (100K+)
- [ ] Fine-tuning on accumulated user feedback (LoRA/QLoRA)
- [ ] Windows / Linux support
