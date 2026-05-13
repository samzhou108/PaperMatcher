"""User profile editor tab."""

import customtkinter as ctk

from app.gui.widgets.scrollable_frame import ScrollableFrame
from app.models.config import AppConfig
from app.gui.widgets.keyword_entry import KeywordEntry


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


class ProfileTab:
    """Profile configuration tab."""

    def __init__(self, master, config: AppConfig):
        self.master = master
        self.config = config
        self._build_ui()

    def _build_ui(self):
        """Build the profile tab UI."""
        # Scrollable frame
        self.scroll = ScrollableFrame(self.master)
        self.scroll.pack(fill="both", expand=True)

        ctk.CTkLabel(
            self.scroll,
            text="Research Profile",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(anchor="w", pady=(0, 15))

        ctk.CTkLabel(
            self.scroll,
            text="This information is used to score article relevance for your research.",
            font=ctk.CTkFont(size=12),
            text_color="gray",
        ).pack(anchor="w", pady=(0, 20))

        # Name
        ctk.CTkLabel(self.scroll, text="Name", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", pady=(5, 2))
        self.name_entry = ctk.CTkEntry(self.scroll)
        self.name_entry.pack(fill="x", pady=(0, 12))
        if self.config.profile.name:
            self.name_entry.insert(0, self.config.profile.name)

        # Role
        ctk.CTkLabel(self.scroll, text="Role", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", pady=(5, 2))
        self.role_var = ctk.StringVar(value=self.config.profile.role or "PhD Student")
        self.role_menu = ctk.CTkOptionMenu(
            self.scroll,
            values=["PhD Student", "Postdoc", "Researcher", "Clinician", "Other"],
            variable=self.role_var,
        )
        self.role_menu.pack(fill="x", pady=(0, 12))

        # Research description
        ctk.CTkLabel(
            self.scroll,
            text="Research Focus",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", pady=(5, 2))
        ctk.CTkLabel(
            self.scroll,
            text="Describe your research focus in 2-3 sentences. This is used to score paper relevance.",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        ).pack(anchor="w", pady=(0, 2))
        self.research_text = ctk.CTkTextbox(self.scroll, height=100, wrap="word")
        self.research_text.pack(fill="x", pady=(0, 12))
        if self.config.profile.research_description:
            self.research_text.insert("1.0", self.config.profile.research_description)

        # Keywords
        ctk.CTkLabel(self.scroll, text="Keywords", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", pady=(5, 2))
        ctk.CTkLabel(
            self.scroll,
            text="Comma-separated keywords — type to autocomplete",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        ).pack(anchor="w", pady=(0, 2))
        self.keywords_entry = KeywordEntry(self.scroll)
        self.keywords_entry.pack(fill="x", pady=(0, 12))
        if self.config.profile.keywords:
            self.keywords_entry.insert(0, ", ".join(self.config.profile.keywords))

        # Fields of Interest
        ctk.CTkLabel(self.scroll, text="Fields of Interest", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", pady=(5, 2))
        ctk.CTkLabel(
            self.scroll,
            text="Broad academic field — use keywords above for specific topics",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        ).pack(anchor="w", pady=(0, 4))

        fields_frame = ctk.CTkFrame(self.scroll, fg_color="transparent")
        fields_frame.pack(fill="x", pady=(0, 4))

        self.topic_vars = {}
        current_topics = set(self.config.profile.topics or [])

        # Save "Other" text separately
        other_saved = next((t for t in current_topics if t not in FIELD_OPTIONS and t != "Other"), "")

        non_other = [f for f in FIELD_OPTIONS if f != "Other"]
        for i, field in enumerate(non_other):
            var = ctk.BooleanVar(value=field in current_topics)
            self.topic_vars[field] = var
            cb = ctk.CTkCheckBox(fields_frame, text=field, variable=var)
            cb.grid(row=i // 2, column=i % 2, padx=10, pady=4, sticky="w")

        # Other checkbox + text entry
        other_row = ctk.CTkFrame(self.scroll, fg_color="transparent")
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

    def save_to_config(self):
        """Save current values to config."""
        self.config.profile.name = self.name_entry.get()
        self.config.profile.role = self.role_var.get()
        self.config.profile.research_description = self.research_text.get("1.0", "end").strip()

        keywords_text = self.keywords_entry.get()
        self.config.profile.keywords = [k.strip() for k in keywords_text.split(",") if k.strip()]

        topics = [t for t, v in self.topic_vars.items() if v.get()]
        if self._other_var.get():
            other_text = self._other_entry.get().strip()
            topics.append(other_text if other_text else "Other")
        self.config.profile.topics = topics