"""LLM and application settings tab."""

import json
import queue
import re
import shutil
import threading
import urllib.request
from datetime import datetime

import customtkinter as ctk

from app.gui.widgets.scrollable_frame import ScrollableFrame
from app.models.config import AppConfig
from app.utils.llm_client import LLMClient, DISTRIBUTION_TIER


OLLAMA_API = "http://localhost:11434"

# Pre-set model names for screener / scorer combos
SCREENER_LOCAL_MODEL = "llama3.2:latest"
SCREENER_CLOUD_DEFAULT = "llama3.2:latest"

SCORER_LOCAL_MODEL = "llama3.2:latest"
SCORER_CLOUD_DEFAULT = "deepseek/deepseek-v4-flash:free"

# Suggested cloud models for dropdown
CLOUD_MODELS = [
    "deepseek/deepseek-v4-flash:free",
    "gpt-4o-mini",
    "gpt-4o",
    "gpt-3.5-turbo",
    "claude-3.5-sonnet",
    "claude-3-opus",
    "llama3.2",
    "mistral-7b",
]

# Curated models for article relevance scoring + summarisation.
# Max ~5 GB — heavier models slow scoring significantly on consumer hardware.
# Excluded: gemma3:4b (calibration failure), qwen3:4b (empty content field bug in Ollama).
CURATED_MODELS = [
    {
        "id": "llama3.2:latest",
        "label": "Llama 3.2 - 3B  (Meta)",
        "size": "~2 GB",
        "note": "Benchmarked: 98% end-to-end recall at t=6. Best fully-offline option.",
    },
    {
        "id": "granite3.3:8b",
        "label": "Granite 3.3 - 8B  (IBM)",
        "size": "~5 GB",
        "note": "Benchmarked: 88% end-to-end recall at t=3, 33% irrelevant pass-through. Best local precision.",
    },
    {
        "id": "gemma3:4b",
        "label": "Gemma 3 - 4B  (Google)",
        "size": "~3.3 GB",
        "note": "Benchmarked: 91% end-to-end recall at t=6. Fast but similar noise level to llama3.2.",
    },
    {
        "id": "mistral:7b",
        "label": "Mistral - 7B",
        "size": "~4.4 GB",
        "note": "Benchmarked: 72% end-to-end recall at t=4, 33% irrelevant pass-through.",
    },
]


class SettingsTab:
    """Settings configuration tab for LLM and thresholds."""

    def __init__(self, master, config: AppConfig):
        self.master = master
        self.config = config
        self._build_ui()

    # ------------------------------------------------------------------
    # UI builder
    # ------------------------------------------------------------------

    def _build_ui(self):
        """Build the settings tab UI."""
        self.scroll = ScrollableFrame(self.master)
        self.scroll.pack(fill="both", expand=True)

        # --- Tier Status Banner (Bug 2 fix: make tier visible) ---
        status_bar = ctk.CTkFrame(self.scroll, fg_color=("gray90", "gray15"),
                                   corner_radius=6, height=28)
        status_bar.pack(fill="x", pady=(0, 10))
        status_bar.pack_propagate(False)
        self._tier_status_label = ctk.CTkLabel(
            status_bar,
            text="",
            font=ctk.CTkFont(size=11),
            text_color="gray30",
        )
        self._tier_status_label.pack(side="left", padx=10)

        # Pre-create StringVars so _update_tier_status() can access them
        self.scoring_model_var = ctk.StringVar(
            value=self.config.llm.scoring_model or "local"
        )
        self.screening_model_var = ctk.StringVar(
            value=self.config.llm.screening_model or "local"
        )

        # LLM Section
        llm_header_row = ctk.CTkFrame(self.scroll, fg_color="transparent")
        llm_header_row.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(
            llm_header_row,
            text="LLM Configuration",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(side="left")
        ctk.CTkButton(
            llm_header_row,
            text="💡 Suggested Setup",
            width=155, height=28,
            fg_color="transparent",
            border_width=1,
            font=ctk.CTkFont(size=12),
            command=self._show_suggested_setup,
        ).pack(side="right", pady=(4, 0))

        # Helper to build a Local/Cloud model section (Pass 1 or Pass 2)
        def _build_model_section(label: str, mode_var: ctk.StringVar,
                                 on_change, local_models: list,
                                 default_local: str, default_cloud_url: str,
                                 default_cloud_model: str, default_key: str,
                                 ) -> dict:
            """Build a self-contained Pass N section. Returns widget refs dict."""
            sep = ctk.CTkFrame(self.scroll, height=2, fg_color="gray75")
            sep.pack(fill="x", pady=(8, 6))
            ctk.CTkLabel(self.scroll, text=label,
                         font=ctk.CTkFont(size=14, weight="bold"),
                         ).pack(anchor="w", pady=(2, 4))

            radio_row = ctk.CTkFrame(self.scroll, fg_color="transparent")
            radio_row.pack(fill="x", pady=(0, 8))
            ctk.CTkRadioButton(radio_row, text="Local (Ollama)",
                               variable=mode_var, value="local",
                               command=on_change,
                               ).pack(side="left", padx=(0, 20))
            ctk.CTkRadioButton(radio_row, text="Cloud (API)",
                               variable=mode_var, value="cloud",
                               command=on_change,
                               ).pack(side="left")

            # Wrapper always packed — local/cloud swap inside it
            content_wrapper = ctk.CTkFrame(self.scroll, fg_color="transparent")
            content_wrapper.pack(fill="x", pady=(0, 6))

            # Local model combo
            local_frame = ctk.CTkFrame(content_wrapper, fg_color="transparent")
            local_frame.pack(fill="x")
            ctk.CTkLabel(local_frame, text="Model:",
                         font=ctk.CTkFont(size=12), width=70, anchor="w",
                         ).pack(side="left")
            local_combo = ctk.CTkComboBox(local_frame, values=local_models, width=300)
            local_combo.set(default_local)
            local_combo.pack(side="left", fill="x", expand=True)

            # Cloud fields (not packed initially)
            cloud_frame = ctk.CTkFrame(content_wrapper, fg_color="transparent")

            def _row(parent, lbl, widget_factory):
                r = ctk.CTkFrame(parent, fg_color="transparent")
                r.pack(fill="x", pady=(0, 4))
                ctk.CTkLabel(r, text=lbl, font=ctk.CTkFont(size=12),
                             width=70, anchor="w").pack(side="left")
                w = widget_factory(r)
                w.pack(side="left", fill="x", expand=True)
                return w

            url_combo = _row(cloud_frame, "URL:",
                             lambda p: ctk.CTkComboBox(p, values=[
                                 "https://openrouter.ai/api/v1",
                                 "https://api.openai.com/v1",
                                 "https://api.anthropic.com/v1",
                                 "https://generativelanguage.googleapis.com/v1beta/openai",
                                 "http://localhost:11434/v1",
                                 "http://localhost:1234/v1",
                                 "http://localhost:4000/v1",
                             ]))
            url_combo.set(default_cloud_url)

            model_entry = _row(cloud_frame, "Model:",
                               lambda p: ctk.CTkComboBox(p, values=CLOUD_MODELS))
            model_entry.set(default_cloud_model)

            key_row = ctk.CTkFrame(cloud_frame, fg_color="transparent")
            key_row.pack(fill="x", pady=(0, 4))
            ctk.CTkLabel(key_row, text="API Key:", font=ctk.CTkFont(size=12),
                         width=70, anchor="w").pack(side="left")
            key_entry = ctk.CTkEntry(key_row, show="*")
            key_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
            if default_key:
                key_entry.insert(0, default_key)
            ctk.CTkButton(key_row, text="Show", width=55, height=28,
                          fg_color=("gray75", "gray30"),
                          hover_color=("gray65", "gray40"),
                          text_color=("black", "white"),
                          font=ctk.CTkFont(size=11),
                          command=lambda e=key_entry: self._toggle_key_visibility(e),
                          ).pack(side="left")

            return {
                "local_frame": local_frame,
                "cloud_frame": cloud_frame,
                "local_combo": local_combo,
                "url_combo": url_combo,
                "model_entry": model_entry,
                "key_entry": key_entry,
            }

        _local_models = [
            "llama3.2:latest", "gemma3:4b", "llama3.1:8b",
            "mistral:7b", "qwen3.5:4b", "granite3.3:8b",
        ]

        # ── Pass 1 — Screener ─────────────────────────────────────────────
        self._p1 = _build_model_section(
            label="Pass 1 — Screener Model",
            mode_var=self.screening_model_var,
            on_change=self._on_screening_model_change,
            local_models=_local_models,
            default_local=(self.config.llm.screening_model_name
                           if self.config.llm.screening_model == "local"
                           else "llama3.2:latest"),
            default_cloud_url=self.config.llm.screening_base_url or "https://openrouter.ai/api/v1",
            default_cloud_model=(self.config.llm.screening_model_name
                                 if self.config.llm.screening_model == "cloud"
                                 else SCREENER_CLOUD_DEFAULT),
            default_key=self.config.llm.screening_api_key or "",
        )

        # Test Pass 1 button
        p1_test_row = ctk.CTkFrame(self.scroll, fg_color="transparent")
        p1_test_row.pack(fill="x", pady=(4, 8))
        self.test_screener_btn = ctk.CTkButton(
            p1_test_row, text="Test Pass 1 (Screener)",
            command=self._test_screener,
        )
        self.test_screener_btn.pack(side="left")
        self.p1_test_result = ctk.CTkLabel(
            p1_test_row, text="", font=ctk.CTkFont(size=12))
        self.p1_test_result.pack(side="left", padx=(12, 0))

        # ── Pass 2 — Scoring ─────────────────────────────────────────────
        self._p2 = _build_model_section(
            label="Pass 2 — Scoring Model",
            mode_var=self.scoring_model_var,
            on_change=self._on_scoring_model_change,
            local_models=_local_models,
            default_local=(self.config.llm.model
                           if self.config.llm.scoring_model == "local"
                           else "llama3.2:latest"),
            default_cloud_url=self.config.llm.base_url or "https://openrouter.ai/api/v1",
            default_cloud_model=(self.config.llm.model
                                 if self.config.llm.scoring_model == "cloud"
                                 else SCORER_CLOUD_DEFAULT),
            default_key=self.config.llm.openrouter_key or "",
        )

        # Test Pass 2 button
        p2_test_row = ctk.CTkFrame(self.scroll, fg_color="transparent")
        p2_test_row.pack(fill="x", pady=(4, 8))
        self.test_btn = ctk.CTkButton(
            p2_test_row, text="Test Pass 2 (Scoring)",
            command=self._test_llm,
        )
        self.test_btn.pack(side="left")
        self.test_result = ctk.CTkLabel(
            p2_test_row, text="", font=ctk.CTkFont(size=12))
        self.test_result.pack(side="left", padx=(12, 0))

        # Apply initial visibility
        self._on_screening_model_change()
        self._on_scoring_model_change()

        # Compatibility shims for methods that reference old widget names
        self._test_frame = p2_test_row  # used by nothing now but kept safe


        # --- NCBI API Key (Phase 3: higher throughput) ---
        sep_ncbi = ctk.CTkFrame(self.scroll, height=2, fg_color="gray75")
        sep_ncbi.pack(fill="x", pady=(15, 5))

        self._ncbi_key_frame = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self._ncbi_key_frame.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(
            self._ncbi_key_frame,
            text="NCBI API Key (optional)",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", pady=(5, 2))
        ctk.CTkLabel(
            self._ncbi_key_frame,
            text="An NCBI API key increases rate limit from 3 to 10 req/sec and raises retmax to 500. "
            "Save in ~/.papermatcher/ncbi_api_key.txt or enter here.",
            font=ctk.CTkFont(size=11),
            text_color="gray",
            wraplength=550,
        ).pack(anchor="w", pady=(0, 2))

        ncbi_entry_row = ctk.CTkFrame(self._ncbi_key_frame, fg_color="transparent")
        ncbi_entry_row.pack(fill="x", pady=(0, 8))
        self.ncbi_key_entry = ctk.CTkEntry(ncbi_entry_row, show="*")
        self.ncbi_key_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkButton(
            ncbi_entry_row, text="Show", width=55, height=28,
            fg_color=("gray75", "gray30"), hover_color=("gray65", "gray40"),
            text_color=("black", "white"), font=ctk.CTkFont(size=11),
            command=lambda: self._toggle_key_visibility(self.ncbi_key_entry),
        ).pack(side="left")
        if self.config.llm.ncbi_api_key:
            self.ncbi_key_entry.insert(0, self.config.llm.ncbi_api_key)

        # --- Relevance Threshold ---
        sep2 = ctk.CTkFrame(self.scroll, height=2, fg_color="gray75")
        sep2.pack(fill="x", pady=(15, 5))

        ctk.CTkLabel(
            self.scroll, text="Relevance Threshold",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", pady=(5, 2))
        ctk.CTkLabel(
            self.scroll,
            text="Only save articles scoring above this threshold (1-10)",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        ).pack(anchor="w", pady=(0, 2))

        thresh_frame = ctk.CTkFrame(self.scroll, fg_color="transparent")
        thresh_frame.pack(fill="x", pady=(0, 15))

        self.threshold_var = ctk.IntVar(
            value=self.config.llm.relevance_threshold or 6
        )
        self.threshold_slider = ctk.CTkSlider(
            thresh_frame, from_=1, to=10, number_of_steps=9,
            variable=self.threshold_var,
            command=lambda v: self.threshold_label.configure(
                text=f"{int(float(v))}"
            ),
        )
        self.threshold_slider.pack(side="left", fill="x", expand=True)

        self.threshold_label = ctk.CTkLabel(
            thresh_frame,
            text=str(self.threshold_var.get()),
            font=ctk.CTkFont(size=14, weight="bold"),
            width=30,
        )
        self.threshold_label.pack(side="left", padx=(10, 0))


        # -- Ollama Model Installer --
        sep3 = ctk.CTkFrame(self.scroll, height=2, fg_color="gray75")
        sep3.pack(fill="x", pady=(15, 5))

        ctk.CTkLabel(
            self.scroll,
            text="Local Model Installer (Ollama)",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", pady=(8, 4))

        # Status row
        status_row = ctk.CTkFrame(self.scroll, fg_color="transparent")
        status_row.pack(fill="x", pady=(0, 8))

        self._ollama_status_label = ctk.CTkLabel(
            status_row,
            text="Checking Ollama...",
            font=ctk.CTkFont(size=12),
            text_color="gray",
        )
        self._ollama_status_label.pack(side="left")

        self._ollama_start_btn = ctk.CTkButton(
            status_row,
            text="Start Ollama",
            width=110,
            height=28,
            state="disabled",
            command=self._start_ollama,
        )
        self._ollama_start_btn.pack(side="left", padx=(10, 0))

        ctk.CTkButton(
            status_row,
            text="Refresh",
            width=70,
            height=28,
            fg_color=("gray75", "gray30"),
            hover_color=("gray65", "gray40"),
            text_color=("black", "white"),
            command=self._refresh_ollama_status,
        ).pack(side="left", padx=(6, 0))

        ctk.CTkLabel(
            status_row,
            text="The 'Use' button installs and sets the selected model for Pass 2 scoring.",
            font=ctk.CTkFont(size=9),
            text_color="gray",
        ).pack(side="right", anchor="e")

        # Model cards
        self._model_cards: dict = {}

        for model in CURATED_MODELS:
            card = ctk.CTkFrame(self.scroll, corner_radius=6)
            card.pack(fill="x", pady=(0, 6))

            inner = ctk.CTkFrame(card, fg_color="transparent")
            inner.pack(fill="x", padx=12, pady=8)

            # Left: label + note
            left = ctk.CTkFrame(inner, fg_color="transparent")
            left.pack(side="left", fill="x", expand=True)

            ctk.CTkLabel(
                left,
                text=f"{model['label']}  {model['size']}",
                font=ctk.CTkFont(size=13, weight="bold"),
                anchor="w",
            ).pack(anchor="w")

            ctk.CTkLabel(
                left,
                text=model["note"],
                font=ctk.CTkFont(size=11),
                text_color="gray",
                anchor="w",
            ).pack(anchor="w")

            # Right: buttons
            right = ctk.CTkFrame(inner, fg_color="transparent")
            right.pack(side="right")

            use_btn = ctk.CTkButton(
                right,
                text="Use",
                width=55,
                height=28,
                font=ctk.CTkFont(size=12),
                fg_color=("gray75", "gray30"),
                hover_color=("gray65", "gray40"),
                text_color=("black", "white"),
                state="disabled",
                command=lambda m=model["id"]: self._use_model(m),
            )
            use_btn.pack(side="left", padx=(0, 6))

            install_btn = ctk.CTkButton(
                right,
                text="Install",
                width=70,
                height=28,
                font=ctk.CTkFont(size=12),
                state="disabled",
                command=lambda m=model["id"]: self._install_model(m),
            )
            install_btn.pack(side="left")

            # Progress bar + status (hidden until install starts)
            progress_bar = ctk.CTkProgressBar(card)
            progress_bar.set(0)

            status_lbl = ctk.CTkLabel(
                card,
                text="",
                font=ctk.CTkFont(size=11),
                text_color="gray",
            )

            self._model_cards[model["id"]] = {
                "install_btn": install_btn,
                "use_btn": use_btn,
                "progress_bar": progress_bar,
                "status_lbl": status_lbl,
            }

        self._install_queue: queue.Queue = queue.Queue()
        self._poll_install_queue()

        # Kick off async status check after UI is built
        self.scroll.after(200, self._refresh_ollama_status)

        # -- App Settings --
        sep = ctk.CTkFrame(self.scroll, height=2, fg_color="gray75")
        sep.pack(fill="x", pady=10)

        ctk.CTkLabel(
            self.scroll,
            text="Application Settings",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(anchor="w", pady=(10, 15))

        # Reset button with confirm dialog
        reset_frame = ctk.CTkFrame(self.scroll, fg_color="transparent")
        reset_frame.pack(fill="x", pady=(5, 10))

        self.reset_btn = ctk.CTkButton(
            reset_frame,
            text="Reset All Settings",
            fg_color="#F44336",
            hover_color="#D32F2F",
            command=self._confirm_reset,
        )
        self.reset_btn.pack(side="left", padx=(0, 10))

        # Message below the button (outside any box)
        self.reset_msg_label = ctk.CTkLabel(
            reset_frame,
            text="This will reset config & database. Backups saved in ~/Documents/PaperMatcher Backups/. Restart required.",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        )
        self.reset_msg_label.pack(side="left")

    # ------------------------------------------------------------------
    # UI helpers (Bug 2: tier visibility)
    # ------------------------------------------------------------------

    def _update_tier_status(self):
        """Refresh the tier / mode label in the status banner."""
        scoring = self.scoring_model_var.get()
        screening = self.screening_model_var.get()

        p2_model = (self._p2["local_combo"].get() if hasattr(self, "_p2") else "llama3.2:latest")
        p1_model = (self._p1["local_combo"].get() if hasattr(self, "_p1") else "llama3.2:latest")
        scoring_label = (
            f"{p2_model} (local)" if scoring == "local"
            else f"{(self._p2['model_entry'].get() if hasattr(self, '_p2') else '')} (cloud)"
        )
        screening_label = (
            f"{p1_model} (local)" if screening == "local"
            else f"{(self._p1['model_entry'].get() if hasattr(self, '_p1') else '')} (cloud)"
        )
        tier = "VIP" if DISTRIBUTION_TIER == "vip" else "Prototype"

        self._tier_status_label.configure(
            text=(
                f"Tier: {tier}  |  "
                f"Pass 1: {screening_label}  |  "
                f"Pass 2: {scoring_label}"
            )
        )

    def _apply_scoring_model_ui(self):
        """No-op — model selection is now handled by _on_scoring_model_change."""
        self._update_tier_status()

    def _toggle_key_visibility(self, entry: ctk.CTkEntry):
        """Toggle an API key entry between hidden (***) and visible text."""
        if entry.cget("show") == "*":
            entry.configure(show="")
        else:
            entry.configure(show="*")

    def _toggle_base_url_visibility(self):
        """No-op — URL fields are now embedded per-section."""
        pass

    def _toggle_screener_model_ui(self):
        """Show local or cloud fields for Pass 1."""
        if not hasattr(self, "_p1"):
            return
        if self.screening_model_var.get() == "cloud":
            self._p1["local_frame"].pack_forget()
            self._p1["cloud_frame"].pack(fill="x")
        else:
            self._p1["cloud_frame"].pack_forget()
            self._p1["local_frame"].pack(fill="x")
        self._update_tier_status()

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_mode_change(self):
        """Legacy handler — kept for compatibility."""
        pass

    def _on_scoring_model_change(self):
        """Show local or cloud fields for Pass 2."""
        if not hasattr(self, "_p2"):
            return
        if self.scoring_model_var.get() == "cloud":
            self._p2["local_frame"].pack_forget()
            self._p2["cloud_frame"].pack(fill="x")
        else:
            self._p2["cloud_frame"].pack_forget()
            self._p2["local_frame"].pack(fill="x")
        self._update_tier_status()

    def _on_screening_model_change(self):
        """Update UI when screener model radio changes."""
        self._toggle_screener_model_ui()

    def _on_url_change(self, event=None):
        """No-op — URL fields are now per-section."""
        pass


    # ------------------------------------------------------------------
    # Ollama helpers
    # ------------------------------------------------------------------

    def _ollama_is_running(self) -> bool:
        try:
            with urllib.request.urlopen(f"{OLLAMA_API}/api/tags", timeout=2) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _get_installed_models(self) -> set[str]:
        try:
            with urllib.request.urlopen(f"{OLLAMA_API}/api/tags", timeout=2) as resp:
                data = json.loads(resp.read())
                return {m["name"] for m in data.get("models", [])}
        except Exception:
            return set()

    def _fetch_installed_models(self) -> list[str]:
        """Fetch installed Ollama models, return sorted list of names."""
        try:
            with urllib.request.urlopen(f"{OLLAMA_API}/api/tags", timeout=2) as resp:
                data = json.loads(resp.read())
                models = [m["name"] for m in data.get("models", [])]
                return sorted(models)
        except Exception:
            return []

    def _refresh_ollama_status(self):
        """Check Ollama status in a background thread, update UI when done."""

        def _check():
            installed_cli = shutil.which("ollama") is not None
            running = self._ollama_is_running()
            installed_models = self._get_installed_models() if running else set()
            self._install_queue.put(("status", installed_cli, running, installed_models))

        threading.Thread(target=_check, daemon=True).start()

    def _apply_ollama_status(self, installed_cli: bool, running: bool, installed_models: set):
        if not installed_cli:
            self._ollama_status_label.configure(
                text="Ollama not found. Install from ollama.com",
                text_color="#F44336",
            )
            self._ollama_start_btn.configure(state="disabled")
            for cards in self._model_cards.values():
                cards["install_btn"].configure(state="disabled")
                cards["use_btn"].configure(state="disabled")
            return

        if running:
            self._ollama_status_label.configure(text="Ollama running OK", text_color="#4CAF50")
            self._ollama_start_btn.configure(state="disabled", text="Running")
        else:
            self._ollama_status_label.configure(text="Ollama installed, not running", text_color="#FF9800")
            self._ollama_start_btn.configure(state="normal", text="Start Ollama")

        for model_id, cards in self._model_cards.items():
            is_installed = any(
                model_id.split(":")[0] in m or model_id in m
                for m in installed_models
            )
            if is_installed:
                cards["install_btn"].configure(state="disabled", text="Installed OK",
                                               fg_color=("gray75", "gray30"),
                                               text_color=("black", "white"))
                cards["use_btn"].configure(state="normal" if running else "disabled")
            else:
                cards["install_btn"].configure(
                    state="normal" if running else "disabled",
                    text="Install",
                    fg_color=["#1F6AA5", "#144870"],
                    text_color="white",
                )
                cards["use_btn"].configure(state="disabled")

    def _start_ollama(self):
        """Start Ollama server in background."""
        import subprocess
        try:
            subprocess.Popen(["ollama", "serve"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._ollama_status_label.configure(text="Starting Ollama...", text_color="gray")
            self._ollama_start_btn.configure(state="disabled")
            self.scroll.after(2500, self._refresh_ollama_status)
        except Exception as e:
            self._ollama_status_label.configure(text=f"Failed to start: {e}", text_color="#F44336")

    def _use_model(self, model_id: str):
        """Set Pass 2 to local mode with the selected model."""
        self.scoring_model_var.set("local")
        self._on_scoring_model_change()
        self._p2["local_combo"].set(model_id)

    def _install_model(self, model_id: str):
        """Pull a model from Ollama in a background thread with progress."""
        import subprocess
        cards = self._model_cards[model_id]
        cards["install_btn"].configure(state="disabled", text="Installing...")
        cards["status_lbl"].configure(text="Starting download...")
        cards["status_lbl"].pack(fill="x", padx=12, pady=(0, 4))
        cards["progress_bar"].pack(fill="x", padx=12, pady=(0, 8))
        cards["progress_bar"].set(0)

        def _pull():
            try:
                payload = json.dumps({"model": model_id, "stream": True}).encode()
                req = urllib.request.Request(
                    f"{OLLAMA_API}/api/pull",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=600) as resp:
                    for raw_line in resp:
                        line = raw_line.decode().strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        status = obj.get("status", "")
                        completed = obj.get("completed", 0)
                        total = obj.get("total", 0)
                        pct = (completed / total) if total > 0 else 0
                        self._install_queue.put(("progress", model_id, status, pct))
                self._install_queue.put(("done", model_id))
            except Exception as e:
                self._install_queue.put(("error", model_id, str(e)))

        threading.Thread(target=_pull, daemon=True).start()

    def _poll_install_queue(self):
        """Drain the install queue on the main thread every 200 ms."""
        try:
            while True:
                item = self._install_queue.get_nowait()
                kind = item[0]

                if kind == "status":
                    _, installed_cli, running, installed_models = item
                    self._apply_ollama_status(installed_cli, running, installed_models)

                elif kind == "progress":
                    _, model_id, status, pct = item
                    cards = self._model_cards.get(model_id)
                    if cards:
                        cards["status_lbl"].configure(text=status[:60])
                        if pct > 0:
                            cards["progress_bar"].set(pct)

                elif kind == "done":
                    _, model_id = item
                    cards = self._model_cards.get(model_id)
                    if cards:
                        cards["status_lbl"].configure(text="Installed OK", text_color="#4CAF50")
                        cards["progress_bar"].set(1.0)
                        cards["install_btn"].configure(
                            state="disabled", text="Installed OK",
                            fg_color=("gray75", "gray30"), text_color=("black", "white"),
                        )
                        cards["use_btn"].configure(state="normal")
                    self._refresh_ollama_status()

                elif kind == "error":
                    _, model_id, msg = item
                    cards = self._model_cards.get(model_id)
                    if cards:
                        cards["status_lbl"].configure(text=f"Error: {msg[:60]}", text_color="#F44336")
                        cards["install_btn"].configure(state="normal", text="Retry")

        except queue.Empty:
            pass

        self.scroll.after(200, self._poll_install_queue)

    # ------------------------------------------------------------------
    # Reset / save
    # ------------------------------------------------------------------

    def _test_llm(self):
        """Test Pass 2 (Scoring) LLM connection."""
        self.test_result.configure(text="Testing...", text_color="gray")
        self.master.update()
        try:
            scoring = self.scoring_model_var.get()
            if scoring == "local":
                model = self._p2["local_combo"].get()
                client = LLMClient(
                    base_url="http://localhost:11434/v1",
                    api_key="not-needed",
                    model=model,
                    scoring_model="local",
                )
            else:
                url = self._p2["url_combo"].get()
                model = self._p2["model_entry"].get()
                key = self._p2["key_entry"].get()
                client = LLMClient(
                    base_url=url, api_key=key, model=model,
                    scoring_model="cloud", openrouter_key=key,
                )
            success, msg = client.test_connection(pass1=False)
            if success:
                self.test_result.configure(text=f"OK: {msg}", text_color="#4CAF50")
            else:
                self.test_result.configure(
                    text=f"Failed: {msg}", text_color="#F44336")
        except Exception as e:
            self.test_result.configure(text=f"Error: {e}", text_color="#F44336")

    def _test_screener(self):
        """Test Pass 1 (Screener) connection."""
        self.p1_test_result.configure(text="Testing...", text_color="gray")
        self.master.update()
        try:
            screening = self.screening_model_var.get()
            if screening == "local":
                model = self._p1["local_combo"].get()
                client = LLMClient(
                    base_url="http://localhost:11434/v1",
                    api_key="not-needed",
                    model=model,
                    scoring_model="local",
                    screening_model="local",
                    screening_model_name=model,
                )
            else:
                url = self._p1["url_combo"].get()
                model = self._p1["model_entry"].get()
                key = self._p1["key_entry"].get()
                client = LLMClient(
                    base_url=url, api_key=key, model=model,
                    scoring_model="cloud",
                    screening_model="cloud",
                    screening_model_name=model,
                    screening_base_url=url,
                    screening_api_key=key,
                )
            success, msg = client.test_connection(pass1=True)
            if success:
                self.p1_test_result.configure(text=f"OK: {msg}", text_color="#4CAF50")
            else:
                self.p1_test_result.configure(
                    text=f"Failed: {msg}", text_color="#F44336")
        except Exception as e:
            self.p1_test_result.configure(text=f"Error: {e}", text_color="#F44336")

    def _confirm_reset(self):
        """Show confirmation popup before resetting."""
        dialog = ctk.CTkToplevel(self.master)
        dialog.title("Confirm Reset")
        dialog.geometry("380x180")
        dialog.transient(self.master)
        dialog.grab_set()

        ctk.CTkLabel(
            dialog,
            text="Are you sure?",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(pady=(20, 5))
        ctk.CTkLabel(
            dialog,
            text="Config and database will be backed up and then deleted.\n"
                 "You will need to restart PaperMatcher to re-run onboarding.",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        ).pack(pady=(0, 15))

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=(0, 15))

        def _do_reset():
            self._reset_settings()
            dialog.destroy()

        ctk.CTkButton(btn_frame, text="Cancel", width=80, command=dialog.destroy).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="Reset", width=80, fg_color="#F44336",
                       hover_color="#D32F2F", command=_do_reset).pack(side="left", padx=10)

    def _reset_settings(self):
        """Reset all settings by deleting config and database (with backups)."""
        from pathlib import Path

        config_path = Path.home() / ".papermatcher" / "config.json"
        db_path = Path.home() / ".papermatcher" / "papermatcher.db"
        backups_dir = Path.home() / "Documents" / "PaperMatcher Backups"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        msgs = []
        try:
            if config_path.exists():
                bak = backups_dir / f"config_reset_{ts}.json"
                bak.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(config_path, bak)
                config_path.unlink()
                msgs.append(f"config.json -> {bak.name}")
        except Exception as e:
            msgs.append(f"Config error: {e}")

        try:
            if db_path.exists():
                bak = backups_dir / f"db_reset_{ts}.db"
                bak.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(db_path, bak)
                db_path.unlink()
                msgs.append(f"database -> {bak.name}")
        except Exception as e:
            msgs.append(f"DB error: {e}")

        if msgs:
            self.test_result.configure(
                text="Reset done: " + "; ".join(msgs) + ". Restart to re-onboard.",
                text_color="#FF9800",
            )
        else:
            self.test_result.configure(text="No config or DB found to reset.", text_color="gray")

    # ------------------------------------------------------------------
    # Suggested Setup Dialog
    # ------------------------------------------------------------------

    def _show_suggested_setup(self):
        """Show the Suggested OpenRouter Setup dialog."""
        dialog = ctk.CTkToplevel(self.master)
        dialog.title("Suggested OpenRouter Setup")
        dialog.geometry("640x600")
        dialog.resizable(True, True)
        dialog.minsize(560, 480)

        # Title
        ctk.CTkLabel(
            dialog,
            text="Suggested OpenRouter Setup",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(anchor="w", padx=15, pady=(15, 10))

        # Scrollable frame with textbox
        scroll_frame = ctk.CTkScrollableFrame(dialog)
        scroll_frame.pack(fill="both", expand=True, padx=15, pady=(0, 10))

        textbox = ctk.CTkTextbox(
            scroll_frame,
            wrap="word",
            height=380,
        )
        textbox.pack(fill="both", expand=True)
        textbox.insert("1.0", """★ DEFAULT RECOMMENDATION (easiest setup, no AI experience needed)
────────────────────────────────────────
Use llama3.2:latest for BOTH Pass 1 and Pass 2.
No API key. No internet required after setup. Just install
Ollama, pull llama3.2:latest, and run.

   Pass 1 (Screener): llama3.2:latest  (local, already default)
   Pass 2 (Scoring):  llama3.2:latest  (local, set threshold to 6)

Trade-off: very high recall (98%) but more irrelevant papers
reach your review queue. The human review step at the end
lets you catch and reject them.

────────────────────────────────────────

PASS 1 — Screener Model
────────────────────────────────────────
The screener asks one question per article: is this paper
on-topic? It runs locally and is the first filter before
any API call is made.

Benchmark (92 labeled papers, one research area):
   llama3.2:latest   100% recall  ~0.3s/paper  ← recommended
   gemma3:4b          93% recall  ~0.9s/paper
   llama3.1:8b       100% recall  ~1.7s/paper  (slower, no benefit)
   mistral:7b        100% recall  ~1.8s/paper  (slower, runs hot)

Recommendation: keep llama3.2:latest as your screener.
No other tested model improved on it. That said, you can
switch to any Ollama model or use a cloud API for Pass 1
if you prefer — use the selector above.

────────────────────────────────────────

PASS 2 — Scoring Model (benchmarked configs)
────────────────────────────────────────
The scoring model reads each paper that passed Pass 1 and
scores it 1–10 against your research profile. This is where
most of the quality difference comes from.

Test set: 92 papers labeled relevant / borderline / irrelevant
for a single PhD research area (neuropathic pain, microglia).
All configs used llama3.2:latest for Pass 1.

   Config                                                                 Threshold  Recall  Noise   Time
   ────────────────────────────────────────
★  deepseek-v4-flash:free (cloud)                  t=4      86%     33%   ~4.4m
★  llama3.2:latest (local)                                    t=6      98%     67%   ~5.4m
   granite3.3:8b (local)                                    t=3      88%     33%   ~15m
   gemma3:4b (local)                                      t=6      91%     67%   ~6.6m

Recall = papers correctly identified as relevant.
Noise = irrelevant papers that still pass the threshold
        (you will see these in the review queue).

────────────────────────────────────────

LOCAL OLLAMA SETUP (Pass 2)
────────────────────────────────────────
No API key, no rate limits, no data leaves your machine.
Set API Base URL to http://localhost:11434, enter model name.

   llama3.2:latest   3B  ~2 GB   — best local recall
   granite3.3:8b     8B  ~5 GB   — best local precision
   gemma3:4b         4B  ~3.3 GB — fast, good recall
   mistral:7b        7B  ~4.4 GB — good precision, runs hot

Cloud-compatible local APIs (Groq, Together AI, LM Studio)
also work with the same URL format.

────────────────────────────────────────

CLOUD SETUP (OpenRouter)
────────────────────────────────────────
1. Create a free account at openrouter.ai
2. Go to Keys → Create Key
3. Paste the key into the OpenRouter API Key field above
4. Set API Base URL to: https://openrouter.ai/api/v1
5. Enter your chosen model name in the scoring model field

⚠  Free tier rate limits
   Each article scored = 1 API request. The free tier gives
   ~50 req/day — in practice you may hit this after fewer
   than 10 requests. Add $10 USD credit to raise the limit
   to 1,000 req/day. For 20–50 articles per run, $10 lasts
   a long time.

★ Free models (as of May 2026)
   deepseek/deepseek-v4-flash:free  — recommended, no prompt
                                      training required
   openrouter/owl-alpha:free        — requires prompt training
   nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free
                                    — requires prompt training

   Enable prompt training (for non-DeepSeek free models):
   OpenRouter → Settings → Privacy → Allow prompt training

Paid models (no daily limit, no prompt training)
   deepseek/deepseek-v4-flash   $0.126 / $0.252 per 1M tokens
   deepseek/deepseek-v4-pro     $0.435 / $0.87  per 1M tokens

Why DeepSeek? Not brand loyalty — it's simply the best
cost-to-performance fit for this task. Scoring 30–50 short
abstracts requires fast, instruction-following responses,
not deep reasoning or large context. DeepSeek V4 Flash is
very cheap and handles this well. That said, you can use
any provider: OpenAI, Anthropic, Gemini, Mistral, etc. —
any OpenAI-compatible API URL works. Use whatever you're
comfortable with or already have credits for.

ℹ  Model availability changes frequently.
   Check openrouter.ai/models for current options.""")
        # Keep state="normal" so text is selectable; block keyboard edits
        def _guard(event):
            if event.state & 0xF:
                return
            if event.keysym in ("Left", "Right", "Up", "Down", "Home", "End",
                                 "Prior", "Next", "Shift_L", "Shift_R"):
                return
            return "break"
        textbox._textbox.bind("<Key>", _guard, add="+")

        # Close button
        def _close():
            dialog.destroy()

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(fill="x", padx=15, pady=(0, 15))

        close_btn = ctk.CTkButton(
            btn_frame,
            text="Close",
            command=_close,
        )
        close_btn.pack()

        # Bind keyboard shortcuts
        dialog.bind("<Return>", lambda e: _close())
        dialog.bind("<Escape>", lambda e: _close())

        # Show dialog
        self.master.update()
        dialog.after(150, lambda: (dialog.lift(), dialog.focus_force()))

    # ------------------------------------------------------------------
    # Config persistence
    # ------------------------------------------------------------------

    def save_to_config(self):
        """Save current values to config."""
        # Pass 1
        self.config.llm.screening_model = self.screening_model_var.get()
        if self.screening_model_var.get() == "local":
            self.config.llm.screening_model_name = self._p1["local_combo"].get()
            self.config.llm.screening_base_url = ""
            self.config.llm.screening_api_key = ""
        else:
            self.config.llm.screening_model_name = self._p1["model_entry"].get()
            self.config.llm.screening_base_url = self._p1["url_combo"].get()
            self.config.llm.screening_api_key = self._p1["key_entry"].get()

        # Pass 2
        self.config.llm.scoring_model = self.scoring_model_var.get()
        if self.scoring_model_var.get() == "local":
            self.config.llm.model = self._p2["local_combo"].get()
            self.config.llm.base_url = "http://localhost:11434/v1"
            self.config.llm.api_key = ""
            self.config.llm.openrouter_key = ""
        else:
            self.config.llm.model = self._p2["model_entry"].get()
            self.config.llm.base_url = self._p2["url_combo"].get()
            key = self._p2["key_entry"].get()
            self.config.llm.api_key = key
            self.config.llm.openrouter_key = key

        self.config.llm.relevance_threshold = self.threshold_var.get()
        self.config.llm.ncbi_api_key = self.ncbi_key_entry.get()