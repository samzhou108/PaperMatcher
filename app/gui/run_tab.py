"""Pipeline execution tab with live logging and progress."""

import json
import logging
import queue
import re
import threading
import traceback
from datetime import datetime
from typing import List, Optional

import customtkinter as ctk
import httpx
from openai import APIError

from app.models.config import AppConfig
from app.gui.review_popup import ReviewPopup
from app.pipeline.pubmed_scraper import PubMedScraper
from app.pipeline.query_builder import QueryBuilder
from app.pipeline.content_fetcher import ContentFetcher
from app.pipeline.relevance_scorer import RelevanceScorer
from app.pipeline.summarizer import Summarizer
from app.utils.db import ArticleDatabase
from app.utils.llm_client import LLMClient
from app.gui.widgets.scrollable_frame import ScrollableFrame

logger = logging.getLogger(__name__)


def _block_edits(textbox: ctk.CTkTextbox) -> None:
    """Make a CTkTextbox read-only while keeping text selectable and copyable.

    Uses state="normal" permanently (so text can be inserted programmatically
    and selected by the user) and blocks keyboard edits via a key handler.
    """
    def _guard(event):
        # Allow: modifier combos (Cmd+C, Ctrl+C, Ctrl+A …), navigation, selection
        if event.state & 0xF:   # any modifier held
            return
        if event.keysym in ("Left", "Right", "Up", "Down", "Home", "End",
                             "Prior", "Next", "Shift_L", "Shift_R",
                             "Control_L", "Control_R", "Meta_L", "Meta_R"):
            return
        return "break"
    textbox._textbox.bind("<Key>", _guard, add="+")


class _PipelineLogHandler(logging.Handler):
    """Routes pipeline module log records into the live-log queue."""

    def __init__(self, log_queue: queue.Queue):
        super().__init__()
        self._q = log_queue
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord):
        try:
            level_map = {
                logging.DEBUG: "info",
                logging.INFO: "info",
                logging.WARNING: "warning",
                logging.ERROR: "error",
                logging.CRITICAL: "error",
            }
            level = level_map.get(record.levelno, "info")
            self._q.put((self.format(record), level))
        except Exception:
            pass


class PipelineStoppedException(Exception):
    """Raised to gracefully stop the pipeline thread."""
    pass


class RunTab:
    """Pipeline execution tab with live log and controls."""

    def __init__(self, master, config: AppConfig, db: ArticleDatabase,
                 sync_config=None):
        self.master = master
        self.config = config
        self.db = db
        self._sync_config = sync_config
        self.is_running = False
        self._stop_flag = False
        self.pipeline_thread: Optional[threading.Thread] = None
        self._log_queue: queue.Queue = queue.Queue()
        self._generate_result_queue: queue.Queue = queue.Queue()
        self._pipeline_done = False
        self._pending_review: Optional[List[str]] = None  # set by bg thread, consumed by poll loop
        self._project_context = ""
        self._advanced_terms: dict[str, list[str]] = {
            "must_include": [],
            "include_to_expand": [],
            "do_not_include": [],
        }
        # Derived keywords (from context/terms) — never written to config/disk.
        self._derived_keywords: list[str] = []
        # Collected saved articles for post-pipeline review popup
        self._saved_articles: list[dict] = []
        # Reference to review popup (reset each pipeline run)
        self._review_popup = None
        # Single httpx.Client created on the main thread to avoid
        # macOS SIGBUS from native TLS context churn in background threads.
        self._http_client: Optional[httpx.Client] = None
        self._query_http_client: Optional[httpx.Client] = None
        # Snapshots of widget values captured on the main thread before the
        # pipeline thread starts — never read CTk widgets from a bg thread.
        self._pipeline_max_results: int = 50
        self._pipeline_since_days: int = 30
        self._pipeline_use_mesh: bool = False
        self._pipeline_raw_query: Optional[str] = None
        self._pipeline_pub_types: List[str] = []
        self._pipeline_pub_type_exclude: bool = False
        self._build_ui()
        self._poll_log_queue()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Resize handle helper
    # ------------------------------------------------------------------

    def _add_resize_handle(self, parent, textbox, min_h: int = 30):
        """Add a thin draggable grip below a textbox that resizes it vertically."""
        grip = ctk.CTkFrame(parent, height=8, fg_color=("gray75", "gray25"), cursor="sb_v_double_arrow")
        grip.pack(fill="x", pady=(0, 2))
        _start = {"y": 0, "h": 0}

        def _press(e):
            _start["y"] = e.y_root
            _start["h"] = textbox.winfo_height()

        def _drag(e):
            delta = e.y_root - _start["y"]
            new_h = max(min_h, _start["h"] + delta)
            textbox.configure(height=new_h)

        grip.bind("<ButtonPress-1>", _press)
        grip.bind("<B1-Motion>", _drag)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        """Build the run tab UI."""
        # Wrap all content in a scrollable frame so controls are accessible
        # even when the window is small.
        sc = ScrollableFrame(self.master, fg_color="transparent")
        sc.pack(fill="both", expand=True)

        controls = ctk.CTkFrame(sc, fg_color="transparent")
        controls.pack(fill="x", pady=(0, 6))

        self.run_btn = ctk.CTkButton(
            controls,
            text="Run Pipeline",
            font=ctk.CTkFont(size=14, weight="bold"),
            width=140,
            height=40,
            command=self._start_pipeline,
        )
        self.run_btn.pack(side="left", padx=(0, 15))

        self.stop_btn = ctk.CTkButton(
            controls,
            text="Stop",
            font=ctk.CTkFont(size=14, weight="bold"),
            width=80,
            height=40,
            fg_color=("#F44336", "#D32F2F"),
            hover_color=("#D32F2F", "#B71C1C"),
            text_color=("black", "white"),
            state="disabled",
            command=self._stop_pipeline,
        )
        self.stop_btn.pack(side="left", padx=(0, 15))

        self.status_label = ctk.CTkLabel(
            controls,
            text="Ready",
            font=ctk.CTkFont(size=13),
            text_color="gray",
        )
        self.status_label.pack(side="left")

        # ---- Run Focus ----
        self._context_frame = ctk.CTkFrame(sc, fg_color="transparent")
        context_frame = self._context_frame
        context_frame.pack(fill="x", pady=(10, 6))

        ctk.CTkLabel(
            context_frame,
            text="Run Focus / Search Context:",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w")

        self.context_textbox = ctk.CTkTextbox(
            context_frame,
            height=42,
            font=ctk.CTkFont(size=12),
            wrap="word",
        )
        self.context_textbox.pack(fill="x", pady=(0, 2))
        self._placeholder_text = (
            "Describe the focus of this run — used for Pass 2 relevance scoring. "
            "Leave blank to use Research Profile only."
        )
        self.context_textbox.insert("1.0", self._placeholder_text)
        self.context_textbox.configure(text_color="gray")

        def _on_context_focus(_event):
            if self.context_textbox.get("1.0", "end").strip() == self._placeholder_text:
                self.context_textbox.delete("1.0", "end")
                self.context_textbox.configure(text_color="white")

        def _on_context_blur(_event):
            if not self.context_textbox.get("1.0", "end").strip():
                self.context_textbox.insert("1.0", self._placeholder_text)
                self.context_textbox.configure(text_color="gray")

        self.context_textbox.bind("<FocusIn>", _on_context_focus)
        self.context_textbox.bind("<FocusOut>", _on_context_blur)
        self._add_resize_handle(context_frame, self.context_textbox)

        # ---- Advanced Search toggle ----
        self._advanced_search_active = False
        self._advanced_toggle_btn = ctk.CTkButton(
            context_frame,
            text="Advanced Search ▼",
            width=160,
            height=28,
            font=ctk.CTkFont(size=11),
            fg_color=("gray75", "gray30"),
            hover_color=("gray65", "gray40"),
            text_color=("black", "white"),
            command=self._toggle_advanced_search,
        )
        self._advanced_toggle_btn.pack(anchor="w", pady=(4, 0))

        # ---- Advanced Search panel (initially hidden) ----
        self._advanced_frame = ctk.CTkFrame(sc, fg_color="transparent")

        ctk.CTkLabel(
            self._advanced_frame,
            text="Structured Search Terms (comma-separated):",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w", padx=(0, 0), pady=(6, 2))

        # Must Include row
        must_frame = ctk.CTkFrame(self._advanced_frame, fg_color="transparent")
        must_frame.pack(fill="x", padx=(0, 0), pady=(0, 4))
        ctk.CTkLabel(
            must_frame, text="Must Include:", font=ctk.CTkFont(size=11), width=90, anchor="w"
        ).pack(side="left")
        self._must_include_textbox = ctk.CTkTextbox(must_frame, height=26, wrap="word")
        self._must_include_textbox.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self._must_include_textbox.bind("<KeyRelease>", self._update_query_preview)

        # Include to Expand row
        expand_frame = ctk.CTkFrame(self._advanced_frame, fg_color="transparent")
        expand_frame.pack(fill="x", padx=(0, 0), pady=(0, 4))
        ctk.CTkLabel(
            expand_frame, text="Include to Expand:", font=ctk.CTkFont(size=11), width=90, anchor="w"
        ).pack(side="left")
        self._expand_textbox = ctk.CTkTextbox(expand_frame, height=26, wrap="word")
        self._expand_textbox.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self._expand_textbox.bind("<KeyRelease>", self._update_query_preview)

        # Do Not Include row
        exclude_frame = ctk.CTkFrame(self._advanced_frame, fg_color="transparent")
        exclude_frame.pack(fill="x", padx=(0, 0), pady=(0, 4))
        ctk.CTkLabel(
            exclude_frame, text="Do Not Include:", font=ctk.CTkFont(size=11), width=90, anchor="w"
        ).pack(side="left")
        self._exclude_textbox = ctk.CTkTextbox(exclude_frame, height=26, wrap="word")
        self._exclude_textbox.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self._exclude_textbox.bind("<KeyRelease>", self._update_query_preview)

        ctk.CTkLabel(
            self._advanced_frame,
            text="\u2139 Separate multiple terms with commas. "
            "All fields are optional. 'Must Include' are AND'd; "
            "others are OR'd with your profile keywords.",
            font=ctk.CTkFont(size=10),
            text_color="gray",
            wraplength=550,
        ).pack(anchor="w", pady=(2, 0))

        # ---- Lookback slider ----
        lookback_row = ctk.CTkFrame(sc, fg_color="transparent")
        lookback_row.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(
            lookback_row,
            text="Look back:",
            font=ctk.CTkFont(size=12),
            text_color="gray",
        ).pack(side="left", padx=(0, 8))

        # Piecewise slider: 5 week steps + 12 month steps + 20 year steps = 37 total
        _DAY_VALS = [1, 7, 14, 21, 28]
        _D = len(_DAY_VALS)   # 5  — positions 0–4
        _M = 12               # positions 5–16  → months 1–12
        _Y = 20               # positions 17–36 → years 1–20
        _SLIDER_MAX = _D + _M + _Y - 1  # 36

        def _pos_to_days(pos: int) -> int:
            if pos < _D:
                return _DAY_VALS[pos]
            elif pos < _D + _M:
                return (pos - _D + 1) * 30
            else:
                return (pos - _D - _M + 1) * 365

        def _days_to_pos(days: int) -> int:
            if days <= 28:
                closest = min(_DAY_VALS, key=lambda x: abs(x - days))
                return _DAY_VALS.index(closest)
            elif days <= 365:
                return _D + max(0, min(_M - 1, round(days / 30) - 1))
            else:
                return _D + _M + max(0, min(_Y - 1, round(days / 365) - 1))

        def _pos_to_display(pos: int):
            if pos < _D:
                return str(_DAY_VALS[pos]), "Days"
            elif pos < _D + _M:
                return str(pos - _D + 1), "Months"
            else:
                return str(pos - _D - _M + 1), "Years"

        def _display_to_days() -> int:
            try:
                n = max(1, int(float(self._lookback_num_var.get())))
            except ValueError:
                n = 1
            unit = self._lookback_unit_var.get()
            if unit == "Months":
                return min(_M * 30, n * 30)
            elif unit == "Years":
                return min(_Y * 365, n * 365)
            # Days: snap to nearest valid week value
            closest = min(_DAY_VALS, key=lambda x: abs(x - n))
            return closest

        _initial_days = self.config.pubmed.default_since_days or 7
        _init_pos = _days_to_pos(_initial_days)
        _init_num, _init_unit = _pos_to_display(_init_pos)

        self._lookback_var = ctk.IntVar(value=_init_pos)
        self._lookback_days = _pos_to_days(_init_pos)
        self._lookback_num_var = ctk.StringVar(value=_init_num)
        self._lookback_unit_var = ctk.StringVar(value=_init_unit)
        self._lookback_syncing = False

        def _entry_to_slider(*_):
            if self._lookback_syncing:
                return
            self._lookback_syncing = True
            days = _display_to_days()
            self._lookback_days = days
            self._lookback_var.set(_days_to_pos(days))
            self._lookback_syncing = False

        def _slider_to_entry(v):
            if self._lookback_syncing:
                return
            self._lookback_syncing = True
            pos = int(round(float(v)))
            self._lookback_days = _pos_to_days(pos)
            num, unit = _pos_to_display(pos)
            self._lookback_num_var.set(num)
            self._lookback_unit_var.set(unit)
            self._lookback_syncing = False

        self._lookback_num_var.trace_add("write", _entry_to_slider)
        self._lookback_unit_var.trace_add("write", _entry_to_slider)

        ctk.CTkEntry(
            lookback_row, textvariable=self._lookback_num_var,
            width=52, font=ctk.CTkFont(size=12), justify="center",
        ).pack(side="left", padx=(0, 4))

        ctk.CTkOptionMenu(
            lookback_row, variable=self._lookback_unit_var,
            values=["Days", "Months", "Years"],
            width=90, height=28, font=ctk.CTkFont(size=12),
        ).pack(side="left", padx=(0, 12))

        ctk.CTkSlider(
            lookback_row, from_=0, to=_SLIDER_MAX, number_of_steps=_SLIDER_MAX,
            variable=self._lookback_var, width=420,
            command=_slider_to_entry,
        ).pack(side="left")

        # ---- Max results slider ----
        max_row = ctk.CTkFrame(sc, fg_color="transparent")
        max_row.pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(
            max_row, text="Max results:",
            font=ctk.CTkFont(size=12), text_color="gray",
        ).pack(side="left", padx=(0, 8))

        self._max_results_var = ctk.IntVar(value=self.config.pubmed.max_results_per_search or 50)
        max_label = ctk.CTkLabel(
            max_row, text=f"{self._max_results_var.get()}",
            font=ctk.CTkFont(size=12, weight="bold"), width=35,
            anchor="w",
        )

        def _on_max_results(v):
            val = int(float(v))
            self._max_results_var.set(val)
            max_label.configure(text=f"{val}")

        ctk.CTkSlider(
            max_row, from_=10, to=100, number_of_steps=18,
            variable=self._max_results_var, width=200,
            command=_on_max_results,
        ).pack(side="left")
        max_label.pack(side="left", padx=(8, 0))

        # ---- Search options row ----
        options_row = ctk.CTkFrame(sc, fg_color="transparent")
        options_row.pack(fill="x", pady=(0, 4))

        self._mesh_var = ctk.BooleanVar(value=True)
        mesh_check = ctk.CTkCheckBox(
            options_row, text="MeSH expansion",
            variable=self._mesh_var,
            font=ctk.CTkFont(size=11),
            command=self._update_query_preview,
        )
        mesh_check.pack(side="left", padx=(0, 12))

        # ---- Publication type filter ----
        pub_type_row = ctk.CTkFrame(sc, fg_color="transparent")
        pub_type_row.pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(
            pub_type_row, text="Publication type:",
            font=ctk.CTkFont(size=11),
        ).pack(side="left", padx=(0, 8))

        self._pub_type_exclude = ctk.BooleanVar(value=False)
        exclude_toggle = ctk.CTkCheckBox(
            pub_type_row, text="Exclude selected types",
            variable=self._pub_type_exclude,
            font=ctk.CTkFont(size=10),
            command=self._update_query_preview
        )
        exclude_toggle.pack(side="left", padx=(0, 8))

        # Publication type options
        self._pub_types: dict[str, ctk.BooleanVar] = {}
        pub_types = [
            ("Review", "Review[Publication Type]"),
            ("Original Research", "Journal Article[Publication Type]"),
            ("Clinical Trial", "Clinical Trial[Publication Type]"),
            ("Meta-Analysis", "Meta-Analysis[Publication Type]"),
            ("Systematic Review", "Systematic Review[Publication Type]"),
        ]
        for label, query_term in pub_types:
            var = ctk.BooleanVar(value=False)
            self._pub_types[label] = var
            cb = ctk.CTkCheckBox(
                pub_type_row, text=label,
                variable=var,
                font=ctk.CTkFont(size=10),
                command=self._update_query_preview,
            )
            cb.pack(side="left", padx=(0, 6))

        # ---- Query preview (editable) ----
        self._query_user_edited = False
        self._query_generating = False

        query_header = ctk.CTkFrame(sc, fg_color="transparent")
        query_header.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(
            query_header,
            text="Query preview (editable):",
            font=ctk.CTkFont(size=11, weight="bold"),
        ).pack(side="left")
        self._generate_btn = ctk.CTkButton(
            query_header,
            text="✨ Generate",
            width=90,
            height=22,
            font=ctk.CTkFont(size=10),
            fg_color=("#7B1FA2", "#4A148C"),
            hover_color=("#6A1B9A", "#38006b"),
            text_color="white",
            command=self._generate_query,
        )
        self._generate_btn.pack(side="left", padx=(8, 0))
        ctk.CTkButton(
            query_header,
            text="↺ Reset",
            width=60,
            height=22,
            font=ctk.CTkFont(size=10),
            fg_color=("gray75", "gray30"),
            hover_color=("gray65", "gray40"),
            text_color=("black", "white"),
            command=self._reset_query_preview,
        ).pack(side="left", padx=(8, 0))
        self._query_edited_label = ctk.CTkLabel(
            query_header,
            text="",
            font=ctk.CTkFont(size=10),
            text_color="#FF9800",
        )
        self._query_edited_label.pack(side="left", padx=(8, 0))

        self._query_preview_box = ctk.CTkTextbox(
            sc,
            height=80,
            font=ctk.CTkFont(family="Menlo", size=11),
            wrap="word",
        )
        self._query_preview_box.pack(fill="x", pady=(2, 0))

        def _on_query_keypress(_event):
            self._query_user_edited = True
            self._query_edited_label.configure(text="✎ custom query — MeSH disabled")

        self._query_preview_box.bind("<KeyRelease>", _on_query_keypress)
        self._add_resize_handle(sc, self._query_preview_box)

        self._mesh_note_label = ctk.CTkLabel(
            sc,
            text="",
            font=ctk.CTkFont(size=10),
            text_color="gray",
            anchor="w",
        )
        self._mesh_note_label.pack(anchor="w", pady=(0, 2))

        self._query_warn_label = ctk.CTkLabel(
            sc,
            text="",
            font=ctk.CTkFont(size=10),
            text_color="#FF9800",
            anchor="w",
            wraplength=600,
            justify="left",
        )
        self._query_warn_label.pack(anchor="w", pady=(0, 4))

        # Populate preview once config is available (deferred so __init__ finishes first).
        # Also re-run whenever the Run tab becomes visible (user switches to it after
        # editing their profile keywords).
        self.master.after(100, self._update_query_preview)
        sc.bind("<Map>", lambda _e: self._update_query_preview())

        # ---- Progress ----
        self.progress = ctk.CTkProgressBar(sc)
        self.progress.pack(fill="x", pady=(0, 10))
        self.progress.set(0)

        # ---- Stats ----
        stats = ctk.CTkFrame(sc, fg_color="transparent")
        stats.pack(fill="x", pady=(0, 10))

        self.stat_searches = ctk.CTkLabel(stats, text="Searches: 0", font=ctk.CTkFont(size=12))
        self.stat_searches.pack(side="left", padx=(0, 20))

        self.stat_articles = ctk.CTkLabel(stats, text="Articles: 0", font=ctk.CTkFont(size=12))
        self.stat_articles.pack(side="left", padx=(0, 20))

        self.stat_scored = ctk.CTkLabel(stats, text="Scored: 0", font=ctk.CTkFont(size=12))
        self.stat_scored.pack(side="left", padx=(0, 20))

        self.stat_saved = ctk.CTkLabel(stats, text="Saved: 0", font=ctk.CTkFont(size=12))
        self.stat_saved.pack(side="left", padx=(0, 20))

        self.stat_skipped = ctk.CTkLabel(stats, text="Skipped: 0", font=ctk.CTkFont(size=12))
        self.stat_skipped.pack(side="left", padx=(0, 20))

        self.stat_errors = ctk.CTkLabel(stats, text="Errors: 0", font=ctk.CTkFont(size=12), text_color="#F44336")
        self.stat_errors.pack(side="left")

        # ---- Log ----
        ctk.CTkLabel(
            sc,
            text="Live Log",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", pady=(5, 5))

        log_frame = ctk.CTkFrame(sc)
        log_frame.pack(fill="x")

        self.log_text = ctk.CTkTextbox(
            log_frame,
            font=ctk.CTkFont(family="Menlo", size=12),
            wrap="word",
            height=300,
        )
        self.log_text.pack(fill="x", padx=5, pady=5)
        _block_edits(self.log_text)
        self._add_resize_handle(log_frame, self.log_text, min_h=100)

    # ------------------------------------------------------------------
    # Advanced search helpers
    # ------------------------------------------------------------------

    def _build_query_string(self) -> str:
        """Build the PubMed query string from current profile keywords and advanced terms.

        Returns the query body (without date filter).
        Profile keywords / expand terms: no field tag (= [All Fields]).
        Must Include: [Title/Abstract] only.
        Do Not Include: no field tag.
        MeSH expansion not applied here — happens at run time in the scraper.
        Publication type filters appended if any are selected.
        """
        keywords = self.config.profile.keywords or []
        advanced = self._parse_advanced_terms() if self._advanced_search_active else {
            "must_include": [], "include_to_expand": [], "do_not_include": []
        }
        must_include = advanced.get("must_include", [])
        include_to_expand = advanced.get("include_to_expand", [])
        do_not_include = advanced.get("do_not_include", [])

        kw_parts = [f'"{kw}"' for kw in keywords if kw.strip()]
        kw_parts += [f'"{t}"' for t in include_to_expand if t.strip()]

        parts = []
        if kw_parts:
            parts.append("(" + " OR ".join(kw_parts) + ")")
        for t in must_include:
            if t.strip():
                parts.append(f'AND "{t}"[Title/Abstract]')
        if do_not_include:
            parts.append("NOT (" + " OR ".join(f'"{t}"' for t in do_not_include) + ")")

        # Publication type filters
        pub_parts = self._build_pub_type_filter()
        if pub_parts:
            parts.append(pub_parts)

        return "\n".join(parts)

    def _update_query_preview(self, *_):
        """Repopulate the editable query preview from profile keywords.

        Skipped if the user has already manually edited the preview.
        """
        if self._query_user_edited:
            return

        # Always clear the edited label regardless of keyword state.
        self._query_edited_label.configure(text="")

        keywords = self.config.profile.keywords or []
        if not keywords:
            self._query_preview_box.configure(state="normal")
            self._query_preview_box.delete("1.0", "end")
            self._query_preview_box.insert("1.0", "(no keywords configured in Profile)")
            self._query_preview_box.configure(state="disabled")
            self._mesh_note_label.configure(text="")
            self._query_warn_label.configure(text="")
            return

        query_str = self._build_query_string()
        self._query_preview_box.configure(state="normal")
        self._query_preview_box.delete("1.0", "end")
        self._query_preview_box.insert("1.0", query_str)

        use_mesh = self._mesh_var.get()
        if use_mesh:
            self._mesh_note_label.configure(
                text="ⓘ MeSH expansion applied at run time  |  Edit the query above to override (MeSH will be skipped)"
            )
        else:
            self._mesh_note_label.configure(text="")

        self._validate_query(query_str)

    def _validate_query(self, query: str):
        """Warn if the query has patterns likely to cause zero results."""
        import re
        warnings = []

        # Count top-level AND clauses
        from app.pipeline.pubmed_scraper import PubMedScraper
        clauses = PubMedScraper._split_top_level_and(query)
        if len(clauses) > 3:
            warnings.append(f"⚠ {len(clauses)} AND conditions — too many may return 0 results (auto-broadening will retry)")

        # Detect multi-word [Title/Abstract] phrases (>2 words in quotes before [Title/Abstract])
        long_ta = re.findall(r'"([^"]{20,})"\[Title/Abstract\]', query, re.IGNORECASE)
        if long_ta:
            warnings.append(f"⚠ Long phrase in [Title/Abstract]: \"{long_ta[0][:40]}\" — may not match any abstract verbatim")

        self._query_warn_label.configure(text="  |  ".join(warnings))

    def _reset_query_preview(self):
        """Clear the user-edited flag and regenerate the auto query."""
        self._query_user_edited = False
        self._update_query_preview()

    # ------------------------------------------------------------------
    # LLM query generation
    # ------------------------------------------------------------------

    def _generate_query(self):
        """Kick off async LLM query generation (runs in a background thread).

        All CTk widget reads happen here on the main thread; only pure Python
        data is passed to the background thread.
        """
        if self._query_generating or self.is_running:
            return

        # Snapshot all widget state on the main thread before spawning.
        ctx = self.context_textbox.get("1.0", "end").strip()
        run_focus = "" if ctx == self._placeholder_text else ctx
        advanced = self._parse_advanced_terms() if self._advanced_search_active else {
            "must_include": [], "include_to_expand": [], "do_not_include": []
        }
        profile_keywords = list(self.config.profile.keywords or [])
        research_description = getattr(self.config.profile, "research_description", "")
        topics = list(self.config.profile.topics or [])
        ncbi_api_key = self.config.llm.ncbi_api_key
        llm_config = {
            "scoring_model": self.config.llm.scoring_model,
            "model": self.config.llm.model,
            "base_url": self.config.llm.base_url,
            "api_key": self.config.llm.api_key,
            "openrouter_key": self.config.llm.openrouter_key,
        }
        advanced["_llm_config"] = llm_config

        self._query_generating = True
        self._generate_btn.configure(state="disabled", text="Generating…")
        self._query_edited_label.configure(
            text="⏳ querying MeSH + LLM…", text_color="#7B1FA2"
        )

        # Create httpx client on the main thread — macOS SIGBUS if created in background thread.
        self._query_http_client = httpx.Client(timeout=60.0)

        threading.Thread(
            target=self._generate_query_async,
            args=(run_focus, advanced, profile_keywords, research_description, topics, ncbi_api_key, self._query_http_client),
            daemon=True,
        ).start()

    def _generate_query_async(self, run_focus, advanced, profile_keywords,
                              research_description, topics, ncbi_api_key, http_client):
        """Background worker: pure computation, no CTk calls.

        Posts result to _generate_result_queue; drained on main thread by _poll_log_queue.
        http_client was created on the main thread to avoid macOS TLS SIGBUS.
        """
        try:
            # Use the Pass 2 cloud model for query generation when configured —
            # stronger models follow instructions reliably and avoid hallucination.
            # Fall back to local llama3.2 when cloud is not set up.
            cloud_model = None
            cloud_base_url = None
            cloud_api_key = None
            cfg = advanced.get("_llm_config", {})  # passed below
            if cfg.get("scoring_model") == "cloud" and cfg.get("base_url"):
                cloud_model = cfg.get("model") or "deepseek/deepseek-v4-flash:free"
                cloud_base_url = cfg.get("base_url")
                cloud_api_key = cfg.get("openrouter_key") or cfg.get("api_key")

            builder = QueryBuilder(
                model="llama3.2:latest",
                http_client=http_client,
                ncbi_api_key=ncbi_api_key,
                cloud_model=cloud_model,
                cloud_base_url=cloud_base_url,
                cloud_api_key=cloud_api_key,
            )

            if not builder.is_available():
                self._generate_result_queue.put(("error", "Ollama unavailable and no cloud model configured."))
                return

            query = builder.build(
                profile_keywords=profile_keywords,
                research_description=research_description,
                run_focus=run_focus,
                must_include=advanced.get("must_include", []),
                include_to_expand=advanced.get("include_to_expand", []),
                topics=topics,
                mesh_hint_keywords=profile_keywords,
            )

            self._generate_result_queue.put(("ok", query))

        except Exception as exc:
            self._generate_result_queue.put(("error", str(exc)))

    def _on_generate_done(self, query: Optional[str], error: Optional[str]):
        """Called on main thread when generation finishes."""
        self._query_generating = False
        # Close the query http client on the main thread (avoids macOS TLS SIGBUS).
        if self._query_http_client:
            try:
                self._query_http_client.close()
            except Exception:
                pass
            self._query_http_client = None
        # Button reset is unconditional — always fires regardless of widget errors below.
        try:
            self._generate_btn.configure(state="normal", text="✨ Generate")
        except Exception:
            pass

        if error:
            try:
                self._query_edited_label.configure(
                    text=f"⚠ {error}", text_color="#F44336"
                )
            except Exception:
                pass
            return

        # Put the generated query into the preview box as if the user typed it,
        # so it will be used verbatim by the pipeline.
        try:
            self._query_user_edited = True
            self._query_preview_box.configure(state="normal")  # re-enable if disabled
            self._query_preview_box.delete("1.0", "end")
            self._query_preview_box.insert("1.0", query)
            self._query_edited_label.configure(
                text="✨ LLM-generated — edit freely", text_color="#7B1FA2"
            )
            self._validate_query(query)
        except Exception as exc:
            logger.warning("Failed to update query preview: %s", exc)

    def _toggle_advanced_search(self):
        """Toggle visibility of the advanced search panel."""
        if self._advanced_frame.winfo_ismapped():
            self._advanced_frame.pack_forget()
            self._advanced_search_active = False
            self._advanced_toggle_btn.configure(text="Advanced Search ▼")
        else:
            self._advanced_frame.pack(fill="x", pady=(4, 0), after=self._context_frame)
            self._advanced_search_active = True
            self._advanced_toggle_btn.configure(text="Advanced Search ▲")

    def _parse_advanced_terms(self) -> dict[str, list[str]]:
        """Parse comma-separated terms from the advanced search textboxes."""
        def _split(text: str) -> list[str]:
            return [t.strip() for t in re.split(r",", text) if t.strip()]

        return {
            "must_include": _split(self._must_include_textbox.get("1.0", "end").strip()),
            "include_to_expand": _split(self._expand_textbox.get("1.0", "end").strip()),
            "do_not_include": _split(self._exclude_textbox.get("1.0", "end").strip()),
        }

    # ------------------------------------------------------------------
    # Pipeline lifecycle
    # ------------------------------------------------------------------

    def _poll_log_queue(self):
        try:
            while True:
                message, level = self._log_queue.get_nowait()
                self._do_log(message, level)
        except queue.Empty:
            pass
        # Drain any pending generate results (thread-safe queue, consumed on main thread).
        try:
            while True:
                kind, data = self._generate_result_queue.get_nowait()
                if kind == "ok":
                    self._on_generate_done(data, None)
                else:
                    self._on_generate_done(None, data)
        except queue.Empty:
            pass
        if self._pipeline_done:
            self._pipeline_done = False
            self._on_pipeline_done()
        if self._pending_review is not None:
            keywords = self._pending_review
            self._pending_review = None
            self._open_review_popup(keywords)
        self.master.after(150, self._poll_log_queue)

    def _on_pipeline_done(self):
        """Reset UI after pipeline finishes (called via after() for thread safety)."""
        try:
            self.run_btn.configure(state="normal", text="Run Pipeline")
        except Exception as e:
            logger.warning("Failed to reset run_btn: %s", e)
        try:
            self.stop_btn.configure(state="disabled", text="Stop")
        except Exception as e:
            logger.warning("Failed to reset stop_btn: %s", e)
        try:
            self._generate_btn.configure(state="normal")
        except Exception:
            pass
        self.status_label.configure(text="Ready", text_color="gray")

    def _log(self, message: str, level: str = "info"):
        self._log_queue.put((message, level))

    def _do_log(self, message: str, level: str = "info"):
        color = {
            "info": "",
            "success": "#4CAF50",
            "warning": "#FF9800",
            "error": "#F44336",
        }.get(level, "")

        timestamp = datetime.now().strftime("%H:%M:%S")

        if color:
            self.log_text.insert("end", f"[{timestamp}] {message}\n", level)
            self.log_text.tag_config(level, foreground=color)
        else:
            self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")

    def _update_stats(self, **kwargs):
        if "searches" in kwargs:
            self.stat_searches.configure(text=f"Searches: {kwargs['searches']}")
        if "articles" in kwargs:
            self.stat_articles.configure(text=f"Articles: {kwargs['articles']}")
        if "scored" in kwargs:
            self.stat_scored.configure(text=f"Scored: {kwargs['scored']}")
        if "saved" in kwargs:
            self.stat_saved.configure(text=f"Saved: {kwargs['saved']}")
        if "skipped" in kwargs:
            self.stat_skipped.configure(text=f"Skipped: {kwargs['skipped']}")
        if "screened_out" in kwargs:
            pass  # shown in log only
        if "errors" in kwargs:
            self.stat_errors.configure(text=f"Errors: {kwargs['errors']}")

    # ------------------------------------------------------------------
    # Pipeline control
    # ------------------------------------------------------------------

    def _close_http_client(self):
        """Safely close the shared HTTP client on the main thread."""
        if self._http_client is not None:
            try:
                self._http_client.close()
            except Exception:
                pass
            self._http_client = None

    def _start_pipeline(self):
        if self.is_running:
            return

        # Close any previous HTTP client on the main thread before
        # creating a new one. Prevents race where an after()-scheduled
        # close from a prior run hits the new client's TLS context.
        self._close_http_client()

        if self._sync_config:
            try:
                self._sync_config()
            except Exception:
                pass

        self._stop_flag = False
        self.is_running = True
        # Persist lookback preference so it survives restarts
        self.config.pubmed.default_since_days = self._lookback_days
        ctx = self.context_textbox.get("1.0", "end").strip()
        self._project_context = "" if ctx == self._placeholder_text else ctx
        self._advanced_terms = self._parse_advanced_terms()

        # Derive search terms from include_to_expand only when profile keywords
        # are empty.  Must Include terms are kept as AND filters — they should
        # not be doubled up in the OR block.  Run Focus text is for Pass 2
        # scoring only and is never used as a PubMed keyword.
        if not self.config.profile.keywords:
            self._derived_keywords = [
                t for t in (self._advanced_terms.get("include_to_expand") or [])
                if t
            ]
            if self._derived_keywords:
                self._log(
                    f"Profile keywords empty — using expand terms: "
                    f"{', '.join(self._derived_keywords)}"
                )
        else:
            self._derived_keywords = []

        self.run_btn.configure(state="disabled", text="Running...")
        self.stop_btn.configure(state="normal")
        self._generate_btn.configure(state="disabled")
        self.progress.set(0)
        self._update_query_preview()

        self._update_stats(searches=0, articles=0, scored=0, saved=0, skipped=0, errors=0)

        self.log_text.delete("1.0", "end")

        self._log("Starting PaperMatcher PubMed search...", "info")

        # Snapshot all widget values on the main thread — never read CTk
        # widgets or tkinter vars from the background pipeline thread.
        self._pipeline_max_results = self._max_results_var.get()
        self._pipeline_since_days = self._lookback_days
        self._pipeline_use_mesh = self._mesh_var.get()
        self._pipeline_raw_query = (
            self._query_preview_box.get("1.0", "end").strip()
            if self._query_user_edited else None
        )
        self._pipeline_pub_types = self._pub_type_query_terms()
        self._pipeline_pub_type_exclude = self._pub_type_exclude.get()

        # Create shared HTTP client on the main thread to avoid
        # macOS SIGBUS from native TLS context creation in background threads.
        self._http_client = httpx.Client(timeout=30.0)

        self.pipeline_thread = threading.Thread(target=self._run_pipeline, daemon=True)
        self.pipeline_thread.start()

    def _stop_pipeline(self):
        """Signal the pipeline to stop. Second click force-resets the UI immediately."""
        if self._stop_flag:
            # Second click: pipeline is blocked in an API call — reset UI now
            # and let the thread finish in the background (it will exit on next check).
            self.is_running = False
            self._stop_flag = False
            self.run_btn.configure(state="normal", text="Run Pipeline")
            self.stop_btn.configure(state="disabled", text="Stop")
            self._generate_btn.configure(state="normal")
            self.status_label.configure(text="Force stopped", text_color="#FF9800")
            self._log("Force stopped. Current API call may still finish in background.", "warning")
            return
        self._stop_flag = True
        self._log("Stop requested — will stop after current API call (click again to force).", "warning")
        self.stop_btn.configure(text="Force Stop")  # keep enabled for second click

    def _run_pipeline(self):
        # Attach a handler that forwards pipeline module logs to the live log.
        _live_handler = _PipelineLogHandler(self._log_queue)
        _watched_loggers = [
            logging.getLogger("app.pipeline.pubmed_scraper"),
            logging.getLogger("app.pipeline.relevance_scorer"),
            logging.getLogger("app.pipeline.summarizer"),
            logging.getLogger("app.pipeline.content_fetcher"),
            logging.getLogger("app.utils.llm_client"),  # API errors, timeouts, rate limits
        ]
        for _pl in _watched_loggers:
            _pl.addHandler(_live_handler)

        try:
            stats = {
                "searches": 0, "articles": 0, "scored": 0,
                "saved": 0, "skipped": 0, "screened_out": 0, "errors": 0
            }

            # Step 1: Validate config
            self.master.after(0, lambda: self.status_label.configure(text="Validating configuration..."))
            self._log("Step 1/5: Validating configuration...")

            if not self._validate_config():
                self.master.after(0, lambda: self.status_label.configure(text="Configuration invalid"))
                self._log("Configuration validation failed. Please check your settings.", "error")
                return

            self.master.after(0, lambda: self.progress.set(0.05))

            # Step 2: Check Ollama availability (Pass 1 screening)
            ollama_ok = self._check_ollama()
            if not ollama_ok:
                self._log("WARNING: Ollama not available. Pass 1 screening disabled — all articles go straight to Pass 2 scoring.", "warning")
                self.master.after(0, lambda: self.status_label.configure(text="Ollama unavailable — screening disabled"))
            else:
                self._log("Ollama detected — Pass 1 screening enabled")

            # Step 3: Search PubMed
            self.master.after(0, lambda: self.status_label.configure(text="Searching PubMed..."))
            self._log("Step 2/5: Searching PubMed by keywords...")

            scraper = PubMedScraper(
                max_results=self._pipeline_max_results,
                session=self._http_client,
                api_key=self.config.llm.ncbi_api_key,
                batch_size=200,
            )
            keywords = self.config.profile.keywords or self._derived_keywords
            must_include = self._advanced_terms.get("must_include", [])
            include_to_expand = self._advanced_terms.get("include_to_expand", [])

            # If no broad OR-keywords yet but must_include terms exist, use
            # them for the OR block too.  The scraper returns early when the OR
            # block is empty, so this keeps the query valid.  The AND
            # [Title/Abstract] filter still applies, so results are correct.
            if not keywords and must_include:
                keywords = list(must_include)

            if not keywords and not self._query_user_edited:
                self._log("No keywords configured. Please add keywords in the Profile tab.", "warning")
                self.master.after(0, lambda: self.status_label.configure(text="No keywords"))
                return

            since_days = self._pipeline_since_days
            use_mesh = self._pipeline_use_mesh

            # Pre-fetch seen PMIDs/DOIs so the scraper can skip them at
            # the esearch level, allowing the pipeline to move beyond the
            # first max_results papers on repeated runs for the same query.
            seen_pmids, seen_dois = self.db.get_seen_ids()
            self._log(f"Excluding {len(seen_pmids)} already-seen articles from search pool")

            if self._pipeline_raw_query is not None:
                # User has customised the query — use it verbatim, no MeSH expansion
                self._log("Using custom query (MeSH expansion skipped)")
                articles = scraper.search_with_query(
                    self._pipeline_raw_query,
                    since_days=since_days,
                    exclude_pmids=seen_pmids,
                )
            else:
                # Build query from profile keywords + advanced terms
                articles = scraper.search_by_keywords(
                    keywords,
                    since_days=since_days,
                    must_include=must_include,
                    include_to_expand=include_to_expand,
                    do_not_include=self._advanced_terms.get("do_not_include", []),
                    pub_types=self._pipeline_pub_types,
                    pub_type_exclude=self._pipeline_pub_type_exclude,
                    use_mesh=use_mesh,
                    exclude_pmids=seen_pmids,
                )

            stats["searches"] = 1
            stats["articles"] = len(articles)
            self.master.after(0, lambda: self._update_stats(**stats))
            self._log(f"Found {len(articles)} articles from PubMed", "success")

            self.master.after(0, lambda: self.progress.set(0.2))

            if not articles:
                self._log("No articles found. Try broadening your keywords or increasing the lookback period.", "warning")
                self.master.after(0, lambda: self.status_label.configure(text="No articles found"))
                return

            # Step 4: Initialize pipeline components
            self.master.after(0, lambda: self.status_label.configure(text="Fetching & scoring articles..."))
            self._log("Step 3/5: Fetching content, screening (Pass 1), and scoring (Pass 2)...")

            # Determine Pass 2 model for logging
            pass2_model = self._resolve_pass2_model_label()
            self._log(f"Pass 2 scoring model: {pass2_model}")

            llm = LLMClient(
                base_url=self.config.llm.base_url,
                api_key=self.config.llm.api_key,
                model=self.config.llm.model,
                scoring_model=self.config.llm.scoring_model,
                openrouter_key=self.config.llm.openrouter_key,
                screening_model=self.config.llm.screening_model,
                screening_model_name=self.config.llm.screening_model_name,
                screening_base_url=self.config.llm.screening_base_url,
                screening_api_key=self.config.llm.screening_api_key,
            )

            fetcher = ContentFetcher()
            scorer = RelevanceScorer(llm, db=self.db, project_context=self._project_context)
            summarizer = Summarizer(llm)

            # Use self._saved_articles so the review popup can access it after pipeline
            self._saved_articles = []
            threshold = self.config.llm.relevance_threshold

            for i, article_data in enumerate(articles):
                if self._stop_flag:
                    self._log("Pipeline stopped by user.", "warning")
                    break

                pct = 0.20 + (0.7 * (i / len(articles)))
                self.master.after(0, lambda v=pct: self.progress.set(v))

                title = article_data.get("title", "Untitled")
                self._log(f"  [{i+1}/{len(articles)}] {title[:70]}...")

                if self.db.is_rejected(
                    doi=article_data.get("doi", ""),
                    pmid=article_data.get("pmid", ""),
                    or_keywords=keywords,
                    and_keywords=must_include,
                ):
                    stats["skipped"] += 1
                    self._log(f"    Previously rejected by user, skipping.")
                    self.master.after(0, lambda: self._update_stats(**stats))
                    continue

                if self.db.is_processed(
                    doi=article_data.get("doi", ""),
                    pmid=article_data.get("pmid", ""),
                    or_keywords=keywords,
                    and_keywords=must_include,
                ):
                    stats["skipped"] += 1
                    self._log(f"    Already processed, skipping.")
                    self.master.after(0, lambda: self._update_stats(**stats))
                    continue

                # Fetch content
                self._log(f"    Fetching content...")
                content = fetcher.fetch(article_data)
                if not content.get("abstract"):
                    stats["skipped"] += 1
                    self._log(f"    No abstract available, skipping.")
                    self.master.after(0, lambda: self._update_stats(**stats))
                    continue

                article_data["abstract"] = content["abstract"]
                article_data["title"] = content.get("title", article_data.get("title", ""))

                # Pass 1: Screen
                if ollama_ok:
                    screened = scorer.screen(article_data, keywords)
                    if not screened:
                        stats["screened_out"] += 1
                        self._log(f"    Pass 1: screened out (not relevant)")
                        self.master.after(0, lambda: self._update_stats(**stats))
                        continue

                # Pass 2: Score
                self._log(f"    Scoring with Pass 2...")
                score, reason = scorer.score(article_data, keywords, must_include,
                                             self._advanced_terms.get("include_to_expand", []),
                                             self._advanced_terms.get("do_not_include", []))
                article_data["relevance_score"] = score
                article_data["relevance_reason"] = reason
                stats["scored"] += 1
                self.master.after(0, lambda: self._update_stats(**stats))

                self._log(f"    Relevance score: {score}/10")

                if scorer.should_save(score, threshold):
                    if self._stop_flag:
                        self._log("Pipeline stopped by user.", "warning")
                        break
                    self._log(f"    Score {score} >= threshold {threshold}, generating summary...", "success")

                    summary = summarizer.summarize(profile, article_data)
                    article_data["summary"] = summary.get("summary", "")
                    article_data["implications"] = summary.get("implications", "")
                    article_data["methodology"] = summary.get("methodology", "")
                    article_data["conflict_bias"] = summary.get("conflict_bias", "")
                    article_data["reproducibility"] = summary.get("reproducibility", "")
                    article_data["relevance_reason"] = summary.get("relevance_note", "")
                    article_data["key_points"] = summary.get("key_points", [])
                    article_data["tags"] = summary.get("tags", [])

                    # Mark processed and get ID for feedback
                    article_id = self.db.mark_processed(article_data)
                    article_data["id"] = article_id
                    self._saved_articles.append(article_data)
                    stats["saved"] += 1
                    self._log(f"    Saved to results (score {score}/10)", "success")
                else:
                    stats["skipped"] += 1
                    self._log(f"    Score {score} below threshold {threshold}, skipping.")

                self.master.after(0, lambda: self._update_stats(**stats))

            self.master.after(0, lambda: self.progress.set(1.0))

            self.config.last_run = datetime.now().isoformat()
            self.config.save()

            self._log("=== Pipeline Complete ===", "success")
            self._log(f"  PubMed searches: {stats['searches']}")
            self._log(f"  Articles found: {stats['articles']}")
            self._log(f"  Pass 1 screened out: {stats['screened_out']}")
            self._log(f"  Articles scored (Pass 2): {stats['scored']}")
            self._log(f"  Articles saved: {stats['saved']}")
            self._log(f"  Skipped (below threshold): {stats['skipped']}")
            if stats["errors"] > 0:
                self._log(f"  Errors: {stats['errors']}", "error")

            self.db.log_run(
                searches=stats["searches"],
                articles_found=stats["articles"],
                articles_scored=stats["scored"],
                articles_saved=stats["saved"],
                errors=stats["errors"],
            )

            # Signal the poll loop to open the review popup on the main thread.
            # Never call after() for this — single-shot after() from bg threads is unreliable.
            if self._saved_articles:
                self._log(f"Opening review popup for {len(self._saved_articles)} articles...", "info")
                self._pending_review = list(self.config.profile.keywords or [])

            self.master.after(0, lambda: self.status_label.configure(
                text=f"Complete! Saved {stats['saved']} articles",
                text_color="#4CAF50",
            ))

        except PipelineStoppedException:
            self._log("Pipeline stopped by user.", "warning")
            self.master.after(0, lambda: self.status_label.configure(
                text="Stopped",
                text_color="#FF9800",
            ))
        except Exception as e:
            self._log(f"Pipeline error: {e}", "error")
            self._log(traceback.format_exc(), "error")
            self.master.after(0, lambda: self.status_label.configure(
                text=f"Pipeline failed: {e}",
                text_color="#F44336",
            ))
        finally:
            for _pl in _watched_loggers:
                _pl.removeHandler(_live_handler)
            self.is_running = False
            self._stop_flag = False
            self._pipeline_done = True

    def _run_pipeline(self):
        # Attach a handler that forwards pipeline module logs to the live log.
        _live_handler = _PipelineLogHandler(self._log_queue)
        _watched_loggers = [
            logging.getLogger("app.pipeline.pubmed_scraper"),
            logging.getLogger("app.pipeline.relevance_scorer"),
            logging.getLogger("app.pipeline.summarizer"),
            logging.getLogger("app.pipeline.content_fetcher"),
            logging.getLogger("app.utils.llm_client"),  # API errors, timeouts, rate limits
        ]
        for _pl in _watched_loggers:
            _pl.addHandler(_live_handler)

        try:
            stats = {
                "searches": 0, "articles": 0, "scored": 0,
                "saved": 0, "skipped": 0, "screened_out": 0, "errors": 0
            }

            # Step 1: Validate config
            self.master.after(0, lambda: self.status_label.configure(text="Validating configuration..."))
            self._log("Step 1/5: Validating configuration...")

            if not self._validate_config():
                self.master.after(0, lambda: self.status_label.configure(text="Configuration invalid"))
                self._log("Configuration validation failed. Please check your settings.", "error")
                return

            self.master.after(0, lambda: self.progress.set(0.05))

            # Step 2: Check Ollama availability (Pass 1 screening)
            ollama_ok = self._check_ollama()
            if not ollama_ok:
                self._log("WARNING: Ollama not available. Pass 1 screening disabled — all articles go straight to Pass 2 scoring.", "warning")
                self.master.after(0, lambda: self.status_label.configure(text="Ollama unavailable — screening disabled"))
            else:
                self._log("Ollama detected — Pass 1 screening enabled")

            # Step 3: Search PubMed
            self.master.after(0, lambda: self.status_label.configure(text="Searching PubMed..."))
            self._log("Step 2/5: Searching PubMed by keywords...")

            scraper = PubMedScraper(
                max_results=self._pipeline_max_results,
                session=self._http_client,
                api_key=self.config.llm.ncbi_api_key,
                batch_size=200,
            )
            keywords = self.config.profile.keywords or self._derived_keywords
            must_include = self._advanced_terms.get("must_include", [])
            include_to_expand = self._advanced_terms.get("include_to_expand", [])

            # If no broad OR-keywords yet but must_include terms exist, use
            # them for the OR block too.  The scraper returns early when the OR
            # block is empty, so this keeps the query valid.  The AND
            # [Title/Abstract] filter still applies, so results are correct.
            if not keywords and must_include:
                keywords = list(must_include)

            if not keywords and not self._query_user_edited:
                self._log("No keywords configured. Please add keywords in the Profile tab.", "warning")
                self.master.after(0, lambda: self.status_label.configure(text="No keywords"))
                return

            since_days = self._pipeline_since_days
            use_mesh = self._pipeline_use_mesh

            # Pre-fetch seen PMIDs/DOIs so the scraper can skip them at
            # the esearch level, allowing the pipeline to move beyond the
            # first max_results papers on repeated runs for the same query.
            seen_pmids, seen_dois = self.db.get_seen_ids()
            self._log(f"Excluding {len(seen_pmids)} already-seen articles from search pool")

            if self._pipeline_raw_query is not None:
                # User has customised the query — use it verbatim, no MeSH expansion
                self._log("Using custom query (MeSH expansion skipped)")
                articles = scraper.search_with_query(
                    self._pipeline_raw_query,
                    since_days=since_days,
                    exclude_pmids=seen_pmids,
                )
            else:
                # Build query from profile keywords + advanced terms
                articles = scraper.search_by_keywords(
                    keywords,
                    since_days=since_days,
                    must_include=must_include,
                    include_to_expand=include_to_expand,
                    do_not_include=self._advanced_terms.get("do_not_include", []),
                    pub_types=self._pipeline_pub_types,
                    pub_type_exclude=self._pipeline_pub_type_exclude,
                    use_mesh=use_mesh,
                    exclude_pmids=seen_pmids,
                )

            stats["searches"] = 1
            stats["articles"] = len(articles)
            self.master.after(0, lambda: self._update_stats(**stats))
            self._log(f"Found {len(articles)} articles from PubMed", "success")

            self.master.after(0, lambda: self.progress.set(0.2))

            if not articles:
                self._log("No articles found. Try broadening your keywords or increasing the lookback period.", "warning")
                self.master.after(0, lambda: self.status_label.configure(text="No articles found"))
                return

            # Step 4: Initialize pipeline components
            self.master.after(0, lambda: self.status_label.configure(text="Fetching & scoring articles..."))
            self._log("Step 3/5: Fetching content, screening (Pass 1), and scoring (Pass 2)...")

            # Determine Pass 2 model for logging
            pass2_model = self._resolve_pass2_model_label()
            self._log(f"Pass 2 scoring model: {pass2_model}")

            llm = LLMClient(
                base_url=self.config.llm.base_url,
                api_key=self.config.llm.api_key,
                model=self.config.llm.model,
                scoring_model=self.config.llm.scoring_model,
                openrouter_key=self.config.llm.openrouter_key,
                screening_model=self.config.llm.screening_model,
                screening_model_name=self.config.llm.screening_model_name,
                screening_base_url=self.config.llm.screening_base_url,
                screening_api_key=self.config.llm.screening_api_key,
            )

            fetcher = ContentFetcher()
            scorer = RelevanceScorer(llm, db=self.db, project_context=self._project_context)
            summarizer = Summarizer(llm)

            # Use self._saved_articles so the review popup can access it after pipeline
            self._saved_articles = []
            threshold = self.config.llm.relevance_threshold

            for i, article_data in enumerate(articles):
                if self._stop_flag:
                    self._log("Pipeline stopped by user.", "warning")
                    break

                pct = 0.20 + (0.7 * (i / len(articles)))
                self.master.after(0, lambda v=pct: self.progress.set(v))

                title = article_data.get("title", "Untitled")
                self._log(f"  [{i+1}/{len(articles)}] {title[:70]}...")

                if self.db.is_rejected(
                    doi=article_data.get("doi", ""),
                    pmid=article_data.get("pmid", ""),
                    or_keywords=keywords,
                    and_keywords=must_include,
                ):
                    stats["skipped"] += 1
                    self._log(f"    Previously rejected by user, skipping.")
                    self.master.after(0, lambda: self._update_stats(**stats))
                    continue

                if self.db.is_processed(
                    doi=article_data.get("doi", ""),
                    pmid=article_data.get("pmid", ""),
                    title=title,
                ):
                    stats["skipped"] += 1
                    self._log(f"    Already processed, skipping.")
                    self.master.after(0, lambda: self._update_stats(**stats))
                    continue

                try:
                    # Skip fetch if scraper already provided the abstract
                    if article_data.get("abstract"):
                        self._log(f"    Abstract already available from batch fetch, skipping re-fetch.")
                    else:
                        fetched = fetcher.fetch_article(
                            url=article_data.get("url", ""),
                            title=title,
                            authors=article_data.get("authors", []),
                        )

                        for key in ["title", "authors", "journal", "volume", "issue",
                                    "date", "doi", "pmid", "abstract", "article_type"]:
                            if fetched.get(key) and not article_data.get(key):
                                article_data[key] = fetched[key]

                        if fetched.get("abstract"):
                            article_data["abstract"] = fetched["abstract"]

                        if not article_data.get("abstract"):
                            self._log(f"    No abstract available, scoring from title only", "warning")
                            article_data["abstract"] = "[Abstract not available]"

                    # --- Pass 1: Screening ---
                    profile = self.config.profile.to_dict()
                    if ollama_ok:
                        passed_screening = scorer.llm.screen_article(
                            article_data.get("title", ""),
                            article_data.get("abstract", ""),
                            profile,
                        )
                        if not passed_screening:
                            stats["screened_out"] += 1
                            self._log(f"    Pass 1: screened out (not relevant)")
                            self.master.after(0, lambda: self._update_stats(**stats))
                            continue
                        self._log(f"    Pass 1: passed screening")

                    # --- Pass 2: Scoring ---
                    score, reason = scorer.score_article(
                        profile, article_data,
                        current_keywords=keywords,
                        must_include=self._advanced_terms.get("must_include", []),
                        include_to_expand=self._advanced_terms.get("include_to_expand", []),
                        do_not_include=self._advanced_terms.get("do_not_include", []),
                    )
                    article_data["relevance_score"] = score
                    article_data["relevance_reason"] = reason
                    stats["scored"] += 1
                    self.master.after(0, lambda: self._update_stats(**stats))

                    self._log(f"    Relevance score: {score}/10")

                    if scorer.should_save(score, threshold):
                        if self._stop_flag:
                            self._log("Pipeline stopped by user.", "warning")
                            break
                        self._log(f"    Score {score} >= threshold {threshold}, generating summary...", "success")

                        summary = summarizer.summarize(profile, article_data)
                        article_data["summary"] = summary.get("summary", "")
                        article_data["implications"] = summary.get("implications", "")
                        article_data["methodology"] = summary.get("methodology", "")
                        article_data["conflict_bias"] = summary.get("conflict_bias", "")
                        article_data["reproducibility"] = summary.get("reproducibility", "")
                        article_data["relevance_reason"] = summary.get("relevance_note", "")
                        article_data["key_points"] = summary.get("key_points", [])
                        article_data["tags"] = summary.get("tags", [])

                        # Mark processed and get ID for feedback
                        article_id = self.db.mark_processed(article_data)
                        article_data["id"] = article_id
                        self._saved_articles.append(article_data)
                        stats["saved"] += 1
                        self._log(f"    Saved to results (score {score}/10)", "success")
                    else:
                        stats["skipped"] += 1
                        self._log(f"    Score {score} below threshold {threshold}, skipping.")

                    self.master.after(0, lambda: self._update_stats(**stats))

                except Exception as e:
                    stats["errors"] += 1
                    self._log(f"    Error processing article: {e}", "error")
                    self.master.after(0, lambda: self._update_stats(**stats))

            self.master.after(0, lambda: self.progress.set(1.0))

            self.config.last_run = datetime.now().isoformat()
            self.config.save()

            self._log("=== Pipeline Complete ===", "success")
            self._log(f"  PubMed searches: {stats['searches']}")
            self._log(f"  Articles found: {stats['articles']}")
            self._log(f"  Pass 1 screened out: {stats['screened_out']}")
            self._log(f"  Articles scored (Pass 2): {stats['scored']}")
            self._log(f"  Articles saved: {stats['saved']}")
            self._log(f"  Skipped (below threshold): {stats['skipped']}")
            if stats["errors"] > 0:
                self._log(f"  Errors: {stats['errors']}", "error")

            self.db.log_run(
                searches=stats["searches"],
                articles_found=stats["articles"],
                articles_scored=stats["scored"],
                articles_saved=stats["saved"],
                errors=stats["errors"],
            )

            # Signal the poll loop to open the review popup on the main thread.
            # Never call after() for this — single-shot after() from bg threads is unreliable.
            if self._saved_articles:
                self._log(f"Opening review popup for {len(self._saved_articles)} articles...", "info")
                self._pending_review = list(self.config.profile.keywords or [])

            self.master.after(0, lambda: self.status_label.configure(
                text=f"Complete! Saved {stats['saved']} articles",
                text_color="#4CAF50",
            ))

        except Exception as e:
            self._log(f"Pipeline error: {e}", "error")
            self._log(traceback.format_exc(), "error")
            self.master.after(0, lambda: self.status_label.configure(
                text=f"Pipeline failed: {e}",
                text_color="#F44336",
            ))
        finally:
            for _pl in _watched_loggers:
                _pl.removeHandler(_live_handler)
            self.is_running = False
            self._stop_flag = False
            self._pipeline_done = True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_pub_type_filter(self) -> str:
        """Build publication type filter string for PubMed query.

        Returns a NOT clause if exclude=True, or nothing if no types selected.
        """
        selected = []
        for label, query_term in [
            ("Review", "Review[Publication Type]"),
            ("Original Research", "Journal Article[Publication Type]"),
            ("Clinical Trial", "Clinical Trial[Publication Type]"),
            ("Meta-Analysis", "Meta-Analysis[Publication Type]"),
            ("Systematic Review", "Systematic Review[Publication Type]"),
        ]:
            if self._pub_types.get(label) and self._pub_types[label].get():
                selected.append(query_term)

        if not selected:
            return ""

        if self._pub_type_exclude.get():
            return f"NOT ({' OR '.join(selected)})"
        else:
            # Include: use AND with OR of types
            return f"AND ({' OR '.join(selected)})"

    def _pub_type_query_terms(self) -> List[str]:
        """Resolve publication type checkboxes to PubMed query terms.

        Returns a list of Publication Type query strings for the scraper.
        """
        terms = []
        for label, query_term in [
            ("Review", "Review[Publication Type]"),
            ("Original Research", "Journal Article[Publication Type]"),
            ("Clinical Trial", "Clinical Trial[Publication Type]"),
            ("Meta-Analysis", "Meta-Analysis[Publication Type]"),
            ("Systematic Review", "Systematic Review[Publication Type]"),
        ]:
            if self._pub_types.get(label) and self._pub_types[label].get():
                terms.append(query_term)
        return terms

    def _open_review_popup(self, keywords: List[str]):
        """Launch the Tinder-style review popup. Always called on the main thread."""
        try:
            must_include = self._advanced_terms.get("must_include", [])
            self._review_popup = ReviewPopup(
                self.master.winfo_toplevel(),
                self.db,
                self._saved_articles,
                list(self.config.profile.keywords or []),
                keywords,
                must_include_keywords=must_include,
            )
        except Exception as exc:
            logger.error("Review popup failed to open: %s", exc, exc_info=True)
            self._log(f"Review popup error: {exc}", "error")

    def _check_ollama(self) -> bool:
        """Check if Ollama is running and llama3.2:latest is available."""
        try:
            import urllib.request
            with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2) as resp:
                data = json.loads(resp.read())
                models = {m["name"] for m in data.get("models", [])}
                return any("llama3.2" in m for m in models)
        except Exception:
            return False

    def _resolve_pass2_model_label(self) -> str:
        """Return a human-readable label for the Pass 2 model."""
        from app.utils.llm_client import DISTRIBUTION_TIER
        if DISTRIBUTION_TIER == "vip":
            return "deepseek/deepseek-v4-flash:free (VIP tier)"
        if self.config.llm.scoring_model == "cloud":
            return f"{self.config.llm.model} (cloud, prototype tier)"
        return "llama3.2:latest (local, prototype tier)"

    def _validate_config(self) -> bool:
        errors = []

        has_keywords = (
            self.config.profile.keywords
            or self._derived_keywords
            or self._query_user_edited
        )
        if not has_keywords:
            errors.append(
                "Keywords not configured. "
                "Add keywords in the Profile tab, fill in Run Focus / structured "
                "search terms, or edit the query preview directly."
            )

        if not self.config.llm.model:
            errors.append("LLM model not configured")

        if not self.config.profile.research_description:
            self._log("Warning: Research description is empty - scoring may be less accurate", "warning")

        if errors:
            for err in errors:
                self._log(f"Config error: {err}", "error")
            return False

        return True