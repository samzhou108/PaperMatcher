# PaperMatcher Releases

## v1.1.0 (2026-06-03)

**Release Type:** Feature update + light mode

### What's New

✅ **Light Mode Support:**
- Full light/dark mode — app now adapts to macOS system appearance
- Light/dark toggle button in the header (top right)
- Fixed all hardcoded dark backgrounds across Run Pipeline, Settings, and Edit dialog tabs

✅ **Citation Export:**
- Export citations in RIS, BibTeX, NBIB, and ENW formats
- Per-article "Cite" button in Results tab
- Batch "Export Citations" toolbar button

✅ **Build Improvements:**
- Quarantine attribute auto-stripped on build (no Gatekeeper badge)
- Spec file auto-reads version from `app/version.py` (version can never drift)
- DMG auto-created as part of `pyinstaller PaperMatcher.spec`

### Bug Fixes
- Fixed Run Pipeline tab rendering black panels in light mode
- Fixed "Suggested Setup" button near-invisible in light mode
- Fixed "Use" model buttons invisible when disabled in light mode
- Fixed Tags horizontal scrollbar showing black background in Edit dialog
- Fixed context textbox text invisible after focus in light mode

### Updated Model Recommendations (June 2026)

| Config | E2E @t=4 | Cost/run |
|---|---|---|
| deepseek/deepseek-v4-flash (recommended) | 83.7% | ~$0.007 |
| inclusionai/ling-2.6-1t (backup) | 81.4% | ~$0.008 |
| openrouter/owl-alpha (free) | 79.1% | $0.00 |

---

## v1.0.0 (2026-05-27)

**Release Type:** Initial stable release

### What's Included

✅ **Core Features:**
- Two-pass LLM pipeline (local screening + configurable scoring)
- PubMed search with MeSH expansion via NCBI E-utilities
- Tinder-style review interface
- Local SQLite database (privacy-first)
- Research profile customization

✅ **LLM Support:**
- Pass 1: Local Ollama (llama3.2:latest recommended)
- Pass 2: Cloud (OpenRouter free/paid) or local Ollama
- LLM-assisted query generation

✅ **UI/UX:**
- Onboarding wizard (3 steps)
- Advanced search panel (must-include, expand, exclude terms)
- Results tab with pagination and export (CSV)
- Settings with model installer and setup guide
- Responsive dark-mode interface (CTk)

✅ **Quality:**
- 98% recall on 92-paper benchmark (llama3.2 both passes, t=6)
- 86% precision with cloud Pass 2 (deepseek-v4-flash, t=4)
- All Python files compile clean
- Full test coverage for core pipeline

### Installation

**From Source (Recommended for development):**
```bash
git clone https://github.com/samzhou108/PaperMatcher
cd PaperMatcher
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 main.py
```

**macOS Standalone App:**
1. Download `PaperMatcher.app` from releases
2. Move to Applications folder
3. Launch from Spotlight or Applications

### Requirements

- macOS 12+ (Intel or Apple Silicon)
- Python 3.11+ (for development)
- Ollama with llama3.2 (optional but recommended for Pass 1)
- OpenRouter API key (optional for cloud Pass 2)

### Known Limitations

- **macOS only** — Windows/Linux support deferred to future release
- **Light mode** — Fixed in v1.1.0
- **Segfault post-review** — Rare edge case, traceback logging added for debugging
- **Trackpad scrolling** — CTk 5.2.2 has clunky trackpad behavior (CTk >5.2.2 should fix)

### What's Next (Roadmap)

- [x] Citation export (RIS, BibTeX, NBIB, ENW) — v1.1.0
- [ ] Journal RSS monitoring (ahead-of-print feeds)
- [ ] Iterative profile enhancement from saved articles
- [ ] Keyword-based tagging and sorting
- [ ] Embedding-based Pass 1 for large datasets (100K+)
- [ ] Fine-tuning on user feedback (LoRA)
- [ ] Windows / Linux support

### Acknowledgements

- **Kimi K2.6** — Initial codebase (single-session build)
- **Claude Sonnet 4.6** — Architecture & iteration
- **Hermes** — Implementation agent
- **DeepSeek V4 Flash** — Default Pass 2 model
- **Fabric** — Summarization prompt patterns

### Support

- Report issues: https://github.com/samzhou108/PaperMatcher/issues
- View source: https://github.com/samzhou108/PaperMatcher
- License: MIT (see LICENSE file)

---

**Built for biomedical researchers. Privacy-first. Free or low-cost.**
