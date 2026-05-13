"""Results tab — shows all saved articles from the local database."""

import subprocess
import sys
from datetime import datetime

import customtkinter as ctk

from app.gui.widgets.scrollable_frame import ScrollableFrame
from app.utils.db import ArticleDatabase


def _open_url(url: str):
    """Open a URL in the default browser."""
    if not url:
        return
    if sys.platform == "darwin":
        subprocess.run(["open", url], check=False)
    else:
        import webbrowser
        webbrowser.open(url)


class ResultsTab:
    """Displays saved articles from SQLite, newest first."""

    def __init__(self, master, db: ArticleDatabase):
        self.master = master
        self.db = db
        self._last_count = -1  # tracks whether data has changed
        self._build_ui()
        # Do NOT refresh here — let AppWindow._on_tab_change handle it lazily

    def _build_ui(self):
        # Top bar
        bar = ctk.CTkFrame(self.master, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(
            bar,
            text="Saved Articles",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(side="left")

        self.count_label = ctk.CTkLabel(
            bar, text="", font=ctk.CTkFont(size=12), text_color="gray"
        )
        self.count_label.pack(side="left", padx=(12, 0))

        ctk.CTkButton(
            bar,
            text="Refresh",
            width=80,
            height=28,
            command=self.refresh,
        ).pack(side="right")

        # Clear All button
        ctk.CTkButton(
            bar,
            text="Clear All",
            width=80,
            height=28,
            fg_color=("gray75", "gray30"),
            hover_color=("gray65", "gray40"),
            text_color=("black", "white"),
            command=self._confirm_clear_all,
        ).pack(side="right", padx=(4, 0))

        # Scrollable article list
        self.scroll = ScrollableFrame(self.master)
        self.scroll.pack(fill="both", expand=True)

    def refresh(self, force=False):
        """Reload articles from DB and redraw. Skips if nothing changed unless force=True."""
        articles = self.db.get_all_processed(limit=200)
        if not force and len(articles) == self._last_count:
            return  # nothing changed, skip expensive rebuild
        self._last_count = len(articles)

        for w in self.scroll.winfo_children():
            w.destroy()

        self.count_label.configure(text=f"{len(articles)} article{'s' if len(articles) != 1 else ''}")

        if not articles:
            ctk.CTkLabel(
                self.scroll,
                text="No articles saved yet. Run the pipeline to find relevant papers.",
                font=ctk.CTkFont(size=13),
                text_color="gray",
                wraplength=600,
            ).pack(pady=40)
            return

        for article in articles:
            self._add_card(article)

    def _confirm_clear_all(self):
        """Show a confirmation dialog before clearing all articles."""
        count = self._last_count if self._last_count >= 0 else 0

        dialog = ctk.CTkToplevel(self.master)
        dialog.title("Clear All Articles")
        dialog.geometry("400x140")
        dialog.transient(self.master)
        dialog.grab_set()

        ctk.CTkLabel(
            dialog,
            text=f"Delete all {count} saved articles?\nThis cannot be undone.",
            font=ctk.CTkFont(size=13),
            wraplength=360,
        ).pack(pady=(15, 10))

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=(0, 15))

        def _do_clear():
            self.db.clear_all()
            self._last_count = -1  # force next refresh to rebuild
            self.refresh(force=True)
            dialog.destroy()

        ctk.CTkButton(btn_frame, text="Clear All", width=100,
                       fg_color=("#F44336", "#D32F2F"),
                       hover_color=("#E53935", "#C62828"),
                       command=_do_clear).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="Cancel", width=100,
                       command=dialog.destroy).pack(side="left", padx=10)

    def _delete_article(self, article_id: int):
        """Delete a saved article from the database."""
        if not article_id:
            return
        try:
            self.db.delete_article(article_id)
            self.refresh()
        except Exception as e:
            print(f"Error deleting article {article_id}: {e}")

    def _set_feedback(self, article_id: int, feedback: str,
                      like_btn, dislike_btn):
        """Record user feedback and update button visuals.
        Clicking the same icon again toggles it off (undo)."""
        current = self.db.get_feedback(article_id)
        # Toggle off if clicking the same feedback that's already set
        if current == feedback:
            feedback = ""

        self.db.set_feedback(article_id, feedback)
        if feedback == "relevant":
            like_btn.configure(
                fg_color=("#4CAF50", "#2E7D32"),
                hover_color=("#388E3C", "#1B5E20"),
            )
            dislike_btn.configure(
                fg_color=("gray75", "gray30"),
                hover_color=("#D32F2F", "#B71C1C"),
            )
        elif feedback == "not_relevant":
            dislike_btn.configure(
                fg_color=("#F44336", "#C62828"),
                hover_color=("#D32F2F", "#B71C1C"),
            )
            like_btn.configure(
                fg_color=("gray75", "gray30"),
                hover_color=("#388E3C", "#1B5E20"),
            )
        else:
            # Cleared — both buttons back to default
            like_btn.configure(
                fg_color=("gray75", "gray30"),
                hover_color=("#388E3C", "#1B5E20"),
            )
            dislike_btn.configure(
                fg_color=("gray75", "gray30"),
                hover_color=("#D32F2F", "#B71C1C"),
            )

    def _add_card(self, article: dict):
        """Add a single article card to the scroll frame."""
        card = ctk.CTkFrame(self.scroll, corner_radius=8)
        card.pack(fill="x", pady=(0, 8), padx=2)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=10)

        # Title row
        title_row = ctk.CTkFrame(inner, fg_color="transparent")
        title_row.pack(fill="x")

        score = article.get("relevance_score") or 0
        score_color = "#4CAF50" if score >= 7 else "#FF9800" if score >= 4 else "gray"
        ctk.CTkLabel(
            title_row,
            text=f"{score}/10",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=score_color,
            width=40,
        ).pack(side="left", padx=(0, 8))

        # Feedback badge
        fb = article.get("feedback", "")
        if fb:
            fb_text = "Relevant" if fb == "relevant" else "Not relevant"
            fb_color = "#4CAF50" if fb == "relevant" else "#F44336"
            ctk.CTkLabel(
                title_row,
                text=fb_text,
                font=ctk.CTkFont(size=10),
                text_color=fb_color,
            ).pack(side="left", padx=(4, 0))

        title = article.get("title") or "Untitled"
        ctk.CTkLabel(
            title_row,
            text=title,
            font=ctk.CTkFont(size=13, weight="bold"),
            wraplength=500,
            justify="left",
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

        # Metadata row
        meta_parts = []
        if article.get("journal"):
            meta_parts.append(article["journal"])
        if article.get("authors"):
            authors = article["authors"]
            # Truncate long author lists
            if len(authors) > 60:
                authors = authors[:57] + "..."
            meta_parts.append(authors)
        if article.get("processed_at"):
            try:
                dt = datetime.fromisoformat(article["processed_at"])
                meta_parts.append(dt.strftime("%Y-%m-%d"))
            except ValueError:
                pass

        if meta_parts:
            ctk.CTkLabel(
                inner,
                text="  •  ".join(meta_parts),
                font=ctk.CTkFont(size=11),
                text_color="gray",
                wraplength=620,
                justify="left",
                anchor="w",
            ).pack(fill="x", pady=(2, 0))

        # Summary
        summary = article.get("summary", "").strip()
        if summary:
            ctk.CTkLabel(
                inner,
                text=summary,
                font=ctk.CTkFont(size=12),
                wraplength=620,
                justify="left",
                anchor="w",
            ).pack(fill="x", pady=(6, 0))

        # Relevance reason
        reason = article.get("relevance_reason", "").strip()
        if reason:
            ctk.CTkLabel(
                inner,
                text=f"Why relevant: {reason}",
                font=ctk.CTkFont(size=11),
                text_color="gray",
                wraplength=620,
                justify="left",
                anchor="w",
            ).pack(fill="x", pady=(4, 0))

        # Tags + open link
        bottom = ctk.CTkFrame(inner, fg_color="transparent")
        bottom.pack(fill="x", pady=(6, 0))

        # Feedback buttons (Phase 2) — packed first so long tags don't push them off-screen
        article_id = article.get("id")
        current_feedback = article.get("feedback", "")

        like_btn = ctk.CTkButton(
            bottom, text="👍", width=36, height=22,
            font=ctk.CTkFont(size=12),
            fg_color=("#4CAF50", "#2E7D32") if current_feedback == "relevant" else ("gray75", "gray30"),
            hover_color=("#388E3C", "#1B5E20"),
            command=lambda aid=article_id: self._set_feedback(aid, "relevant", like_btn, dislike_btn),
        )
        like_btn.pack(side="left", padx=(0, 2))

        dislike_btn = ctk.CTkButton(
            bottom, text="👎", width=36, height=22,
            font=ctk.CTkFont(size=12),
            fg_color=("#F44336", "#C62828") if current_feedback == "not_relevant" else ("gray75", "gray30"),
            hover_color=("#D32F2F", "#B71C1C"),
            command=lambda aid=article_id: self._set_feedback(aid, "not_relevant", like_btn, dislike_btn),
        )
        dislike_btn.pack(side="left", padx=(2, 4))

        tags_str = article.get("tags", "")
        if tags_str:
            # Truncate long tag strings so they don't overflow the row
            display_tags = tags_str if len(tags_str) <= 80 else tags_str[:77] + "…"
            ctk.CTkLabel(
                bottom,
                text=display_tags,
                font=ctk.CTkFont(size=10),
                text_color="gray",
            ).pack(side="left", padx=(4, 0))

        url = article.get("url", "")
        if url:
            ctk.CTkButton(
                bottom,
                text="Open",
                width=60,
                height=22,
                font=ctk.CTkFont(size=11),
                command=lambda u=url: _open_url(u),
            ).pack(side="right")

        doi = article.get("doi", "")
        if doi and not doi.startswith("PPI:"):
            doi_url = f"https://doi.org/{doi}"
            ctk.CTkButton(
                bottom,
                text="DOI",
                width=50,
                height=22,
                font=ctk.CTkFont(size=11),
                fg_color=("gray75", "gray30"),
                hover_color=("gray65", "gray40"),
                text_color=("black", "white"),
                command=lambda u=doi_url: _open_url(u),
            ).pack(side="right", padx=(0, 4))

        # Edit + Delete buttons
        edit_btn = ctk.CTkButton(
            bottom,
            text="Edit",
            width=55,
            height=22,
            font=ctk.CTkFont(size=10),
            fg_color=("gray75", "gray30"),
            hover_color=("gray65", "gray40"),
            text_color=("black", "white"),
            command=lambda: self._edit_article(article),
        )
        edit_btn.pack(side="right", padx=(2, 4))

        article_id = article.get("id")
        ctk.CTkButton(
            bottom,
            text="Delete",
            width=55,
            height=22,
            font=ctk.CTkFont(size=10),
            fg_color=("gray75", "gray30"),
            hover_color=("gray65", "gray40"),
            text_color=("black", "white"),
            command=lambda aid=article_id: self._delete_article(aid),
        ).pack(side="right", padx=(2, 4))

    def _edit_article(self, article: dict):
        """Open a dialog to edit article tags, score, and summary."""
        dialog = ctk.CTkToplevel(self.master)
        dialog.title("Edit Article")
        dialog.geometry("600x500")
        dialog.transient(self.master)
        dialog.grab_set()

        # Title (read-only)
        ctk.CTkLabel(
            dialog,
            text=article.get("title", "Untitled"),
            font=ctk.CTkFont(size=14, weight="bold"),
            wraplength=560,
            justify="left",
        ).pack(anchor="w", padx=15, pady=(15, 10))

        # Score field
        score_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        score_frame.pack(fill="x", padx=15, pady=(0, 10))
        ctk.CTkLabel(score_frame, text="Relevance Score (1-10):", font=ctk.CTkFont(size=12)).pack(anchor="w")
        score_var = ctk.IntVar(value=article.get("relevance_score", 0))
        ctk.CTkSlider(
            score_frame, from_=1, to=10, number_of_steps=9,
            variable=score_var, width=200,
        ).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(score_frame, textvariable=score_var, font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")

        # Summary field
        ctk.CTkLabel(dialog, text="Summary:", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=15, pady=(5, 2))
        summary_text = ctk.CTkTextbox(dialog, height=80, wrap="word")
        summary_text.pack(fill="x", padx=15, pady=(0, 5))
        summary_text.insert("1.0", article.get("summary", ""))

        # Tags field
        ctk.CTkLabel(dialog, text="Tags (comma-separated):", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=15, pady=(5, 2))
        tags_entry = ctk.CTkEntry(dialog)
        tags_entry.pack(fill="x", padx=15, pady=(0, 5))
        tags_entry.insert(0, article.get("tags", ""))

        # Reason field
        ctk.CTkLabel(dialog, text="Relevance Reason:", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=15, pady=(5, 2))
        reason_text = ctk.CTkTextbox(dialog, height=60, wrap="word")
        reason_text.pack(fill="x", padx=15, pady=(0, 5))
        reason_text.insert("1.0", article.get("relevance_reason", ""))

        # Include/Exclude checkboxes
        include_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(dialog, text="Include in results", variable=include_var).pack(anchor="w", padx=15, pady=(5, 0))

        # Feedback selector
        feedback_var = ctk.StringVar(value=article.get("feedback", ""))
        ctk.CTkLabel(dialog, text="Feedback:", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=15, pady=(5, 2))
        feedback_combo = ctk.CTkComboBox(
            dialog, width=200,
            values=["", "relevant", "not_relevant"],
            variable=feedback_var,
        )
        feedback_combo.pack(anchor="w", padx=15, pady=(0, 5))

        def save_and_close() -> None:
            """Save edited values back to the database."""
            try:
                self.db.update_article(
                    article_id=article.get("id"),
                    relevance_score=score_var.get(),
                    summary=summary_text.get("1.0", "end").strip(),
                    tags=tags_entry.get(),
                    relevance_reason=reason_text.get("1.0", "end").strip(),
                    include=include_var.get(),
                    feedback=feedback_var.get(),
                )
                dialog.destroy()
                self.refresh()
            except Exception as e:
                ctk.CTkLabel(dialog, text=f"Error: {e}", text_color="red").pack()

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=(10, 15))
        ctk.CTkButton(btn_frame, text="Save", width=100, command=save_and_close).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="Cancel", width=100, command=dialog.destroy).pack(side="left", padx=10)