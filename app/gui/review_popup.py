"""Tinder-style one-card-at-a-time review popup for post-pipeline article review."""

import logging
import traceback
from typing import List, Optional

import customtkinter as ctk

from app.utils.db import ArticleDatabase

logger = logging.getLogger(__name__)


def _block_edits(textbox: ctk.CTkTextbox) -> None:
    """Read-only textbox that still allows text selection and copy."""
    def _guard(event):
        if event.state & 0xF:
            return
        if event.keysym in ("Left", "Right", "Up", "Down", "Home", "End",
                             "Prior", "Next", "Shift_L", "Shift_R",
                             "Control_L", "Control_R", "Meta_L", "Meta_R"):
            return
        return "break"
    textbox._textbox.bind("<Key>", _guard, add="+")

# Highlight colour for keyword matches in abstract
KEYWORD_HIGHLIGHT = "#FFB74D"  # warm orange


class ReviewPopup:
    """Post-pipeline review: show saved articles one at a time with Accept / Reject / Skip."""

    def __init__(self, master, db: ArticleDatabase,
                 saved_articles: List[dict],
                 profile_keywords: List[str],
                 run_keywords: List[str],
                 must_include_keywords: Optional[List[str]] = None):
        """
        Args:
            master: parent CTk widget
            db: ArticleDatabase instance
            saved_articles: list of article dicts saved in this run
            profile_keywords: user profile keywords for highlighting
            run_keywords: keywords used in current run (OR context for soft blacklist)
            must_include_keywords: AND/must-include terms (hard context for soft blacklist)
        """
        self.master = master
        self.db = db
        self.articles = saved_articles
        self._or_keywords = list(run_keywords or [])
        self._and_keywords = list(must_include_keywords or [])
        self.all_keywords = list(set(
            kw.strip().lower()
            for kw in (profile_keywords + run_keywords)
            if kw.strip()
        ))

        self._idx = 0
        self._total = len(self.articles)
        self._reviewed_ids: set[int] = set()
        self._rejected_ids: set[int] = set()
        self._closing = False  # guard against double-close

        logger.info("ReviewPopup: opening with %d articles", self._total)

        if self._total == 0:
            return

        self.root = ctk.CTkToplevel(master)
        self.root.title(f"PaperMatcher — Review ({self._total} articles)")
        self.root.geometry("780x820")
        self.root.minsize(700, 680)
        self.root.transient(master)

        # Prevent close-button dismiss — must Accept/Reject/Skip
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._show_card()

        # CTkToplevel starts withdrawn and uses an internal after() to deiconify.
        # We must wait for that to fire before doing anything with the window.
        self.root.after(200, self._raise_window)

    def _raise_window(self):
        """Force the popup to the front after CTkToplevel has finished its own setup."""
        logger.info("ReviewPopup: raising window")
        try:
            # Flush parent's event queue so CTkToplevel's deferred deiconify fires.
            self.master.update()
            # Explicitly show in case the window is still in withdrawn state.
            self.root.deiconify()
            self.root.update()
            # macOS focus-stealing prevention: -topmost forces the window in front.
            self.root.attributes("-topmost", True)
            self.root.lift()
            self.root.focus_force()
            # grab_set keeps focus in this window (same pattern as onboarding).
            self.root.grab_set()
            # Remove -topmost after 300ms so it doesn't stay above other apps.
            self.root.after(300, self._clear_topmost)
        except Exception as e:
            logger.warning("Failed to raise review popup: %s", e)

    def _clear_topmost(self):
        try:
            self.root.attributes("-topmost", False)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.main_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        self.main_frame.pack(fill="both", expand=True, padx=16, pady=16)

        # Progress bar
        progress_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        progress_frame.pack(fill="x", pady=(0, 8))

        self.progress = ctk.CTkProgressBar(progress_frame, height=6)
        self.progress.pack(fill="x")
        self.progress.set(0)

        self.progress_label = ctk.CTkLabel(
            progress_frame, text="",
            font=ctk.CTkFont(size=11), text_color="gray"
        )
        self.progress_label.pack(anchor="e", pady=(2, 0))

        # Card frame
        self.card_frame = ctk.CTkFrame(self.main_frame, corner_radius=10)
        self.card_frame.pack(fill="both", expand=True, pady=(0, 12))

        # Title
        self.title_label = ctk.CTkLabel(
            self.card_frame, text="",
            font=ctk.CTkFont(size=16, weight="bold"),
            wraplength=720, justify="left", anchor="w"
        )
        self.title_label.pack(anchor="w", padx=16, pady=(14, 4))

        # Metadata
        self.meta_label = ctk.CTkLabel(
            self.card_frame, text="",
            font=ctk.CTkFont(size=12), text_color="gray"
        )
        self.meta_label.pack(anchor="w", padx=16, pady=(0, 6))

        # Score badge
        self.score_label = ctk.CTkLabel(
            self.card_frame, text="",
            font=ctk.CTkFont(size=12, weight="bold"), width=45,
            anchor="e"
        )
        self.score_label.pack(anchor="ne", padx=16, pady=(10, 2))

        # Separator
        sep = ctk.CTkFrame(self.card_frame, height=1, fg_color="gray30")
        sep.pack(fill="x", padx=16, pady=(4, 8))

        # Abstract (scrollable)
        abstract_frame = ctk.CTkFrame(self.card_frame, fg_color="transparent")
        abstract_frame.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        self.abstract_text = ctk.CTkTextbox(
            abstract_frame, wrap="word",
            font=ctk.CTkFont(size=12),
            height=320
        )
        self.abstract_text.pack(fill="both", expand=True)
        _block_edits(self.abstract_text)

        # Bottom bar with action buttons
        btn_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        btn_frame.pack(fill="x", pady=(4, 0))

        # Reject / Skip / Accept  — order: reject left, accept right
        self.reject_btn = ctk.CTkButton(
            btn_frame, text="👎  Reject", width=130, height=38,
            fg_color=("#F44336", "#D32F2F"),
            hover_color=("#E53935", "#C62828"),
            text_color=("white", "white"),
            font=ctk.CTkFont(size=13, weight="bold"),
            command=lambda: self._review("rejected")
        )
        self.reject_btn.pack(side="left", padx=(0, 8))

        self.skip_btn = ctk.CTkButton(
            btn_frame, text="⏭  Skip", width=100, height=38,
            fg_color=("gray75", "gray30"),
            hover_color=("gray65", "gray40"),
            font=ctk.CTkFont(size=13),
            command=lambda: self._review("skipped")
        )
        self.skip_btn.pack(side="left", padx=8)

        self.accept_btn = ctk.CTkButton(
            btn_frame, text="👍  Accept", width=130, height=38,
            fg_color=("#4CAF50", "#2E7D32"),
            hover_color=("#388E3C", "#1B5E20"),
            text_color=("white", "white"),
            font=ctk.CTkFont(size=13, weight="bold"),
            command=lambda: self._review("accepted")
        )
        self.accept_btn.pack(side="right", padx=(8, 0))

        # Keyboard shortcuts
        self.root.bind("<Left>", lambda e: self._review("rejected"))
        self.root.bind("<Down>", lambda e: self._review("skipped"))
        self.root.bind("<Right>", lambda e: self._review("accepted"))

    # ------------------------------------------------------------------
    # Card display
    # ------------------------------------------------------------------

    def _show_card(self):
        if self._idx >= self._total:
            self._finish()
            return

        article = self.articles[self._idx]
        progress = (self._idx + 1) / self._total
        self.progress.set(progress)
        self.progress_label.configure(text=f"{self._idx + 1}  /  {self._total}")

        title = article.get("title", "Untitled")
        self.title_label.configure(text=title)

        journal = article.get("journal", "")
        date = article.get("date", "")[:4] if article.get("date") else ""  # year only
        score = article.get("relevance_score", 0)
        try:
            score = int(score)
        except (TypeError, ValueError):
            score = 0
        pmid = article.get("pmid", "")

        # Authors: first author et al.
        authors_raw = article.get("authors", "")
        author_str = ""
        if authors_raw:
            author_list = [a.strip() for a in str(authors_raw).split(",") if a.strip()]
            if len(author_list) == 1:
                author_str = author_list[0]
            elif len(author_list) == 2:
                author_str = f"{author_list[0]}, {author_list[1]}"
            elif len(author_list) > 2:
                author_str = f"{author_list[0]} et al."

        meta_parts = [p for p in [author_str, journal, date, f"PMID {pmid}" if pmid else ""] if p]
        self.meta_label.configure(text="  •  ".join(meta_parts))

        score_color = "#4CAF50" if score >= 7 else "#FF9800" if score >= 4 else "gray"
        self.score_label.configure(
            text=f"{score}/10",
            text_color=score_color
        )

        self.abstract_text.delete("1.0", "end")

        summary = article.get("summary", "").strip()
        if summary:
            self.abstract_text.insert("end", "Summary\n", "section_header")
            self.abstract_text.insert("end", summary + "\n\n")

        self.abstract_text.insert("end", "Abstract\n", "section_header")
        abstract = article.get("abstract", "[Abstract not available]")
        self.abstract_text.insert("end", abstract + "\n")

        reason = article.get("relevance_reason", "").strip()
        if reason:
            self.abstract_text.insert("end", "\nWhy this article?\n", "section_header")
            self.abstract_text.insert("end", reason + "\n")

        # Extended analysis fields
        implications = article.get("implications", "").strip()
        if implications:
            self.abstract_text.insert("end", "\nImplications\n", "section_header")
            self.abstract_text.insert("end", implications + "\n")

        methodology = article.get("methodology", "").strip()
        conflict = article.get("conflict_bias", "").strip()
        reproducibility = article.get("reproducibility", "").strip()
        if any([methodology, conflict, reproducibility]):
            self.abstract_text.insert("end", "\nStudy Details\n", "section_header")
            if methodology:
                self.abstract_text.insert("end", f"Methodology: {methodology}\n")
            if reproducibility:
                self.abstract_text.insert("end", f"Reproducibility: {reproducibility}\n")
            if conflict and conflict.upper() != "NONE DETECTED":
                self.abstract_text.insert("end", f"Conflict/Bias: {conflict}\n")

        self.abstract_text.tag_config("section_header", foreground="#4FC3F7")
        self._apply_highlights()

        # Focus accept button for fast review
        self.accept_btn.focus_force()

    def _apply_highlights(self):
        """Highlight keywords in the abstract section only.

        Uses the section_header tag ranges to locate the Abstract section
        boundaries — avoids position-tracking timing issues with CTkTextbox.
        """
        if not self.all_keywords:
            return

        # Find abstract content bounds from section_header tag ranges
        search_start = "1.0"
        search_stop  = "end"
        try:
            tb = self.abstract_text._textbox
            ranges = tb.tag_ranges("section_header")
            for i in range(0, len(ranges), 2):
                hdr_start = str(ranges[i])
                hdr_end   = str(ranges[i + 1])
                if tb.get(hdr_start, hdr_end).strip() == "Abstract":
                    search_start = hdr_end
                    search_stop  = str(ranges[i + 2]) if i + 2 < len(ranges) else "end"
                    break
        except Exception:
            pass  # fall back to full-text search

        self.abstract_text.tag_config(
            "highlight",
            background=KEYWORD_HIGHLIGHT,
            foreground="black",
        )
        full_text = self.abstract_text.get("1.0", "end")

        for kw in self.all_keywords:
            if len(kw) < 2:
                continue
            start = search_start
            while True:
                search_pos = self.abstract_text.search(
                    kw, start, stopindex=search_stop, nocase=True
                )
                if not search_pos:
                    break
                line, col = search_pos.split(".")
                idx = len(self.abstract_text.get("1.0", f"{line}.{col}"))
                before = full_text[idx - 1] if idx > 0 else " "
                after_idx = idx + len(kw)
                after = full_text[after_idx] if after_idx < len(full_text) else " "
                if not (before.isalnum() or before in "_-") and not (after.isalnum() or after in "_-"):
                    end_pos = f"{search_pos}+{len(kw)}c"
                    self.abstract_text.tag_add("highlight", search_pos, end_pos)
                start = f"{search_pos}+{len(kw)}c"

    # ------------------------------------------------------------------
    # Review actions
    # ------------------------------------------------------------------

    def _review(self, action: str):
        """Handle Accept / Reject / Skip for current article."""
        if self._idx >= self._total:
            return
        article = self.articles[self._idx]
        article_id = article.get("id")
        logger.info("ReviewPopup: %s article %d/%d id=%s title=%s",
                    action, self._idx + 1, self._total,
                    article_id, article.get("title", "?")[:60])

        try:
            if article_id:
                if action == "accepted":
                    self.db.update_article(article_id, feedback="accepted")
                elif action == "rejected":
                    self.db.reject_article(
                        article_id,
                        or_keywords=self._or_keywords,
                        and_keywords=self._and_keywords,
                    )
                    self._rejected_ids.add(self._idx)
        except Exception:
            logger.error("ReviewPopup: DB error on %s:\n%s", action, traceback.format_exc())

        self._reviewed_ids.add(self._idx)
        self._idx += 1
        try:
            self._show_card()
        except Exception:
            logger.error("ReviewPopup: _show_card error:\n%s", traceback.format_exc())

    def _finish(self):
        """Close popup when all articles reviewed."""
        n_reviewed = len(self._reviewed_ids)
        n_rejected = len(self._rejected_ids)
        n_kept = n_reviewed - n_rejected  # kept = reviewed - rejected (includes skipped)

        self.title_label.configure(text="✅ Review complete!")
        self.meta_label.configure(text="")
        self.score_label.configure(text="")

        self.abstract_text.delete("1.0", "end")
        self.abstract_text.insert(
            "1.0",
            f"Reviewed {n_reviewed} articles:\n"
            f"  ✓  Kept (accepted + skipped): {n_kept}\n"
            f"  ✗  Rejected: {n_rejected}\n\n"
            f"Rejected articles are stored with search context and will be\n"
            f"skipped in future runs using similar keywords.\n\n"
            f"Skipped articles remain in the database and will appear\n"
            f"in future searches."
        )

        # Hide review buttons, show Close only
        self.reject_btn.pack_forget()
        self.skip_btn.pack_forget()
        self.accept_btn.configure(
            text="Close",
            fg_color=("gray75", "gray30"),
            hover_color=("gray65", "gray40"),
            command=self._on_close
        )
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Unbind review shortcuts; map Enter to Close for easy dismissal
        self.root.unbind("<Left>")
        self.root.unbind("<Down>")
        self.root.unbind("<Right>")
        self.root.bind("<Return>", lambda e: self._on_close())
        # Also bind Return on the textbox itself — _block_edits intercepts it otherwise
        self.abstract_text._textbox.bind("<Return>", lambda e: self._on_close())
        # Clicking anywhere outside the textbox returns focus to root so Enter works
        self.main_frame.bind("<Button-1>", lambda e: self.root.focus_set())
        self.root.focus_set()

    def _on_close(self):
        """Close the popup, releasing grab so the main window gets focus back."""
        if self._closing:
            logger.debug("ReviewPopup: _on_close called again — ignoring (already closing)")
            return
        self._closing = True
        logger.info("ReviewPopup: closing (reviewed=%d rejected=%d)",
                    len(self._reviewed_ids), len(self._rejected_ids))

        # Release grab first so the main window can accept events
        try:
            self.root.grab_release()
            logger.debug("ReviewPopup: grab_release OK")
        except Exception:
            logger.warning("ReviewPopup: grab_release failed:\n%s", traceback.format_exc())

        # Delay destroy by one event-loop cycle to let grab_release fully propagate.
        # Calling destroy() immediately after grab_release() can cause a race where
        # tkinter tries to restore focus to a widget that's mid-destruction (segfault).
        try:
            self.root.after(50, self._destroy_root)
        except Exception:
            logger.warning("ReviewPopup: could not schedule destroy, calling directly:\n%s",
                           traceback.format_exc())
            self._destroy_root()

    def _destroy_root(self):
        """Actually destroy the window — called via after() to avoid race with grab_release."""
        try:
            self.root.destroy()
            logger.info("ReviewPopup: destroyed OK")
        except Exception:
            logger.warning("ReviewPopup: root.destroy failed:\n%s", traceback.format_exc())