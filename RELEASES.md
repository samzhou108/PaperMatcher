# PaperMatcher Releases

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
- **Light mode** — Not yet implemented (dark mode only)
- **Segfault post-review** — Rare edge case, traceback logging added for debugging
- **Trackpad scrolling** — CTk 5.2.2 has clunky trackpad behavior (CTk >5.2.2 should fix)

### What's Next (Roadmap)

- [ ] Citation export (RIS, BibTeX, NBIB, ENW)
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
