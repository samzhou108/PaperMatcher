"""Pipeline execution tab with live logging and progress."""

import json
import queue
import re
import threading
import traceback
from datetime import datetime
from typing import Optional

import customtkinter as ctk

from app.models.config import AppConfig
from app.pipeline.pubmed_scraper import PubMedScraper
from app.pipeline.content_fetcher import ContentFetcher
from app.pipeline.relevance_scorer import RelevanceScorer
from app.pipeline.summarizer import Summarizer
from app.utils.db import ArticleDatabase
from app.utils.llm_client import LLMClient


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
        self._pipeline_done = False
        self._project_context = ""
        self._advanced_terms: dict[str, list[str]] = {
            "must_include": [],
            "include_to_expand": [],
            "do_not_include": [],
        }
        self._build_ui()
        self._poll_log_queue()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        """Build the run tab UI."""
        controls = ctk.CTkFrame(self.master, fg_color="transparent")
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

        # ---- Project context ----
        context_frame = ctk.CTkFrame(self.master, fg_color="transparent")
        context_frame.pack(fill="x", pady=(10, 6))

        ctk.CTkLabel(
            context_frame,
            text="Project Context:",
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
            "Optional: describe the specific focus of this run "
            "(e.g. 'Focus on female-specific microglial responses. Exclude reviews.')"
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
        self._advanced_frame = ctk.CTkFrame(self.master, fg_color="transparent")

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

        # Include to Expand row
        expand_frame = ctk.CTkFrame(self._advanced_frame, fg_color="transparent")
        expand_frame.pack(fill="x", padx=(0, 0), pady=(0, 4))
        ctk.CTkLabel(
            expand_frame, text="Include to Expand:", font=ctk.CTkFont(size=11), width=90, anchor="w"
        ).pack(side="left")
        self._expand_textbox = ctk.CTkTextbox(expand_frame, height=26, wrap="word")
        self._expand_textbox.pack(side="left", fill="x", expand=True, padx=(4, 0))

        # Do Not Include row
        exclude_frame = ctk.CTkFrame(self._advanced_frame, fg_color="transparent")
        exclude_frame.pack(fill="x", padx=(0, 0), pady=(0, 4))
        ctk.CTkLabel(
            exclude_frame, text="Do Not Include:", font=ctk.CTkFont(size=11), width=90, anchor="w"
        ).pack(side="left")
        self._exclude_textbox = ctk.CTkTextbox(exclude_frame, height=26, wrap="word")
        self._exclude_textbox.pack(side="left", fill="x", expand=True, padx=(4, 0))

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
        lookback_row = ctk.CTkFrame(self.master, fg_color="transparent")
        lookback_row.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(
            lookback_row,
            text="Look back:",
            font=ctk.CTkFont(size=12),
            text_color="gray",
        ).pack(side="left", padx=(0, 8))

        self._lookback_var = ctk.IntVar(value=7)
        self._lookback_label = ctk.CTkLabel(
            lookback_row,
            text="7 days",
            font=ctk.CTkFont(size=12),
            width=60,
            anchor="w",
        )

        def _on_lookback(v):
            val = int(float(v))
            self._lookback_var.set(val)
            if val <= 30:
                self._lookback_label.configure(text=f"{val} day{'s' if val != 1 else ''}")
            elif val <= 365:
                months = val // 30
                self._lookback_label.configure(text=f"{months} month{'s' if months != 1 else ''}")
            else:
                years = val / 365
                if years == int(years):
                    self._lookback_label.configure(text=f"{int(years)} year{'s' if int(years) != 1 else ''}")
                else:
                    self._lookback_label.configure(text=f"{years:.1f} years")

        ctk.CTkSlider(
            lookback_row, from_=1, to=3650, number_of_steps=3649,
            variable=self._lookback_var, width=200,
            command=_on_lookback,
        ).pack(side="left")
        self._lookback_label.pack(side="left", padx=(8, 0))

        # ---- Max results slider ----
        max_row = ctk.CTkFrame(self.master, fg_color="transparent")
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

        # ---- Progress ----
        self.progress = ctk.CTkProgressBar(self.master)
        self.progress.pack(fill="x", pady=(0, 10))
        self.progress.set(0)

        # ---- Stats ----
        stats = ctk.CTkFrame(self.master, fg_color="transparent")
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
            self.master,
            text="Live Log",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", pady=(5, 5))

        log_frame = ctk.CTkFrame(self.master)
        log_frame.pack(fill="both", expand=True)

        self.log_text = ctk.CTkTextbox(
            log_frame,
            font=ctk.CTkFont(family="Menlo", size=12),
            wrap="word",
        )
        self.log_text.pack(fill="both", expand=True, padx=5, pady=5)
        self.log_text.configure(state="disabled")

    # ------------------------------------------------------------------
    # Advanced search helpers
    # ------------------------------------------------------------------

    def _toggle_advanced_search(self):
        """Toggle visibility of the advanced search panel."""
        if self._advanced_frame.winfo_ismapped():
            self._advanced_frame.pack_forget()
            self._advanced_search_active = False
            self._advanced_toggle_btn.configure(text="Advanced Search ▼")
        else:
            self._advanced_frame.pack(fill="x", pady=(4, 0), after=self.context_textbox)
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
        if self._pipeline_done:
            self._pipeline_done = False
            self._on_pipeline_done()
        self.master.after(150, self._poll_log_queue)

    def _on_pipeline_done(self):
        """Reset UI after pipeline finishes (called via after() for thread safety)."""
        try:
            self.run_btn.configure(state="normal", text="Run Pipeline")
        except Exception as e:
            print(f"[PaperPilot/WARN] Failed to reset run_btn: {e}")
        try:
            self.stop_btn.configure(state="disabled")
        except Exception as e:
            print(f"[PaperPilot/WARN] Failed to reset stop_btn: {e}")
        self.status_label.configure(text="Ready", text_color="gray")

    def _log(self, message: str, level: str = "info"):
        print(f"[PaperPilot/{level.upper()}] {message}")
        self._log_queue.put((message, level))

    def _do_log(self, message: str, level: str = "info"):
        color = {
            "info": "",
            "success": "#4CAF50",
            "warning": "#FF9800",
            "error": "#F44336",
        }.get(level, "")

        timestamp = datetime.now().strftime("%H:%M:%S")

        self.log_text.configure(state="normal")
        if color:
            self.log_text.insert("end", f"[{timestamp}] {message}\n", level)
            self.log_text.tag_config(level, foreground=color)
        else:
            self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

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

    def _start_pipeline(self):
        if self.is_running:
            return

        if self._sync_config:
            try:
                self._sync_config()
            except Exception:
                pass

        self._stop_flag = False
        self.is_running = True
        self._project_context = self.context_textbox.get("1.0", "end").strip()
        self._advanced_terms = self._parse_advanced_terms()
        self.run_btn.configure(state="disabled", text="Running...")
        self.stop_btn.configure(state="normal")
        self.progress.set(0)

        self._update_stats(searches=0, articles=0, scored=0, saved=0, skipped=0, errors=0)

        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

        self._log("Starting PaperPilot PubMed search...", "info")

        self.pipeline_thread = threading.Thread(target=self._run_pipeline, daemon=True)
        self.pipeline_thread.start()

    def _stop_pipeline(self):
        """Signal the pipeline to stop gracefully, then reset UI immediately."""
        if self._stop_flag:
            # Already stopping — force immediate reset
            self.is_running = False
            self.run_btn.configure(state="normal", text="Run Pipeline")
            self.stop_btn.configure(state="disabled")
            self.status_label.configure(text="Stopped", text_color="#FF9800")
            return
        self._stop_flag = True
        self._log("Stopping pipeline after current article...", "warning")
        self.stop_btn.configure(state="disabled")

    def _run_pipeline(self):
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

            scraper = PubMedScraper(max_results=self._max_results_var.get())
            keywords = self.config.profile.keywords

            if not keywords:
                self._log("No keywords configured. Please add keywords in the Profile tab.", "warning")
                self.master.after(0, lambda: self.status_label.configure(text="No keywords"))
                return

            since_days = self._lookback_var.get()

            # Build effective search query combining profile keywords + advanced terms
            must_include = self._advanced_terms.get("must_include", [])
            include_to_expand = self._advanced_terms.get("include_to_expand", [])

            articles = scraper.search_by_keywords(
                keywords,
                since_days=since_days,
                must_include=must_include,
                include_to_expand=include_to_expand,
                do_not_include=self._advanced_terms.get("do_not_include", []),
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
            )

            fetcher = ContentFetcher()
            scorer = RelevanceScorer(llm, db=self.db, project_context=self._project_context)
            summarizer = Summarizer(llm)

            saved_articles = []
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
                        self._log(f"    Score {score} >= threshold {threshold}, generating summary...", "success")

                        summary = summarizer.summarize(profile, article_data)
                        article_data["summary"] = summary.get("summary", "")
                        article_data["relevance_reason"] = summary.get("relevance_note", "")
                        article_data["key_points"] = summary.get("key_points", [])
                        article_data["tags"] = summary.get("tags", [])

                        self.db.mark_processed(article_data)

                        saved_articles.append(article_data)
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

            self._log("", "info")
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
            self.is_running = False
            self._stop_flag = False
            self._pipeline_done = True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
            return "baidu/Qianfan-OCR-Fast:free (VIP tier)"
        if self.config.llm.scoring_model == "cloud":
            return f"{self.config.llm.model} (cloud, prototype tier)"
        return "llama3.2:latest (local, prototype tier)"

    def _validate_config(self) -> bool:
        errors = []

        if not self.config.profile.keywords:
            errors.append("Keywords not configured")

        if not self.config.llm.model:
            errors.append("LLM model not configured")

        if not self.config.profile.research_description:
            self._log("Warning: Research description is empty - scoring may be less accurate", "warning")

        if errors:
            for err in errors:
                self._log(f"Config error: {err}", "error")
            return False

        return True