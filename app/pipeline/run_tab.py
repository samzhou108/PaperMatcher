"""Pipeline execution tab with live logging and progress."""

import queue
import threading
import traceback
from datetime import datetime
from typing import Optional

import customtkinter as ctk

from app.models.config import AppConfig
from app.models.article import Article
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
        self._sync_config = sync_config  # optional callback: syncs UI -> config before run
        self.is_running = False
        self.pipeline_thread: Optional[threading.Thread] = None
        self._stop_flag: bool = False
        self._log_queue: queue.Queue = queue.Queue()

        self._build_ui()
        self._poll_log_queue()

    def _build_ui(self):
        """Build the run tab UI."""
        # Top controls
        controls = ctk.CTkFrame(self.master, fg_color="transparent")
        controls.pack(fill="x", pady=(0, 6))

        self.run_btn = ctk.CTkButton(
            controls,
            text="Search PubMed & Score",
            font=ctk.CTkFont(size=14, weight="bold"),
            width=180,
            height=40,
            command=self._start_pipeline,
        )
        self.run_btn.pack(side="left", padx=(0, 15))

        self.stop_btn = ctk.CTkButton(
            controls,
            text="Stop",
            width=100,
            height=40,
            fg_color="#F44336",
            hover_color="#D32F2F",
            state="disabled",
            command=self._stop_pipeline,
        )
        self.stop_btn.pack(side="left", padx=(5, 15))

        self.status_label = ctk.CTkLabel(
            controls,
            text="Ready",
            font=ctk.CTkFont(size=13),
            text_color="gray",
        )
        self.status_label.pack(side="left")

        # Lookback slider
        lookback_row = ctk.CTkFrame(self.master, fg_color="transparent")
        lookback_row.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(
            lookback_row,
            text="Look back:",
            font=ctk.CTkFont(size=12),
            text_color="gray",
        ).pack(side="left", padx=(0, 8))

        self._lookback_var = ctk.IntVar(value=self.config.pubmed.default_since_days or 7)
        self._lookback_label = ctk.CTkLabel(
            lookback_row,
            text="7 days",
            font=ctk.CTkFont(size=12),
            width=52,
            anchor="w",
        )

        def _on_lookback(v):
            days = int(float(v))
            self._lookback_var.set(days)
            self._lookback_label.configure(text=f"{days} day{'s' if days != 1 else ''}")

        ctk.CTkSlider(
            lookback_row,
            from_=1,
            to=365,
            number_of_steps=364,
            variable=self._lookback_var,
            width=200,
            command=_on_lookback,
        ).pack(side="left")
        self._lookback_label.pack(side="left", padx=(8, 0))

        # Result count label
        self.result_count_label = ctk.CTkLabel(
            lookback_row,
            text="",
            font=ctk.CTkFont(size=12),
            text_color="gray",
        )
        self.result_count_label.pack(side="left", padx=(10, 0))

        # Progress bar
        self.progress = ctk.CTkProgressBar(self.master)
        self.progress.pack(fill="x", pady=(0, 10))
        self.progress.set(0)

        # Stats row
        stats = ctk.CTkFrame(self.master, fg_color="transparent")
        stats.pack(fill="x", pady=(0, 10))

        self.stat_searched = ctk.CTkLabel(stats, text="Searched: 0", font=ctk.CTkFont(size=12))
        self.stat_searched.pack(side="left", padx=(0, 20))

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

        # Log area
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

    def _poll_log_queue(self):
        """Drain the log queue on the main thread every 150 ms."""
        try:
            while True:
                message, level = self._log_queue.get_nowait()
                if level == "__reset_button__":
                    try:
                        self.run_btn.configure(state="normal", text="Search PubMed & Score")
                    except Exception:
                        pass
                else:
                    self._do_log(message, level)
        except queue.Empty:
            pass
        self.master.after(150, self._poll_log_queue)

    def _log(self, message: str, level: str = "info"):
        """Thread-safe: put message in queue; also print to terminal."""
        print(f"[PaperMatcher/{level.upper()}] {message}")
        self._log_queue.put((message, level))

    def _do_log(self, message: str, level: str = "info"):
        """Write to log widget - called only from main thread via poll."""
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
        """Update statistics labels."""
        if "searched" in kwargs:
            self.stat_searched.configure(text=f"Searched: {kwargs['searched']}")
        if "articles" in kwargs:
            self.stat_articles.configure(text=f"Articles: {kwargs['articles']}")
        if "scored" in kwargs:
            self.stat_scored.configure(text=f"Scored: {kwargs['scored']}")
        if "saved" in kwargs:
            self.stat_saved.configure(text=f"Saved: {kwargs['saved']}")
        if "skipped" in kwargs:
            self.stat_skipped.configure(text=f"Skipped: {kwargs['skipped']}")
        if "errors" in kwargs:
            self.stat_errors.configure(text=f"Errors: {kwargs['errors']}")

    def _start_pipeline(self):
        """Start the pipeline in a background thread."""
        if self.is_running:
            return

        # Sync UI fields -> config object so the latest profile/settings are used
        if self._sync_config:
            try:
                self._sync_config()
            except Exception:
                pass

        self.is_running = True
        self._stop_flag = False
        self.run_btn.configure(state="disabled", text="Running...")
        self.stop_btn.configure(state="normal")
        self.progress.set(0)

        # Reset stats counters
        self._update_stats(searched=0, articles=0, scored=0, saved=0, skipped=0, errors=0)

        # Clear log
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

        self._log("Starting PaperMatcher pipeline (PubMed scraper)...", "info")

        self.pipeline_thread = threading.Thread(target=self._run_pipeline, daemon=True)
        self.pipeline_thread.start()

    def _stop_pipeline(self):
        """Signal the pipeline to stop after the current article."""
        self._stop_flag = True
        self.stop_btn.configure(state="disabled", text="Stopping...")
        self._log("Stopping pipeline after current article...", "warning")

    def _run_pipeline(self):
        """Execute the full pipeline using PubMed scraper."""
        try:
            stats = {"searched": 0, "articles": 0, "scored": 0, "saved": 0, "skipped": 0, "errors": 0}

            # Step 1: Validate config
            self.master.after(0, lambda: self.status_label.configure(text="Validating configuration..."))
            self._log("Step 1/4: Validating configuration...")

            if not self._validate_config():
                self.master.after(0, lambda: self.status_label.configure(text="Configuration invalid"))
                self._log("Configuration validation failed. Please check your settings.", "error")
                return

            self.master.after(0, lambda: self.progress.set(0.1))

            # Step 2: Search PubMed
            self.master.after(0, lambda: self.status_label.configure(text="Searching PubMed..."))
            lookback_days = self._lookback_var.get()
            self._log(f"Step 2/4: Searching PubMed (keywords + journals, last {lookback_days} days)...")

            try:
                fetcher = ContentFetcher()
                keywords = self.config.profile.keywords
                journals = self.config.pubmed.journals_to_monitor

                self._log(f"  Keywords: {keywords}")
                self._log(f"  Journals: {journals if journals else '(none configured)'}")

                articles = fetcher.search_articles(
                    keywords=keywords,
                    journals=journals,
                    since_days=lookback_days,
                )
                stats["searched"] = 1
                stats["articles"] = len(articles)
                self.master.after(0, lambda: self._update_stats(**stats))
                self._log(f"Found {len(articles)} article(s) from PubMed", "success")
                self.result_count_label.configure(text=f"{len(articles)} results")

            except Exception as e:
                stats["errors"] += 1
                self.master.after(0, lambda: self._update_stats(**stats))
                self._log(f"PubMed search failed: {e}", "error")
                return

            self.master.after(0, lambda: self.progress.set(0.25))

            if not articles:
                self._log("No articles found matching your keywords. Try adjusting keywords or expanding the lookback period.", "warning")
                self.master.after(0, lambda: self.status_label.configure(text="No articles found"))
                return

            # Step 3: Initialize pipeline components
            self.master.after(0, lambda: self.status_label.configure(text="Scoring articles..."))
            self._log("Step 3/4: Scoring and summarizing articles...")

            llm = LLMClient(
                base_url=self.config.llm.base_url,
                api_key=self.config.llm.api_key,
                model=self.config.llm.model,
            )

            scorer = RelevanceScorer(llm)
            summarizer = Summarizer(llm)

            # Process articles
            saved_articles = []
            threshold = self.config.llm.relevance_threshold

            for i, article_data in enumerate(articles):
                pct = 0.4 + (0.5 * (i / len(articles)))
                self.master.after(0, lambda v=pct: self.progress.set(v))

                title = article_data.get("title", "Untitled")
                self._log(f"  [{i+1}/{len(articles)}] {title[:70]}...")

                # Check if previously rejected by user feedback
                if self.db.is_rejected(
                    doi=article_data.get("doi", ""),
                    pmid=article_data.get("pmid", ""),
                ):
                    stats["skipped"] += 1
                    self._log(f"    Previously rejected by user, skipping.")
                    self.master.after(0, lambda: self._update_stats(**stats))
                    continue

                # Check if already processed
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
                    # Fetch extra content if needed (e.g., full abstract from publisher)
                    if not article_data.get("abstract"):
                        fetched = fetcher.fetch_article(
                            url=article_data.get("url", ""),
                            title=title,
                            authors=article_data.get("authors", []),
                        )
                        if fetched.get("abstract"):
                            article_data["abstract"] = fetched["abstract"]
                        for key in ["journal", "volume", "issue", "date", "doi", "pmid", "authors"]:
                            if fetched.get(key) and not article_data.get(key):
                                article_data[key] = fetched[key]

                    # If still no abstract, mark it
                    if not article_data.get("abstract"):
                        self._log(f"    No abstract available, scoring from title only", "warning")
                        article_data["abstract"] = "[Abstract not available]"

                    # Score relevance
                    profile = self.config.profile.to_dict()
                    score, reason = scorer.score_article(profile, article_data)
                    article_data["relevance_score"] = score
                    article_data["relevance_reason"] = reason
                    stats["scored"] += 1
                    self.master.after(0, lambda: self._update_stats(**stats))

                    self._log(f"    Relevance score: {score}/10")

                    if scorer.should_save(score, threshold):
                        self._log(f"    Score {score} >= threshold {threshold}, generating summary...", "success")

                        # Generate summary
                        summary = summarizer.summarize(profile, article_data)
                        article_data["summary"] = summary.get("summary", "")
                        article_data["relevance_reason"] = summary.get("relevance_note", "")
                        article_data["key_points"] = summary.get("key_points", [])
                        article_data["tags"] = summary.get("tags", [])

                        # Save to results
                        self.db.mark_processed(article_data)

                        saved_articles.append(article_data)
                        stats["saved"] += 1
                        self._log(f"    Saved to results (score {score}/10)", "success")
                    else:
                        stats["skipped"] += 1
                        self._log(f"    Score {score} below threshold {threshold}, skipping.")

                    if self._stop_flag:
                        self._log("Pipeline stopped by user.", "warning")
                        break

                    self.master.after(0, lambda: self._update_stats(**stats))

                except Exception as e:
                    stats["errors"] += 1
                    self._log(f"    Error processing article: {e}", "error")
                    self.master.after(0, lambda: self._update_stats(**stats))

            self.master.after(0, lambda: self.progress.set(1.0))

            # Update last run
            self.config.last_run = datetime.now().isoformat()

            # Log summary
            self._log("", "info")
            self._log("=== Pipeline Complete ===", "success")
            self._log(f"  PubMed Searches: {stats['searched']}")
            self._log(f"  Articles found: {stats['articles']}")
            self._log(f"  Articles scored: {stats['scored']}")
            self._log(f"  Articles saved: {stats['saved']}")
            self._log(f"  Skipped (already processed or below threshold): {stats['skipped']}")
            if stats["errors"] > 0:
                self._log(f"  Errors: {stats['errors']}", "error")

            self.db.log_run(
                searches=stats["searched"],
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
            self.stop_btn.configure(state="disabled", text="Stop", fg_color="#F44336", hover_color="#D32F2F")
            self._log_queue.put(("", "__reset_button__"))

    def _validate_config(self) -> bool:
        """Validate that required configuration is set."""
        errors = []

        if not self.config.llm.model:
            errors.append("LLM model not configured")

        if not self.config.profile.keywords:
            errors.append("No keywords configured — add keywords to your profile")

        if not self.config.profile.research_description:
            self._log("Warning: Research description is empty - scoring may be less accurate", "warning")

        if errors:
            for err in errors:
                self._log(f"Config error: {err}", "error")
            return False

        return True