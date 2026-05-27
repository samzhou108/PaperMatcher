# PaperMatcher Distribution & Installation

**For End Users**

## Installation (macOS)

### Quick Start — 3 Steps

1. **Download** `PaperMatcher_v1.0.0.dmg` from [releases](https://github.com/samzhou108/PaperMatcher/releases)
2. **Mount** — Double-click the `.dmg` file
3. **Install** — Drag `PaperMatcher.app` → Applications folder
4. **Launch** — Open Applications → PaperMatcher (or search Spotlight)

That's it. No terminal, no installation wizard, no Python required.

### First Launch

On first run, macOS may show a security warning (Gatekeeper):
- Click "Open" to allow the app to run
- The warning only appears once

Then PaperMatcher will open with a 3-step onboarding wizard:
1. **Profile** — Your research keywords and interests
2. **PubMed Settings** — NCBI API key (optional but recommended)
3. **LLM Setup** — Choose how to score articles (local or cloud)

## System Requirements

- **macOS 12+** (Intel or Apple Silicon)
- **Ollama** (optional, recommended) — [Download here](https://ollama.ai)
  - Install and run: `ollama pull llama3.2:latest`
  - PaperMatcher will automatically detect it
- **OpenRouter API key** (optional, for cloud scoring) — [Free signup](https://openrouter.ai)

## What's Included

✅ **Full PaperMatcher application**
- 2-pass LLM pipeline
- PubMed search with MeSH expansion
- Local SQLite database
- Tinder-style review interface
- Results export (CSV)
- Settings & customization

No additional downloads or setup needed beyond optional Ollama/OpenRouter.

## Usage Tips

**First pipeline run:**
- Start with a small search (5–10 articles) to test settings
- Allow 1–2 minutes for first run (downloads models on demand)
- Subsequent runs are much faster

**For best results:**
- Install Ollama locally — free, ~0.3s per article, fully private
- OR use OpenRouter free tier (deepseek-v4-flash) for cloud scoring
- Either works; combination is also supported

**Data privacy:**
- All your research questions and saved articles stay in `~/.papermatcher/` on your machine
- Only Pass 2 API calls (if using cloud) leave your device
- No telemetry or tracking

## Troubleshooting

### "PaperMatcher cannot be opened"
macOS Gatekeeper protection on first launch.
- Solution: Right-click PaperMatcher → Open → "Open anyway"

### App launches but crashes immediately
- **Check 1:** Do you have Ollama running? If yes, check: `curl http://localhost:11434/api/tags`
- **Check 2:** Is your NCBI API key valid? (optional, but errors should show in app)
- **Check 3:** Look for error messages in the app's log viewer (Run tab)

### "Cannot connect to Ollama"
- Install Ollama: https://ollama.ai
- Start Ollama (it runs in background): `ollama serve`
- Pull the model: `ollama pull llama3.2:latest`
- Restart PaperMatcher

### Slow performance
- Ollama on first run downloads the model (~2GB) — this takes a few minutes
- Subsequent runs use the cached model
- If very slow, check System Activity Monitor (RAM/CPU usage)

### "No results found"
- Your PubMed query may be too narrow
- Try simpler keywords or adjust filters
- Read the query preview before running

## Uninstall

Simply drag `PaperMatcher.app` from Applications to Trash.

**To keep your database:**
- Your data is in `~/.papermatcher/` — it persists after uninstall
- Delete it manually if you want a clean slate

## Updates

**How to get the latest version:**
1. Download the latest `.dmg` from [releases](https://github.com/samzhou108/PaperMatcher/releases)
2. Repeat the installation steps above
3. Your data in `~/.papermatcher/` will be preserved (backward compatible)

---

**Built for biomedical researchers. Privacy-first. Free.**

Need help? Open an issue: https://github.com/samzhou108/PaperMatcher/issues
