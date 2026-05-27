# PaperMatcher Packaging Guide

## Building the Standalone macOS App

PaperMatcher is bundled as a native macOS app using PyInstaller. The build produces `dist/PaperMatcher.app`, a self-contained application that requires no Python installation.

### Prerequisites

- macOS 12+
- Python 3.11+
- All dependencies installed: `pip install -r requirements.txt`

### Building

```bash
cd PaperMatcher
source venv/bin/activate
pyinstaller PaperMatcher.spec
```

**Output:** `dist/PaperMatcher.app` (~42 MB)

The spec file is pre-configured to:
- Bundle the `app/` package
- Include all required dependencies
- Create a properly signed macOS app bundle
- Support both Intel and Apple Silicon (arm64)

### Distribution

#### Option 1: Direct App Share
1. Drag `dist/PaperMatcher.app` to Applications folder
2. Run from Spotlight search or Applications

#### Option 2: DMG Installer (Professional)
Create a `.dmg` for a polished distribution:

```bash
# Create a DMG with the app
mkdir -p tmp_dmg
cp -r dist/PaperMatcher.app tmp_dmg/
cp README.md tmp_dmg/
cp LICENSE tmp_dmg/

# Create the DMG
hdiutil create -volname "PaperMatcher" \
  -srcfolder tmp_dmg \
  -ov -format UDZO \
  PaperMatcher_v1.0.0.dmg

rm -rf tmp_dmg
```

#### Option 3: Homebrew/Package Manager
Future: Can be packaged for Homebrew or MacPorts.

### Requirements for End Users

**Always required:**
- macOS 12+ (Intel or Apple Silicon)

**Optional (for better experience):**
- Ollama installed for local Pass 1 screening ([Download](https://ollama.ai))
- OpenRouter API key for cloud Pass 2 scoring ([Free signup](https://openrouter.ai))

The app will work without Ollama/OpenRouter but will show setup prompts.

### Code Signing & Notarization

The current build includes basic code signing (`codesign_identity: None` uses ad-hoc signing).

For distribution via Apple's App Store or to avoid Gatekeeper warnings:

```bash
codesign --deep --force --verify --verbose \
  --sign "Developer ID Application: Your Name" \
  dist/PaperMatcher.app
```

Then notarize with Apple:
```bash
xcrun notarytool submit PaperMatcher_v1.0.0.dmg \
  --apple-id your-email@example.com \
  --password "your-app-specific-password" \
  --team-id YOUR_TEAM_ID
```

### Troubleshooting

**"PaperMatcher cannot be opened" on first launch**
- This is Gatekeeper protection on macOS
- Solution: Right-click → Open (or run `xattr -d com.apple.quarantine dist/PaperMatcher.app`)

**App crashes on launch**
- Check Console.app for error logs
- Verify Ollama is running (if using local Pass 1)
- Check that required directories (`~/.papermatcher`) exist

**Performance issues**
- App is expected to use ~200MB RAM for 50 articles
- If very slow, check if Ollama is responsive: `curl http://localhost:11434/api/tags`

### Build Configuration

Edit `PaperMatcher.spec` to customize:
- Bundle identifier: Change `bundle_identifier`
- Version: Update `CFBundleVersion` and `CFBundleShortVersionString`
- Icon: Set `icon` parameter to path of `.icns` file
- Hidden imports: Add to `hiddenimports` if modules are missing at runtime

### Notes

- The bundled app is **not** reduced-size optimized (UPX is enabled but Darwin support is limited)
- For significant size reduction, consider distributing via Python wheels instead
- Each rebuild requires ~1 minute on Apple Silicon due to code signing

---

**Last updated:** 2026-05-27  
**Version:** PaperMatcher v1.0.0
