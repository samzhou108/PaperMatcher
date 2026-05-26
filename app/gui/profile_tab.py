"""User profile editor tab."""

import customtkinter as ctk
import json
from pathlib import Path

from app.gui.widgets.scrollable_frame import ScrollableFrame
from app.models.config import AppConfig
from app.gui.widgets.keyword_entry import KeywordEntry
from app.gui.widgets.pill_frame import PillFrame

MESH_CACHE_PATH = Path.home() / ".papermatcher" / "mesh_cache.json"


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
            text="Type a keyword and press Enter or click a suggestion",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        ).pack(anchor="w", pady=(0, 2))

        # Pill display — shows current keywords with × to remove
        init_kws = list(self.config.profile.keywords or [])
        self._kw_pills = PillFrame(
            self.scroll,
            items=init_kws,
            read_only=False,
            on_change=self._on_pills_changed,
        )
        self._kw_pills.pack(fill="x", pady=(0, 4))

        self.keywords_entry = KeywordEntry(
            self.scroll,
            placeholder_text="Type a keyword and press Enter…",
            on_add_keyword=self._add_keyword_pill,
        )
        self.keywords_entry.pack(fill="x", pady=(0, 8))

        if self.config.profile.keywords:
            self.keywords_entry.insert(0, "")

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

        # MeSH Cache section
        sep = ctk.CTkFrame(self.scroll, height=1, fg_color="gray30")
        sep.pack(fill="x", pady=(18, 10))

        cache_row = ctk.CTkFrame(self.scroll, fg_color="transparent")
        cache_row.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(
            cache_row,
            text="MeSH Cache",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left")

        ctk.CTkButton(
            cache_row,
            text="Manage",
            width=90, height=28,
            command=self._show_cache_manager,
        ).pack(side="right")

        ctk.CTkLabel(
            self.scroll,
            text="Keyword → MeSH descriptor mappings cached from NCBI lookups. Delete an entry to force a fresh lookup on the next run.",
            font=ctk.CTkFont(size=11),
            text_color="gray",
            wraplength=520,
            justify="left",
        ).pack(anchor="w", pady=(0, 12))

    def _add_keyword_pill(self, keyword: str):
        """Add a keyword as a pill."""
        keyword = keyword.strip()
        if not keyword:
            return
        current = self._kw_pills.get_items()
        if keyword not in current:
            self._kw_pills.set_items(current + [keyword])

    def _on_pills_changed(self, new_items: list):
        """Called when a pill is removed — nothing to do, pills are source of truth."""
        pass

    def save_to_config(self):
        """Save current values to config."""
        self.config.profile.name = self.name_entry.get()
        self.config.profile.role = self.role_var.get()
        self.config.profile.research_description = self.research_text.get("1.0", "end").strip()

        keywords = self._kw_pills.get_items()
        if keywords:
            self.config.profile.keywords = keywords

        topics = [t for t, v in self.topic_vars.items() if v.get()]
        if self._other_var.get():
            other_text = self._other_entry.get().strip()
            topics.append(other_text if other_text else "Other")
        self.config.profile.topics = topics

    def _show_cache_manager(self):
        """Open MeSH Cache manager dialog."""
        dialog = ctk.CTkToplevel(self.master)
        dialog.title("MeSH Cache")
        dialog.geometry("480x440")
        dialog.resizable(True, True)
        dialog.minsize(400, 300)

        # Title
        ctk.CTkLabel(
            dialog,
            text="MeSH Cache",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=15, pady=(15, 8))

        # Scrollable frame
        scroll_frame = ctk.CTkScrollableFrame(dialog)
        scroll_frame.pack(fill="both", expand=True, padx=15, pady=(0, 8))
        self._cache_scroll_frame = scroll_frame

        # Bottom bar
        bottom_bar = ctk.CTkFrame(dialog, fg_color="transparent")
        bottom_bar.pack(fill="x", padx=15, pady=(0, 10))

        ctk.CTkButton(
            bottom_bar,
            text="Clear All",
            width=100,
            fg_color="#C62828",
            hover_color="#B71C1C",
            command=lambda: self._clear_all_cache(dialog),
        ).pack(side="left")

        ctk.CTkButton(
            bottom_bar,
            text="Close",
            width=80,
            command=lambda: dialog.destroy(),
        ).pack(side="right")

        # Bind Escape
        dialog.bind("<Escape>", lambda e: dialog.destroy())

        # Populate
        self._refresh_cache_list(scroll_frame)

        # Raise after short delay
        self.master.after(150, lambda: (dialog.lift(), dialog.focus_force()))

    def _refresh_cache_list(self, scroll_frame):
        """Refresh the cache list in the scrollable frame."""
        # Destroy all children
        for widget in scroll_frame.winfo_children():
            widget.destroy()

        try:
            cache = json.loads(MESH_CACHE_PATH.read_text()) if MESH_CACHE_PATH.exists() and MESH_CACHE_PATH.stat().st_size > 0 else {}
        except Exception:
            cache = {}

        if not cache:
            ctk.CTkLabel(
                scroll_frame,
                text="Cache is empty.",
                font=ctk.CTkFont(size=12),
                text_color="gray",
            ).pack(anchor="w", pady=10)
            return

        for keyword, descriptor in sorted(cache.items()):
            row = ctk.CTkFrame(scroll_frame, fg_color="transparent")
            row.pack(fill="x", pady=2)

            ctk.CTkLabel(
                row,
                text=keyword,
                font=ctk.CTkFont(size=12),
                anchor="w",
                width=200,
            ).pack(side="left", padx=(0, 8))

            display = descriptor if descriptor else "— no match —"
            ctk.CTkLabel(
                row,
                text=display,
                font=ctk.CTkFont(size=11),
                text_color="gray" if not descriptor else None,
                wraplength=180,
            ).pack(side="left", fill="x", expand=True)

            ctk.CTkButton(
                row,
                text="×",
                width=28,
                height=24,
                fg_color="transparent",
                text_color="gray",
                hover_color="gray20",
                command=lambda k=keyword: self._delete_cache_entry(k),
            ).pack(side="right")

    def _delete_cache_entry(self, keyword):
        """Delete a single cache entry and refresh the list."""
        try:
            cache = json.loads(MESH_CACHE_PATH.read_text()) if MESH_CACHE_PATH.exists() and MESH_CACHE_PATH.stat().st_size > 0 else {}
            cache.pop(keyword, None)
            MESH_CACHE_PATH.write_text(json.dumps(cache, indent=2))
        except Exception:
            pass
        if hasattr(self, "_cache_scroll_frame"):
            self._refresh_cache_list(self._cache_scroll_frame)

    def _clear_all_cache(self, dialog):
        """Clear all cache entries."""
        try:
            if MESH_CACHE_PATH.exists():
                MESH_CACHE_PATH.write_text("{}")
        except Exception:
            pass
        if hasattr(self, "_cache_scroll_frame"):
            self._refresh_cache_list(self._cache_scroll_frame)

