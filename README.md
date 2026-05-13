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

Two-pass pipeline with separate models for each pass:

- **Pass 1 (screening):** always runs locally via Ollama (`llama3.2:latest` by default)
- **Pass 2 (scoring + summarization):** configurable in Settings
  - *Local:* any Ollama model
  - *Cloud:* any OpenAI-compatible API endpoint (OpenRouter, LM Studio, etc.)

No LiteLLM proxy required. Prototype builds use a bring-your-own-key model.

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
