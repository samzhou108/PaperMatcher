"""First-run onboarding wizard (3 steps: Profile → PubMed → LLM)."""

import os
from pathlib import Path
from typing import Callable

import customtkinter as ctk

from app.gui.widgets.scrollable_frame import ScrollableFrame
from app.models.config import AppConfig
from app.gui.widgets.keyword_entry import KeywordEntry
from app.utils.llm_client import SCREENER_LOCAL_MODEL


FIELD_OPTIONS = [
    "Neuroscience",
    "Immunology",
    "Medicine / Clinical Research",
    "Biochemistry",
    "Molecular Biology",
    "Cell Biology",
    "Genetics & Genomics",
    "Epidemiology",
    "Bioinformatics",
    "Pharmacology",
    "Physiology",
    "Other",
]

# Journals commonly tracked by researchers
DEFAULT_JOURNALS = [
    "Cell",
    "Nature",
    "Science",
    "Neuron",
    "Nature Neuroscience",
    "Immunity",
    "Cell Stem Cell",
    "Nature Medicine",
    "PNAS",
    "PLOS Biology",
]


class OnboardingWizard:
    """3-step onboarding wizard: Profile -> PubMed -> LLM."""

    def __init__(self, master: ctk.CTk, on_complete: Callable[[AppConfig], None]):
        self.master = master
        self.on_complete = on_complete
        self.config = AppConfig()
        self.current_step = 0

        self.window = ctk.CTkToplevel(master)
        self.window.title("PaperPilot - Welcome")
        self.window.geometry("700x650")
        self.window.transient(master)
        self.window.grab_set()

        # Center
        self.window.update_idletasks()
        x = (self.window.winfo_screenwidth() // 2) - 350
        y = (self.window.winfo_screenheight() // 2) - 325
        self.window.geometry(f"700x650+{x}+{y}")

        self._build_ui()

    def _build_ui(self):
        """Build wizard UI."""
        # Header
        header = ctk.CTkFrame(self.window, fg_color="transparent")
        header.pack(fill="x", padx=30, pady=(20, 5))

        self.step_label = ctk.CTkLabel(
            header,
            text="Step 1 of 3",
            font=ctk.CTkFont(size=12),
            text_color="gray",
        )
        self.step_label.pack(side="left")

        self.title_label = ctk.CTkLabel(
            header,
            text="Welcome to PaperPilot",
            font=ctk.CTkFont(size=24, weight="bold"),
        )
        self.title_label.pack(pady=(0, 5))

        # Progress bar
        self.progress = ctk.CTkProgressBar(self.window)
        self.progress.pack(fill="x", padx=30, pady=(0, 15))
        self.progress.set(0.33)

        # Content container
        self.content = ScrollableFrame(self.window, fg_color="transparent")
        self.content.pack(fill="both", expand=True, padx=30, pady=5)

        # Navigation buttons
        nav = ctk.CTkFrame(self.window, fg_color="transparent")
        nav.pack(fill="x", padx=30, pady=(5, 20))

        self.back_btn = ctk.CTkButton(
            nav,
            text="Back",
            width=100,
            state="disabled",
            command=self._prev_step,
        )
        self.back_btn.pack(side="left")

        self.next_btn = ctk.CTkButton(
            nav,
            text="Next",
            width=100,
            command=self._next_step,
        )
        self.next_btn.pack(side="right")

        self._show_step()

    @staticmethod
    def _lookback_text(days: int) -> str:
        """Format lookback period: days → months → years."""
        if days <= 30:
            return f"{days} day{'s' if days != 1 else ''}"
        if days <= 365:
            months = days // 30
            return f"{months} month{'s' if months != 1 else ''}"
        years = days / 365
        if years == int(years):
            return f"{int(years)} year{'s' if int(years) != 1 else ''}"
        return f"{years:.1f} years"

    def _show_step(self):
        """Show current step content."""
        for widget in self.content.winfo_children():
            widget.destroy()

        self.step_label.configure(text=f"Step {self.current_step + 1} of 3")
        self.progress.set((self.current_step + 1) / 3)

        if self.current_step == 0:
            self.title_label.configure(text="Your Research Profile")
            self._build_profile_step()
            self.next_btn.configure(text="Next")
        elif self.current_step == 1:
            self.title_label.configure(text="PubMed Settings")
            self._build_pubmed_step()
            self.next_btn.configure(text="Next")
        elif self.current_step == 2:
            self.title_label.configure(text="LLM Setup")
            self._build_llm_step()
            self.next_btn.configure(text="Done")

        self.back_btn.configure(state="normal" if self.current_step > 0 else "disabled")

    def _build_profile_step(self):
        """Step 1: User profile."""
        ctk.CTkLabel(
            self.content,
            text="Name",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", pady=(5, 2))

        self.profile_name = ctk.CTkEntry(self.content, placeholder_text="Your name")
        self.profile_name.pack(fill="x", pady=(0, 10))
        if self.config.profile.name:
            self.profile_name.insert(0, self.config.profile.name)

        ctk.CTkLabel(
            self.content,
            text="Current Role",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", pady=(5, 2))

        self.profile_role = ctk.CTkOptionMenu(
            self.content,
            values=["PhD Student", "Postdoc", "Researcher", "Clinician", "Other"],
        )
        self.profile_role.pack(fill="x", pady=(0, 10))
        self.profile_role.set(self.config.profile.role or "PhD Student")

        ctk.CTkLabel(
            self.content,
            text="Research Focus",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", pady=(5, 2))

        ctk.CTkLabel(
            self.content,
            text="Describe your research focus in 2-3 sentences. This is used to score paper relevance.",
            font=ctk.CTkFont(size=11),
            text_color="gray",
            wraplength=580,
            justify="left",
        ).pack(anchor="w", pady=(0, 2))

        self.profile_research = ctk.CTkTextbox(self.content, height=80, wrap="word")
        self.profile_research.pack(fill="x", pady=(0, 10))
        if self.config.profile.research_description:
            self.profile_research.insert("1.0", self.config.profile.research_description)

        ctk.CTkLabel(
            self.content,
            text="Keywords (comma-separated)",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", pady=(5, 2))

        ctk.CTkLabel(
            self.content,
            text="Type to autocomplete — e.g. stem cells, immunology, neurodegeneration",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        ).pack(anchor="w", pady=(0, 2))

        self.profile_keywords = KeywordEntry(self.content)
        self.profile_keywords.pack(fill="x", pady=(0, 10))
        if self.config.profile.keywords:
            self.profile_keywords.insert(0, ", ".join(self.config.profile.keywords))

        ctk.CTkLabel(
            self.content,
            text="Fields of Interest",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", pady=(5, 2))
        ctk.CTkLabel(
            self.content,
            text="Broad academic field — use keywords above for specific topics",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        ).pack(anchor="w", pady=(0, 4))

        fields_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        fields_frame.pack(fill="x", pady=(0, 4))

        self.topic_vars = {}
        current_topics = set(self.config.profile.topics or [])
        other_saved = next((t for t in current_topics if t not in FIELD_OPTIONS and t != "Other"), "")

        non_other = [f for f in FIELD_OPTIONS if f != "Other"]
        for i, field in enumerate(non_other):
            var = ctk.BooleanVar(value=field in current_topics)
            self.topic_vars[field] = var
            cb = ctk.CTkCheckBox(fields_frame, text=field, variable=var)
            cb.grid(row=i // 2, column=i % 2, padx=10, pady=3, sticky="w")

        # Other row
        other_row = ctk.CTkFrame(self.content, fg_color="transparent")
        other_row.pack(fill="x", pady=(2, 10))

        self._other_var = ctk.BooleanVar(value=bool(other_saved) or "Other" in current_topics)
        self._other_entry = ctk.CTkEntry(
            other_row,
            placeholder_text="Specify your field…",
            width=260,
            state="normal" if self._other_var.get() else "disabled",
        )
        if other_saved:
            self._other_entry.insert(0, other_saved)

        def _toggle_other():
            if self._other_var.get():
                self._other_entry.configure(state="normal")
                self._other_entry.focus()
            else:
                self._other_entry.configure(state="disabled")

        ctk.CTkCheckBox(
            other_row,
            text="Other:",
            variable=self._other_var,
            command=_toggle_other,
        ).pack(side="left", padx=(10, 8))
        self._other_entry.pack(side="left")

    def _build_pubmed_step(self):
        """Step 2: PubMed configuration."""
        ctk.CTkLabel(
            self.content,
            text="PaperPilot now searches PubMed directly instead of scraping emails.",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", pady=(5, 2))

        ctk.CTkLabel(
            self.content,
            text="Configure which journals and keywords to monitor:",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        ).pack(anchor="w", pady=(0, 10))

        # Default journals to monitor
        ctk.CTkLabel(
            self.content,
            text="Journals to Monitor",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", pady=(5, 2))

        self.journal_vars = {}
        current_journals = set(self.config.pubmed.journals_to_monitor or [])

        journal_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        journal_frame.pack(fill="x", pady=(0, 10))

        for i, journal in enumerate(DEFAULT_JOURNALS):
            var = ctk.BooleanVar(value=journal in current_journals or not current_journals)
            self.journal_vars[journal] = var
            cb = ctk.CTkCheckBox(journal_frame, text=journal, variable=var)
            cb.grid(row=i // 3, column=i % 3, padx=10, pady=2, sticky="w")

        # Custom journal entry
        ctk.CTkLabel(
            self.content,
            text="Add custom journal (comma-separated):",
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", pady=(10, 2))

        self.custom_journals = ctk.CTkEntry(self.content, placeholder_text="e.g. Nature Genetics, JAMA, Lancet")
        self.custom_journals.pack(fill="x", pady=(0, 10))

        # Lookback setting
        ctk.CTkLabel(
            self.content,
            text="Default Lookback Period (days)",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", pady=(5, 2))

        lookback_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        lookback_frame.pack(fill="x", pady=(0, 10))

        self.lookback_var = ctk.IntVar(value=self.config.pubmed.default_since_days or 7)
        ctk.CTkSlider(
            lookback_frame,
            from_=1,
            to=3650,
            number_of_steps=364,
            variable=self.lookback_var,
            width=200,
        ).pack(side="left")
        self._lookback_label = ctk.CTkLabel(
            lookback_frame,
            text=self._lookback_text(self.lookback_var.get()),
            font=ctk.CTkFont(size=12),
        )
        self.lookback_var.trace_add("write", lambda *_: self._lookback_label.configure(
            text=self._lookback_text(int(self.lookback_var.get()))
        ))
        self._lookback_label.pack(side="left", padx=(10, 0))

        # Max results
        ctk.CTkLabel(
            self.content,
            text="Max Results Per Search",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", pady=(5, 2))

        max_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        max_frame.pack(fill="x", pady=(0, 10))

        self.max_results_var = ctk.IntVar(value=self.config.pubmed.max_results_per_search or 50)
        ctk.CTkSlider(
            max_frame,
            from_=10,
            to=100,
            number_of_steps=18,
            variable=self.max_results_var,
            width=200,
        ).pack(side="left")
        self._max_results_label = ctk.CTkLabel(
            max_frame,
            text=f"{self.max_results_var.get()} papers",
            font=ctk.CTkFont(size=12),
        )
        self.max_results_var.trace_add("write", lambda *_: self._max_results_label.configure(
            text=f"{self.max_results_var.get()} papers"
        ))
        self._max_results_label.pack(side="left", padx=(10, 0))

    def _build_llm_step(self):
        """Step 3: LLM setup."""
        ctk.CTkLabel(
            self.content,
            text="LLM Mode",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", pady=(5, 2))

        self.llm_mode = ctk.StringVar(value=self.config.llm.mode or "cloud")

        mode_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        mode_frame.pack(fill="x", pady=(0, 10))

        ctk.CTkRadioButton(
            mode_frame,
            text="Cloud API",
            variable=self.llm_mode,
            value="cloud",
            command=self._on_llm_mode_change,
        ).pack(side="left", padx=(0, 20))

        ctk.CTkRadioButton(
            mode_frame,
            text="Local model (Ollama)",
            variable=self.llm_mode,
            value="local",
            command=self._on_llm_mode_change,
        ).pack(side="left")

        ctk.CTkLabel(
            self.content,
            text="API Base URL",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", pady=(5, 2))

        self.llm_base_url = ctk.CTkEntry(self.content)
        self.llm_base_url.pack(fill="x", pady=(0, 10))

        # API Key — hidden when local mode is selected
        self._api_key_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        self._api_key_frame.pack(fill="x")
        ctk.CTkLabel(
            self._api_key_frame,
            text="API Key",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", pady=(5, 2))
        self.llm_api_key = ctk.CTkEntry(self._api_key_frame, show="*")
        self.llm_api_key.pack(fill="x", pady=(0, 10))
        if self.config.llm.api_key:
            self.llm_api_key.insert(0, self.config.llm.api_key)

        ctk.CTkLabel(
            self.content,
            text="Model Name",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", pady=(5, 2))

        self.llm_model = ctk.CTkEntry(self.content)
        self.llm_model.pack(fill="x", pady=(0, 10))
        if self.config.llm.model:
            self.llm_model.insert(0, self.config.llm.model)

        ctk.CTkLabel(
            self.content,
            text="Relevance Threshold",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", pady=(5, 2))

        ctk.CTkLabel(
            self.content,
            text="Only save articles scoring above this threshold (1-10)",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        ).pack(anchor="w", pady=(0, 2))

        threshold_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        threshold_frame.pack(fill="x", pady=(0, 10))

        self.threshold_var = ctk.IntVar(value=self.config.llm.relevance_threshold or 6)
        self.threshold_slider = ctk.CTkSlider(
            threshold_frame,
            from_=1,
            to=10,
            number_of_steps=9,
            variable=self.threshold_var,
            command=lambda v: self.threshold_label.configure(text=f"{int(float(v))}"),
        )
        self.threshold_slider.pack(side="left", fill="x", expand=True)

        self.threshold_label = ctk.CTkLabel(
            threshold_frame,
            text=str(self.threshold_var.get()),
            font=ctk.CTkFont(size=14, weight="bold"),
            width=30,
        )
        self.threshold_label.pack(side="left", padx=(10, 0))

        # Test LLM button
        test_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        test_frame.pack(fill="x", pady=(10, 5))

        self.test_llm_btn = ctk.CTkButton(
            test_frame,
            text="Test LLM",
            command=self._test_llm,
        )
        self.test_llm_btn.pack(anchor="w")

        self.llm_test_result = ctk.CTkLabel(
            test_frame,
            text="",
            font=ctk.CTkFont(size=12),
            wraplength=580,
            justify="left",
        )
        self.llm_test_result.pack(anchor="w", pady=(4, 0))

        # Set initial values based on mode
        self._on_llm_mode_change()

    def _on_llm_mode_change(self):
        """Update UI when user changes LLM mode."""
        mode = self.llm_mode.get()
        if mode == "cloud":
            self.llm_base_url.delete(0, "end")
            self.llm_base_url.insert(0, "https://api.openai.com/v1")
            self._api_key_frame.pack(fill="x", after=self.llm_base_url)
        else:
            self.llm_base_url.delete(0, "end")
            self.llm_base_url.insert(0, "http://localhost:11434/v1")
            self.llm_api_key.delete(0, "end")
            self._api_key_frame.pack_forget()
            if self.llm_model.get() in ["gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"]:
                self.llm_model.delete(0, "end")
                self.llm_model.insert(0, "llama3.2")

    def _test_llm(self):
        """Test LLM connection."""
        from app.utils.llm_client import LLMClient

        self.llm_test_result.configure(text="Testing...", text_color="gray")
        self.window.update()

        try:
            client = LLMClient(
                base_url=self.llm_base_url.get(),
                api_key=self.llm_api_key.get(),
                model=self.llm_model.get(),
                scoring_model="cloud" if self.llm_mode.get() == "cloud" else "local",
                screening_model="local",
            )
            success, msg = client.test_connection()

            if success:
                self.llm_test_result.configure(text=f"✓ {msg[:60]}", text_color="green")
            else:
                self.llm_test_result.configure(text=f"✗ {msg[:80]}", text_color="red")
        except Exception as e:
            self.llm_test_result.configure(text=f"✗ Error: {e}", text_color="red")

    def _prev_step(self):
        """Go to previous step."""
        if self.current_step > 0:
            self.current_step -= 1
            self._show_step()

    def _next_step(self):
        """Go to next step or finish."""
        self._save_current_step()

        if self.current_step < 2:
            self.current_step += 1
            self._show_step()
        else:
            self._finish()

    def _save_current_step(self):
        """Save data from current step to config."""
        if self.current_step == 0:
            self.config.profile.name = self.profile_name.get()
            self.config.profile.role = self.profile_role.get()
            self.config.profile.research_description = self.profile_research.get("1.0", "end").strip()
            keywords_text = self.profile_keywords.get()
            self.config.profile.keywords = [k.strip() for k in keywords_text.split(",") if k.strip()]
            topics = [t for t, v in self.topic_vars.items() if v.get()]
            if self._other_var.get():
                other_text = self._other_entry.get().strip()
                topics.append(other_text if other_text else "Other")
            self.config.profile.topics = topics

        elif self.current_step == 1:
            # PubMed journals
            journals = [j for j, v in self.journal_vars.items() if v.get()]
            # Add custom journals
            custom = self.custom_journals.get().strip()
            if custom:
                for j in custom.split(","):
                    j = j.strip()
                    if j and j not in journals:
                        journals.append(j)
            self.config.pubmed.journals_to_monitor = journals
            self.config.pubmed.default_since_days = self.lookback_var.get()
            self.config.pubmed.max_results_per_search = self.max_results_var.get()

        elif self.current_step == 2:
            self.config.llm.mode = self.llm_mode.get()
            self.config.llm.scoring_model = "cloud" if self.llm_mode.get() == "cloud" else "local"
            self.config.llm.base_url = self.llm_base_url.get()
            self.config.llm.api_key = self.llm_api_key.get() if self.llm_api_key.cget("state") != "disabled" else ""
            self.config.llm.model = self.llm_model.get()
            self.config.llm.relevance_threshold = self.threshold_var.get()
            self.config.llm.screening_model = "local"
            self.config.llm.screening_model_name = SCREENER_LOCAL_MODEL

    def _finish(self):
        """Complete onboarding."""
        self.config.save()
        self.window.destroy()
        self.on_complete(self.config)

    def grab_set(self):
        """Keep grab_set interface."""
        self.window.grab_set()