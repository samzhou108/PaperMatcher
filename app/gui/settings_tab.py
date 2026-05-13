"""LLM and application settings tab."""

import json
import queue
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
SCREENER_CLOUD_DEFAULT = "baidu/Qianfan-OCR-Fast:free"

SCORER_LOCAL_MODEL = "llama3.2:latest"
SCORER_CLOUD_DEFAULT = "gpt-4o-mini"

# Curated models for article relevance scoring + summarisation.
# Chosen for strong instruction following at small size (3-5 GB).
CURATED_MODELS = [
    {
        "id": "llama3.2:latest",
        "label": "Llama 3.2 - 3B  (Meta)",
        "size": "~2 GB",
        "note": "Already installed - lightweight baseline",
    },
    {
        "id": "qwen3:4b",
        "label": "Qwen 3 - 4B  (Alibaba)",
        "size": "~2.6 GB",
        "note": "Strong instruction following and classification at small size",
    },
    {
        "id": "gemma3:4b",
        "label": "Gemma 3 - 4B  (Google)",
        "size": "~3.3 GB",
        "note": "Solid structured-output and scoring tasks",
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
        ctk.CTkLabel(
            self.scroll,
            text="LLM Configuration",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(anchor="w", pady=(0, 15))

        # Scoring Model (Bug 1 fix: removed duplicate Mode toggle;
        # "Scoring Model" radio is the single control)
        ctk.CTkLabel(
            self.scroll, text="Scoring Model",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", pady=(5, 2))
        ctk.CTkLabel(
            self.scroll,
            text="Choose where Pass 2 relevance scoring runs.",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        ).pack(anchor="w", pady=(0, 2))

        scoring_frame = ctk.CTkFrame(self.scroll, fg_color="transparent")
        scoring_frame.pack(fill="x", pady=(0, 12))

        ctk.CTkRadioButton(
            scoring_frame, text="Local (llama3.2, no API cost)",
            variable=self.scoring_model_var, value="local",
            command=self._on_scoring_model_change,
        ).pack(side="left", padx=(0, 15))

        ctk.CTkRadioButton(
            scoring_frame, text="Cloud (my API key)",
            variable=self.scoring_model_var, value="cloud",
            command=self._on_scoring_model_change,
        ).pack(side="left")

        # --- Pass 2 model selection (replaces "Default Model") ---
        self._pass2_frame = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self._pass2_frame.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(
            self._pass2_frame, text="Pass 2 model:",
            font=ctk.CTkFont(size=12),
        ).pack(side="left", padx=(0, 8))

        self.model_combo = ctk.CTkComboBox(
            self._pass2_frame, width=300,
            values=["gpt-4o-mini"] + self.config.llm.model_options(),
        )
        self.model_combo.set(self.config.llm.model or "gpt-4o-mini")
        self.model_combo.pack(side="left", fill="x", expand=True, padx=(0, 12))

        self._apply_scoring_model_ui()

        # --- OpenRouter API Key (shown when cloud scoring selected) ---
        self._openrouter_key_frame = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self._openrouter_key_frame.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(
            self._openrouter_key_frame, text="OpenRouter API Key",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", pady=(5, 2))
        ctk.CTkLabel(
            self._openrouter_key_frame,
            text="Enter your OpenRouter key for cloud scoring (free tier: ~50 req/day).",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        ).pack(anchor="w", pady=(0, 2))
        self.openrouter_key_entry = ctk.CTkEntry(self._openrouter_key_frame, show="*")
        self.openrouter_key_entry.pack(fill="x", pady=(0, 8))
        if self.config.llm.openrouter_key:
            self.openrouter_key_entry.insert(0, self.config.llm.openrouter_key)

        # --- API Base URL (shown when cloud scoring or cloud screener) ---
        self._base_url_frame = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self._base_url_frame.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(
            self._base_url_frame, text="API Base URL",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", pady=(5, 2))
        self.url_combo = ctk.CTkComboBox(
            self._base_url_frame,
            values=["https://api.openai.com/v1"] + self.config.llm.base_url_options(),
        )
        self.url_combo.set(self.config.llm.base_url or "https://api.openai.com/v1")
        self.url_combo.pack(fill="x", pady=(0, 12))

        # --- Screener Model Override (Bug 1 extra: option for cloud Pass 1) ---
        sep1 = ctk.CTkFrame(self.scroll, height=2, fg_color="gray75")
        sep1.pack(fill="x", pady=(15, 5))

        ctk.CTkLabel(
            self.scroll,
            text="Screener Model (Pass 1)",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", pady=(5, 2))
        ctk.CTkLabel(
            self.scroll,
            text="Override the default local screener (llama3.2 via Ollama) with a cloud API.\n"
                 "Only applies when scoring model is also set to Cloud.",
            font=ctk.CTkFont(size=11),
            text_color="gray",
            wraplength=550,
        ).pack(anchor="w", pady=(0, 2))

        screen_frame = ctk.CTkFrame(self.scroll, fg_color="transparent")
        screen_frame.pack(fill="x", pady=(0, 12))

        ctk.CTkRadioButton(
            screen_frame, text="Local (llama3.2 via Ollama — default)",
            variable=self.screening_model_var, value="local",
            command=self._on_screening_model_change,
        ).pack(side="left", padx=(0, 15))

        ctk.CTkRadioButton(
            screen_frame, text="Cloud (API — uses Pass 2 model for screening)",
            variable=self.screening_model_var, value="cloud",
            command=self._on_screening_model_change,
        ).pack(side="left")

        # Screener model name entry (shown when cloud screener selected)
        self._screener_model_frame = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self._screener_model_frame.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(
            self._screener_model_frame, text="Screener model name:",
            font=ctk.CTkFont(size=12),
        ).pack(side="left", padx=(0, 8))
        self.screener_model_entry = ctk.CTkEntry(self._screener_model_frame, width=300)
        self.screener_model_entry.pack(side="left", fill="x", expand=True)
        self.screener_model_entry.insert(
            0, self.config.llm.screening_model_name or SCREENER_CLOUD_DEFAULT
        )

        self._toggle_screener_model_ui()
        self._toggle_base_url_visibility()

        # Relevance Threshold
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

        # Test LLM button
        test_frame = ctk.CTkFrame(self.scroll, fg_color="transparent")
        test_frame.pack(fill="x", pady=(10, 20))

        self.test_btn = ctk.CTkButton(
            test_frame, text="Test LLM Connection",
            command=self._test_llm,
        )
        self.test_btn.pack(side="left")

        self.test_result = ctk.CTkLabel(
            test_frame, text="", font=ctk.CTkFont(size=12)
        )
        self.test_result.pack(side="left", padx=(15, 0))

        # --- Visibility toggles on init ---
        if self.scoring_model_var.get() != "cloud":
            self._openrouter_key_frame.pack_forget()
            self._base_url_frame.pack_forget()
        if self.screening_model_var.get() != "cloud":
            self._screener_model_frame.pack_forget()

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
        status_row.pack(fill="x", pady=(0, 10))

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
            text="This will reset config & database. Backups saved in Journal_Tracker/. Restart required.",
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

        scoring_label = (
            "llama3.2 (local)"
            if scoring == "local"
            else f"{self.model_combo.get()} (cloud)"
        )
        screening_label = (
            "llama3.2 (local)"
            if screening == "local"
            else f"{self.screener_model_entry.get()} (cloud)"
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
        """Show model combo with correct default based on scoring mode."""
        mode = self.scoring_model_var.get()
        if mode == "cloud":
            self.model_combo.configure(
                values=["gpt-4o-mini"] + self.config.llm.model_options(),
            )
            self.model_combo.set(self.config.llm.model or SCORER_CLOUD_DEFAULT)
        else:
            self.model_combo.set(SCORER_LOCAL_MODEL)

        self._update_tier_status()

    def _toggle_base_url_visibility(self):
        """Show base URL when either sco or screener uses cloud."""
        scoring_cloud = self.scoring_model_var.get() == "cloud"
        screening_cloud = self.screening_model_var.get() == "cloud"
        if scoring_cloud or screening_cloud:
            self._base_url_frame.pack(fill="x", pady=(0, 12))
        else:
            self._base_url_frame.pack_forget()

    def _toggle_screener_model_ui(self):
        """Show screener model entry only when cloud screener is selected."""
        if self.screening_model_var.get() == "cloud":
            self._screener_model_frame.pack(fill="x", pady=(0, 12))
        else:
            self._screener_model_frame.pack_forget()
        self._toggle_base_url_visibility()
        self._update_tier_status()

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_mode_change(self):
        """Legacy handler — kept for compatibility with onboarding calls."""
        pass  # Mode toggle removed; scoring_model_var drives everything

    def _on_scoring_model_change(self):
        """Update UI when scoring model radio changes."""
        self._apply_scoring_model_ui()
        self._toggle_base_url_visibility()

        if self.scoring_model_var.get() == "cloud":
            self._openrouter_key_frame.pack(fill="x", pady=(0, 12))
        else:
            self._openrouter_key_frame.pack_forget()

    def _on_screening_model_change(self):
        """Update UI when screener model radio changes."""
        self._toggle_screener_model_ui()

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
        """Fill in model combo and switch to local mode."""
        self.scoring_model_var.set("local")
        self._on_scoring_model_change()
        self.model_combo.set(model_id)

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
        """Test LLM connection."""
        self.test_result.configure(text="Testing...", text_color="gray")
        self.master.update()

        try:
            client = LLMClient(
                base_url=self.url_combo.get(),
                api_key=self.openrouter_key_entry.get(),
                model=self.model_combo.get(),
                scoring_model=self.scoring_model_var.get(),
                openrouter_key=self.openrouter_key_entry.get(),
                screening_model=self.screening_model_var.get(),
                screening_model_name=self.screener_model_entry.get(),
            )
            success, msg = client.test_connection()

            if success:
                # Save this successful pairing for future dropdown use
                self.config.llm.add_pairing(
                    self.model_combo.get(), self.url_combo.get()
                )
                self.test_result.configure(
                    text=f"OK: {msg}", text_color="#4CAF50"
                )
            else:
                self.test_result.configure(
                    text=f"Connection failed: {msg}\n"
                         "Check your API key, base URL, and that the model name is correct.",
                    text_color="#F44336",
                )
        except Exception as e:
            self.test_result.configure(
                text=f"Error: {str(e)}\n"
                     "Make sure the base URL is reachable and your API key is valid.",
                text_color="#F44336",
            )

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
                 "You will need to restart PaperPilot to re-run onboarding.",
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

        config_path = Path.home() / ".paperPilot" / "config.json"
        db_path = Path.home() / ".paperPilot" / "paperpilot.db"
        backups_dir = Path.home() / "Documents" / "Claude" / "Projects" / "Journal_Tracker"
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
    # Config persistence
    # ------------------------------------------------------------------

    def save_to_config(self):
        """Save current values to config."""
        self.config.llm.scoring_model = self.scoring_model_var.get()
        self.config.llm.model = self.model_combo.get()
        self.config.llm.base_url = self.url_combo.get()
        self.config.llm.api_key = self.openrouter_key_entry.get() if self.scoring_model_var.get() == "cloud" else ""
        self.config.llm.relevance_threshold = self.threshold_var.get()
        self.config.llm.openrouter_key = self.openrouter_key_entry.get()
        self.config.llm.screening_model = self.screening_model_var.get()
        self.config.llm.screening_model_name = self.screener_model_entry.get()