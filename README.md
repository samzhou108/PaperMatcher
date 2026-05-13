PaperPilot - A PubMed-based research article tracker.

A macOS desktop application (customtkinter GUI) that:
1. Searches PubMed database for articles matching your research keywords
2. Optionally monitors specific journals for new publications
3. Scores each article 1-10 for relevance against your research profile via LLM
4. Summarizes articles above threshold and saves them to SQLite + displays in Results tab
5. Edit, delete, and flag saved articles from the Results tab
6. Stop running pipelines at any time

LLM is called via an OpenAI-compatible endpoint (http://localhost:4000/v1, LiteLLM proxy).
Default model is cloud-fast_Qianfan_noZDR (Baidu/Qianfan fast tier).

---

## Current state

The PubMed scraper version is functional end-to-end. The pipeline:
1. User sets up research profile with keywords (onboarding)
2. PubMed is searched by keyword + monitored journals
3. Results are scored by LLM for relevance (strict 1-10 scale)
4. Relevant articles get summarized and saved to SQLite
5. Saved articles can be edited, deleted, or filtered in the Results tab

---

## Key files

```
PubMedPaperPilot/
├── main.py                        # Entry point
├── app/
│   ├── gui/
│   │   ├── app_window.py          # Main window, tab layout
│   │   ├── onboarding.py          # 3-step onboarding (Profile -> PubMed -> LLM)
│   │   ├── profile_tab.py         # Research profile (name, role, keywords, fields)
│   │   ├── results_tab.py         # Article card browser with edit/delete (SQLite-backed)
│   │   ├── run_tab.py             # Pipeline runner with live log, stats, stop button
│   │   ├── settings_tab.py        # LLM config, Ollama installer, reset with confirmation
│   │   └── widgets/
│   │       ├── keyword_entry.py   # Autocomplete keyword entry
│   │       └── scrollable_frame.py # Cross-platform scrollable frame with trackpad support
│   ├── pipeline/
│   │   ├── pubmed_scraper.py      # PubMed E-utilities search/scrape
│   │   ├── content_fetcher.py     # Fetches articles from PubMed (merging data)
│   │   ├── relevance_scorer.py    # LLM scoring (1-10) with strict distribution
│   │   └── summarizer.py          # LLM summarization
│   ├── models/
│   │   ├── config.py              # AppConfig dataclass (profile, pubmed, llm sections)
│   │   ├── article.py             # Article dataclass
│   │   └── user_profile.py        # User profile dataclass
│   └── utils/
│       ├── db.py                  # SQLite ArticleDatabase (dedup, edit, delete)
│       ├── pubmed.py              # PubMed E-utilities API client (fallback)
│       └── llm_client.py          # OpenAI-compatible LLM client
├── build/
│   └── build_macos.sh             # PyInstaller build script
└── requirements.txt
```

---

## How to run

```bash
cd ~/Documents/Claude/Projects/Journal\ Tracker/PubMedPaperPilot
source venv/bin/activate
python3 main.py
```

If launching for the first time (no config), the 3-step onboarding screen appears. Otherwise opens directly to the main window.

---

## Built with

| Tool | Role |
|---|---|
| [Kimi K2.6](https://platform.kimi.ai) | Initial codebase — agent swarm built the full 25-file app in one run |
| [Claude Sonnet 4.6](https://claude.ai) (Anthropic Cowork) | Architecture, planning, debugging, prompt design |
| [Perplexity AI](https://perplexity.ai) | Literature research, feature validation |
| [Baidu Qianfan — CoBuddy](https://openrouter.ai) | Coding/debugging via Hermes agent; reasoning tasks |
| [InclusionAI — Ring 2.6-1T](https://openrouter.ai) | Pass 1 screening model (fast, no training on prompts) |
| [Baidu Qianfan — OCR-Fast](https://openrouter.ai) | Pass 2 scoring model candidate; model eval |

---

## Key features

- **PubMed direct search** - No IMAP/email integration needed; queries NCBI E-utilities directly
- **Keyword + journal search** - Discover articles by research keywords and monitor specific journals
- **Strict relevance scoring** - LLM scores on a full 1-10 scale; most papers score 3-6, only strong matches get 7+
- **Stop button** - Interrupt running pipelines between articles
- **Edit saved articles** - Adjust scores, tags, summaries, and inclusion from the Results tab
- **Delete articles** - Remove saved articles with immediate database update
- **Lookback up to 365 days** - Search PubMed for publications up to one year back
- **Cross-platform scrolling** - Trackpad, mouse wheel, and button4/5 all work on scrollable frames
- **LLM flexibility** - Supports cloud APIs (OpenAI-compatible) and local models (Ollama)
- **Results stored locally** - SQLite database with deduplication, displayed in built-in Results tab