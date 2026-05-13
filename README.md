PaperPilot — PubMed research article tracker for macOS.

A customtkinter desktop app that searches PubMed for articles matching your research profile, scores them for relevance via a 2-pass LLM pipeline, and saves results locally.

---

## How it works

1. Set up a research profile with keywords and fields of interest (onboarding)
2. PubMed E-utilities is searched by keyword and/or monitored journals
3. Pass 1: local Ollama model screens articles for topic relevance (fast filter)
4. Pass 2: configurable model scores each article 1–10 against your profile
5. Articles above your threshold are summarized and saved to SQLite
6. Browse, edit, delete, and rate saved articles in the Results tab
7. Thumbs-down ratings persist across runs — rejected articles are never re-surfaced

---

## LLM configuration

Two-pass pipeline. Pass 1 always runs locally; Pass 2 is configurable.

### Pass 1 — Screening (local)

Filters out clearly irrelevant articles before the cloud model is called. Runs via Ollama.

**Recommended:** `llama3.2:latest` (3B) — tested, 100% recall on relevant papers, fast (~0.3s/article)

Any Ollama model in the 3–8B range should work. Pass 1 only needs to answer YES/MAYBE/NO, so a larger model gives diminishing returns. Avoid very small models (<1B) — they tend to hallucinate labels.

### Pass 2 — Scoring + summarization (configurable)

Scores each article 1–10 and generates a summary. This is where model quality matters.

**Tested configs** — evaluated against 92 papers labeled from an EndNote library in a single research area (neuroscience/pharmacology: sEH inhibitors, neuropathic pain). "Tested" means the full pipeline was run end-to-end on this dataset; results may not generalize to other fields or keyword sets.

| Pass 2 model | Relevant recall @t=4 | Irrelevant pass-through | Time/run |
|---|---|---|---|
| `llama3.2:latest` (local) | 97.7% @t=6 | 67% | ~5.4 min |
| Baidu/Qianfan OCR-Fast (free) | 86.0% | 33% | ~4.4 min |
| InclusionAI Ring 2.6-1T (free) | 74.4% | 0% | ~6.6 min |

**Production default:** `Baidu/Qianfan OCR-Fast` via [OpenRouter](https://openrouter.ai) (free tier). Best precision/recall tradeoff with zero irrelevant papers above threshold 4.

**Local fallback:** `llama3.2:latest` both passes at threshold 6 — highest recall, no API needed, but ~67% of irrelevant papers pass through.

**Other options (not tested in this pipeline):**

| Model | Where | Cost | Notes |
|---|---|---|---|
| `gemma2:9b`, `mistral:7b` | Ollama | Free | Likely comparable to llama3.2 locally |
| `qwen3.5:9b` | Ollama/LM Studio | Free | Reasoning variant may over-think simple scoring task |
| `gpt-4o-mini` | OpenAI API | ~$0.15/1M tok | Strong instruction following; paid |
| `claude-haiku-4` | Anthropic API | ~$0.25/1M tok | Fast, reliable JSON output; paid |
| `google/gemini-flash-1.5` | OpenRouter | Free tier available | Not tested |

### Caveats

**OpenRouter free tier:** Free models on OpenRouter have rate limits (typically 20 req/min, 200 req/day per model as of 2026). A pipeline run over 50–100 articles will approach or hit the daily limit. If you hit it, the app logs an API error and skips scoring for that article. Options: spread runs across days, use a paid model, or use local fallback.

**NCBI E-utilities (PubMed):** Without a registered API key, the rate limit is 3 requests/second. The app enforces this with a 0.4s inter-call delay and exponential backoff on 429s. Fetching 100+ articles in a single run may still trigger occasional rate limit errors — these are retried automatically. Registering a free NCBI API key raises the limit to 10 req/sec.

**Local models and RAM:** Pass 1 runs concurrently with article processing. On machines with <16GB RAM, running a 7B+ Ollama model alongside the GUI may cause slowdowns. `llama3.2:latest` (3B, ~2GB) is the safest default.

### Recommended setup (free)

1. Install [Ollama](https://ollama.ai) and pull `llama3.2:latest`
2. Create a free [OpenRouter](https://openrouter.ai) account and get an API key
3. In Settings: Pass 1 = local, Pass 2 = cloud → enter OpenRouter key, select `baidu/qianfan-ocr-fast:free`
4. Set threshold to 4

---

## Key files

```
PubMedPaperPilot/
├── main.py                        # Entry point
├── app/
│   ├── gui/
│   │   ├── app_window.py          # Main window, tab layout
│   │   ├── onboarding.py          # 3-step onboarding (Profile → PubMed → LLM)
│   │   ├── profile_tab.py         # Research profile (name, role, keywords, fields)
│   │   ├── results_tab.py         # Article card browser with edit/delete/feedback
│   │   ├── run_tab.py             # Pipeline runner with live log, stats, stop button
│   │   ├── settings_tab.py        # LLM config, Ollama installer, reset with confirmation
│   │   └── widgets/
│   │       ├── keyword_entry.py   # Autocomplete keyword entry
│   │       └── scrollable_frame.py # Cross-platform scrollable frame
│   ├── pipeline/
│   │   ├── pubmed_scraper.py      # PubMed E-utilities search/scrape with retry
│   │   ├── content_fetcher.py     # Article content fetcher (abstract merge)
│   │   ├── relevance_scorer.py    # LLM scoring (1-10) with feedback injection
│   │   └── summarizer.py          # LLM summarization
│   ├── models/
│   │   ├── config.py              # AppConfig dataclass
│   │   ├── article.py             # Article dataclass
│   │   └── user_profile.py        # User profile dataclass
│   └── utils/
│       ├── db.py                  # SQLite ArticleDatabase (dedup, feedback, rejection memory)
│       ├── pubmed.py              # PubMed E-utilities client (fallback fetcher)
│       └── llm_client.py          # OpenAI-compatible LLM client (2-pass support)
├── build/
│   └── build_macos.sh             # PyInstaller build script
└── requirements.txt
```

---

## How to run

```bash
cd ~/Documents/Claude/Projects/Journal_Tracker/PubMedPaperPilot
source venv/bin/activate
python3 main.py
```

First launch opens the 3-step onboarding screen. Subsequent launches go directly to the main window.

---

## Notable design choices

- **2-pass pipeline** — Pass 1 (local, fast) filters out clearly irrelevant articles before the cloud model is called, reducing API cost
- **Feedback loop** — thumbs-up/down ratings are injected into the Pass 2 scoring prompt on subsequent runs; rejected articles are permanently skipped via a `feedback_history` table that survives database resets
- **Lookback up to 10 years** — slider displays days → months → years
- **Boolean search** — OR-joined keywords wrapped in parentheses before AND-ing the date filter, matching PubMed operator precedence
- **Rate limiting** — PubMed E-utilities calls are gated at 3 req/sec with exponential backoff on 429/5xx

---

## Built with

| Tool | Role |
|---|---|
| [Kimi K2.6](https://platform.kimi.ai) | Initial codebase — single agent built the full app in one run |
| [Claude Sonnet 4.6](https://claude.ai) (Anthropic Cowork) | Architecture, planning, debugging, prompt design |
| [Perplexity AI](https://perplexity.ai) | Literature research, feature validation |
| [InclusionAI — Ring 2.6-1T](https://openrouter.ai) | Coding/debugging via Hermes agent |
| [Baidu Qianfan — OCR-Fast](https://openrouter.ai) | Pass 2 scoring model (production default) |
