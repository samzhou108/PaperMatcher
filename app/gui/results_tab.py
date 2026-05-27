"""Results tab — shows all saved articles from the local database."""

import csv
import subprocess
import sys
from datetime import datetime
from tkinter import filedialog

import customtkinter as ctk

from app.gui.widgets.scrollable_frame import ScrollableFrame
from app.gui.widgets.pill_frame import PillFrame
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

    PAGE_SIZE = 20

    def __init__(self, master, db: ArticleDatabase):
        self.master = master
        self.db = db
        self._last_count = -1  # tracks whether data has changed
        self._page = 0
        self._articles: list = []
        self._expanded: set = set()  # track expanded article IDs
        self._article_map: dict = {}  # id → article dict, avoids DB round-trips on expand
        self._detail_frames: dict = {}  # id → detail CTkFrame widget reference
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

        ctk.CTkButton(
            bar,
            text="Export CSV",
            width=90,
            height=28,
            fg_color=("gray75", "gray30"),
            hover_color=("gray65", "gray40"),
            text_color=("black", "white"),
            command=self._export_csv,
        ).pack(side="right", padx=(4, 0))

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

        # Pagination nav bar (hidden until there are multiple pages)
        self._nav_bar = ctk.CTkFrame(self.master, fg_color="transparent")
        self._nav_bar.pack(fill="x", pady=(4, 0))

        self._prev_btn = ctk.CTkButton(
            self._nav_bar, text="← Prev", width=80, height=28,
            command=self._prev_page,
        )
        self._prev_btn.pack(side="left")

        self._page_label = ctk.CTkLabel(
            self._nav_bar, text="", font=ctk.CTkFont(size=12), width=140
        )
        self._page_label.pack(side="left", padx=12)

        self._next_btn = ctk.CTkButton(
            self._nav_bar, text="Next →", width=80, height=28,
            command=self._next_page,
        )
        self._next_btn.pack(side="left")

    def refresh(self, force=False):
        """Reload articles from DB and redraw current page.

        Skips the DB fetch (but still redraws) if nothing changed, unless force=True.
        """
        articles = self.db.get_all_processed(limit=5000)
        if not force and len(articles) == self._last_count:
            return
        self._last_count = len(articles)
        self._articles = articles
        # Reset to page 0 only on a full reload, not on page navigation
        if force:
            self._page = 0
        self._render_page()

    def _render_page(self):
        """Render only the articles for the current page."""
        for w in self.scroll.winfo_children():
            w.destroy()
        self._detail_frames.clear()

        total = len(self._articles)
        n_pages = max(1, (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self._page = max(0, min(self._page, n_pages - 1))

        self.count_label.configure(
            text=f"{total} article{'s' if total != 1 else ''}"
        )

        if not self._articles:
            ctk.CTkLabel(
                self.scroll,
                text="No articles saved yet. Run the pipeline to find relevant papers.",
                font=ctk.CTkFont(size=13),
                text_color="gray",
                wraplength=600,
            ).pack(pady=40)
            self._nav_bar.pack_forget()
            return

        start = self._page * self.PAGE_SIZE
        end = min(start + self.PAGE_SIZE, total)
        self._article_map = {a.get("id"): a for a in self._articles}
        for article in self._articles[start:end]:
            self._add_card(article)

        # Re-bind scroll events so newly added cards respond to trackpad scrolling
        self.scroll.refresh_scroll_bindings()

        # Update nav bar
        if n_pages > 1:
            self._nav_bar.pack(fill="x", pady=(4, 0))
            self._page_label.configure(
                text=f"Page {self._page + 1} / {n_pages}  ({start + 1}–{end})"
            )
            self._prev_btn.configure(state="normal" if self._page > 0 else "disabled")
            self._next_btn.configure(state="normal" if self._page < n_pages - 1 else "disabled")
        else:
            self._nav_bar.pack_forget()

    def _prev_page(self):
        self._page -= 1
        self._render_page()

    def _next_page(self):
        self._page += 1
        self._render_page()

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
            self._last_count = -1
            self._articles = []
            self._page = 0
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
            self._last_count = -1  # force DB re-fetch on next refresh
            self.refresh(force=True)
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

    def _toggle_expand(self, article_id: int, card, indicator_label):
        """Toggle expanded state. card is the outer CTkFrame so detail drops below the row."""
        if article_id in self._expanded:
            self._expanded.remove(article_id)
            indicator_label.configure(text="▶")
            frame = self._detail_frames.pop(article_id, None)
            if frame:
                frame.destroy()
        else:
            self._expanded.add(article_id)
            indicator_label.configure(text="▼")
            self._build_detail_frame(article_id, card)

    def _build_detail_frame(self, article_id: int, card):
        """Build and pack detail frame inside card (vertical), so it appears below the row."""
        # Destroy any stale frame first (safety)
        old = self._detail_frames.pop(article_id, None)
        if old:
            old.destroy()

        article = self._article_map.get(article_id)
        if not article:
            return

        detail_frame = ctk.CTkFrame(card, fg_color=("gray90", "gray20"), corner_radius=6)
        detail_frame.pack(fill="x", padx=8, pady=(0, 8))
        self._detail_frames[article_id] = detail_frame

        # Summary
        summary = article.get("summary", "").strip()
        if summary:
            ctk.CTkLabel(
                detail_frame,
                text="Summary",
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color="gray",
            ).pack(anchor="w", padx=12, pady=(4, 0))
            ctk.CTkLabel(
                detail_frame,
                text=summary,
                font=ctk.CTkFont(size=12),
                wraplength=580,
                anchor="w",
            ).pack(anchor="w", padx=12, pady=(0, 4))

        # Implications
        implications = article.get("implications", "").strip()
        if implications:
            ctk.CTkLabel(
                detail_frame,
                text="Implications",
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color="gray",
            ).pack(anchor="w", padx=12, pady=(4, 0))
            ctk.CTkLabel(
                detail_frame,
                text=implications,
                font=ctk.CTkFont(size=11),
                wraplength=580,
                anchor="w",
            ).pack(anchor="w", padx=12, pady=(0, 4))

        # Study details row
        methodology = article.get("methodology", "")
        reproducibility = article.get("reproducibility", "")
        conflict_bias = article.get("conflict_bias", "")
        has_study_details = methodology or reproducibility or (conflict_bias and conflict_bias != "NONE DETECTED")
        if has_study_details:
            ctk.CTkLabel(
                detail_frame,
                text="Methodology:",
                font=ctk.CTkFont(size=11),
                text_color="gray",
            ).pack(anchor="w", padx=12, pady=(4, 0))
            if methodology:
                ctk.CTkLabel(
                    detail_frame,
                    text=methodology,
                    font=ctk.CTkFont(size=11),
                    anchor="w",
                ).pack(anchor="w", padx=12, pady=(0, 2))
            if reproducibility:
                ctk.CTkLabel(
                    detail_frame,
                    text="Reproducibility:",
                    font=ctk.CTkFont(size=11),
                    text_color="gray",
                ).pack(anchor="w", padx=12, pady=(2, 0))
                ctk.CTkLabel(
                    detail_frame,
                    text=reproducibility,
                    font=ctk.CTkFont(size=11),
                    anchor="w",
                ).pack(anchor="w", padx=12, pady=(0, 2))
            if conflict_bias and conflict_bias != "NONE DETECTED":
                ctk.CTkLabel(
                    detail_frame,
                    text="Conflict/Bias:",
                    font=ctk.CTkFont(size=11),
                    text_color="#FF9800",
                ).pack(anchor="w", padx=12, pady=(2, 0))
                ctk.CTkLabel(
                    detail_frame,
                    text=conflict_bias,
                    font=ctk.CTkFont(size=11),
                    anchor="w",
                ).pack(anchor="w", padx=12, pady=(0, 2))

        # Relevance reason
        reason = article.get("relevance_reason", "").strip()
        if reason:
            ctk.CTkLabel(
                detail_frame,
                text="Why relevant:",
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color="gray",
            ).pack(anchor="w", padx=12, pady=(4, 0))
            ctk.CTkLabel(
                detail_frame,
                text=reason,
                font=ctk.CTkFont(size=11),
                wraplength=580,
                anchor="w",
            ).pack(anchor="w", padx=12, pady=(0, 4))

        # Tags — displayed as pills
        tags_str = article.get("tags", "").strip()
        if tags_str:
            tag_list = [t.strip() for t in tags_str.split(",") if t.strip()]
            if tag_list:
                ctk.CTkLabel(
                    detail_frame,
                    text="Tags:",
                    font=ctk.CTkFont(size=11, weight="bold"),
                    text_color="gray",
                ).pack(anchor="w", padx=12, pady=(4, 0))
                PillFrame(
                    detail_frame,
                    items=tag_list,
                    read_only=True,
                ).pack(anchor="w", padx=12, pady=(2, 6))

        # Action buttons row
        btn_row = ctk.CTkFrame(detail_frame, fg_color="transparent")
        btn_row.pack(fill="x", pady=(6, 8))

        ctk.CTkButton(
            btn_row,
            text="Copy",
            width=55,
            height=22,
            font=ctk.CTkFont(size=10),
            fg_color=("gray75", "gray30"),
            hover_color=("gray65", "gray40"),
            text_color=("black", "white"),
            command=lambda a=article: self._copy_article(a),
        ).pack(side="right", padx=(2, 4))

        ctk.CTkButton(
            btn_row,
            text="Edit",
            width=55,
            height=22,
            font=ctk.CTkFont(size=10),
            fg_color=("gray75", "gray30"),
            hover_color=("gray65", "gray40"),
            text_color=("black", "white"),
            command=lambda: self._edit_article(article),
        ).pack(side="right", padx=(2, 4))

        ctk.CTkButton(
            btn_row,
            text="Delete",
            width=55,
            height=22,
            font=ctk.CTkFont(size=10),
            fg_color=("gray75", "gray30"),
            hover_color=("gray65", "gray40"),
            text_color=("black", "white"),
            command=lambda aid=article_id: self._delete_article(aid),
        ).pack(side="right", padx=(2, 4))

        doi = article.get("doi", "")
        if doi and not doi.startswith("PPI:"):
            doi_url = f"https://doi.org/{doi}"
            ctk.CTkButton(
                btn_row,
                text="DOI",
                width=50,
                height=22,
                font=ctk.CTkFont(size=10),
                fg_color=("gray75", "gray30"),
                hover_color=("gray65", "gray40"),
                text_color=("black", "white"),
                command=lambda u=doi_url: _open_url(u),
            ).pack(side="right", padx=(0, 4))

        url = article.get("url", "")
        if url:
            ctk.CTkButton(
                btn_row,
                text="Open",
                width=60,
                height=22,
                font=ctk.CTkFont(size=10),
                fg_color=("gray75", "gray30"),
                hover_color=("gray65", "gray40"),
                text_color=("black", "white"),
                command=lambda u=url: _open_url(u),
            ).pack(side="right", padx=(0, 4))

    def _add_card(self, article: dict):
        """Add a compact article row that expands on click to show full details."""
        article_id = article.get("id")
        score = article.get("relevance_score") or 0
        score_color = "#4CAF50" if score >= 7 else "#FF9800" if score >= 4 else "gray"

        # Main card frame
        card = ctk.CTkFrame(self.scroll, corner_radius=6)
        card.pack(fill="x", pady=(0, 6), padx=2)

        # Clickable row frame
        row_frame = ctk.CTkFrame(card, fg_color="transparent")
        row_frame.pack(fill="x", padx=12, pady=8)

        # Right side: expand indicator (packed first so it reserves space on the right)
        indicator_label = ctk.CTkLabel(
            row_frame,
            text="▶",
            font=ctk.CTkFont(size=11),
            text_color="gray",
            width=16,
        )
        indicator_label.pack(side="right", padx=(4, 0))

        # Left side: vertical stack (title row + meta row)
        left_frame = ctk.CTkFrame(row_frame, fg_color="transparent")
        left_frame.pack(side="left", fill="x", expand=True)

        # Row 1: score badge + feedback badge + title (all horizontal)
        title_row = ctk.CTkFrame(left_frame, fg_color="transparent")
        title_row.pack(fill="x")

        ctk.CTkLabel(
            title_row,
            text=f"{score}/10",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=score_color,
            width=40,
        ).pack(side="left", padx=(0, 6))

        title = article.get("title") or "Untitled"
        title_label = ctk.CTkLabel(
            title_row,
            text=title,
            font=ctk.CTkFont(size=13, weight="bold"),
            wraplength=520,
            justify="left",
            anchor="w",
        )
        title_label.pack(side="left", fill="x", expand=True)

        # Row 2: authors • journal • year (below title)
        meta_parts = []

        # Authors: first author or "First, Second et al."
        authors_raw = article.get("authors", "")
        if authors_raw:
            author_list = [a.strip() for a in str(authors_raw).split(",") if a.strip()]
            if len(author_list) == 1:
                meta_parts.append(author_list[0])
            elif len(author_list) == 2:
                meta_parts.append(f"{author_list[0]}, {author_list[1]}")
            elif len(author_list) > 2:
                meta_parts.append(f"{author_list[0]} et al.")

        if article.get("journal"):
            meta_parts.append(article["journal"])

        date_val = article.get("date", "")
        if date_val:
            year = str(date_val)[:4]
            if year.isdigit():
                meta_parts.append(year)
        elif article.get("processed_at"):
            try:
                meta_parts.append(datetime.fromisoformat(article["processed_at"]).strftime("%Y"))
            except ValueError:
                pass

        meta_line = " • ".join(meta_parts)

        if meta_line:
            ctk.CTkLabel(
                left_frame,
                text=meta_line,
                font=ctk.CTkFont(size=11),
                text_color="gray",
                anchor="w",
                justify="left",
                wraplength=520,
            ).pack(fill="x", pady=(2, 0))

        # Bind click to card toggle — pass `card` (vertical frame) so detail drops below row
        def on_click(event):
            self._toggle_expand(article_id, card, indicator_label)

        for widget in (row_frame, title_label, indicator_label):
            widget.bind("<Button-1>", on_click)

        # If already expanded (page re-render), rebuild detail into card
        if article_id in self._expanded:
            self._build_detail_frame(article_id, card)

    def _copy_article(self, article: dict):
        """Copy article metadata + summary to clipboard as plain text."""
        parts = []
        if article.get("title"):
            parts.append(article["title"])
        if article.get("authors"):
            parts.append(f"Authors: {article['authors']}")
        if article.get("journal"):
            date = f" ({article['date']})" if article.get("date") else ""
            parts.append(f"Journal: {article['journal']}{date}")
        if article.get("doi"):
            parts.append(f"DOI: https://doi.org/{article['doi']}")
        elif article.get("url"):
            parts.append(f"URL: {article['url']}")
        if article.get("relevance_score"):
            parts.append(f"Relevance: {article['relevance_score']}/10")
        if article.get("summary"):
            parts.append(f"\nSummary: {article['summary']}")
        if article.get("tags"):
            parts.append(f"Tags: {article['tags']}")

        text = "\n".join(parts)
        self.master.clipboard_clear()
        self.master.clipboard_append(text)

    def _export_csv(self):
        """Export all saved articles to a CSV file chosen by the user."""
        articles = self.db.get_all_processed(limit=10000)
        if not articles:
            return

        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile="papermatcher_export.csv",
        )
        if not path:
            return

        fieldnames = [
            "title", "authors", "journal", "date", "doi", "url", "pmid",
            "relevance_score", "relevance_reason", "summary", "tags", "feedback",
        ]
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                for a in articles:
                    # Reconstruct date from processed_at if not stored separately
                    if not a.get("date") and a.get("processed_at"):
                        try:
                            a["date"] = datetime.fromisoformat(a["processed_at"]).strftime("%Y-%m-%d")
                        except ValueError:
                            pass
                    writer.writerow(a)
        except Exception as e:
            print(f"Export error: {e}")

    def _edit_article(self, article: dict):
        """Open a dialog to edit article tags, score, and summary."""
        dialog = ctk.CTkToplevel(self.master)
        dialog.title("Edit Article")
        dialog.geometry("600x500")
        dialog.transient(self.master)
        dialog.grab_set()

        # Wrap all content in a scrollable frame for vertical scrolling
        content = ScrollableFrame(dialog, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=0, pady=0)

        # Title (read-only)
        ctk.CTkLabel(
            content,
            text=article.get("title", "Untitled"),
            font=ctk.CTkFont(size=14, weight="bold"),
            wraplength=560,
            justify="left",
        ).pack(anchor="w", padx=15, pady=(15, 10))

        # Score field
        score_frame = ctk.CTkFrame(content, fg_color="transparent")
        score_frame.pack(fill="x", padx=15, pady=(0, 10))
        ctk.CTkLabel(score_frame, text="Relevance Score (1-10):", font=ctk.CTkFont(size=12)).pack(anchor="w")
        score_var = ctk.IntVar(value=article.get("relevance_score", 0))
        ctk.CTkSlider(
            score_frame, from_=1, to=10, number_of_steps=9,
            variable=score_var, width=200,
        ).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(score_frame, textvariable=score_var, font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")

        # Summary field
        ctk.CTkLabel(content, text="Summary:", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=15, pady=(5, 2))
        summary_text = ctk.CTkTextbox(content, height=80, wrap="word")
        summary_text.pack(fill="x", padx=15, pady=(0, 5))
        summary_text.insert("1.0", article.get("summary", ""))

        # Tags field — horizontal scroll via Canvas
        ctk.CTkLabel(content, text="Tags:", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=15, pady=(5, 2))
        tag_list_init = [t.strip() for t in article.get("tags", "").split(",") if t.strip()]

        # Create a horizontal scrollable container using Canvas
        tag_canvas = ctk.CTkCanvas(content, height=50, bg="#212121", highlightthickness=0)
        tag_canvas.pack(fill="x", padx=15, pady=(0, 2))

        tag_container = ctk.CTkFrame(tag_canvas, fg_color="transparent")
        tag_canvas_window = tag_canvas.create_window(0, 0, window=tag_container, anchor="nw")
        tag_pills = PillFrame(tag_container, items=tag_list_init, read_only=False)
        tag_pills.pack(fill="both", expand=True)

        def _on_tag_canvas_configure(event=None):
            tag_canvas.configure(scrollregion=tag_canvas.bbox("all"))
        tag_container.bind("<Configure>", _on_tag_canvas_configure)

        # Mousewheel binding for horizontal scroll on canvas
        def _on_tag_scroll(event):
            if event.delta > 0:
                tag_canvas.xview_scroll(-3, "units")
            else:
                tag_canvas.xview_scroll(3, "units")
        tag_canvas.bind("<MouseWheel>", _on_tag_scroll)

        tag_add_frame = ctk.CTkFrame(content, fg_color="transparent")
        tag_add_frame.pack(fill="x", padx=15, pady=(0, 5))
        tag_add_entry = ctk.CTkEntry(tag_add_frame, placeholder_text="Add tag, press Enter…")
        tag_add_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

        def _add_tag(event=None):
            raw = tag_add_entry.get().strip().strip("_").replace(" ", "_").lower()
            if raw and raw not in tag_pills.get_items():
                tag_pills.set_items(tag_pills.get_items() + [raw])
            tag_add_entry.delete(0, "end")

        tag_add_entry.bind("<Return>", _add_tag)
        ctk.CTkButton(tag_add_frame, text="Add", width=55, height=28,
                      command=_add_tag).pack(side="left")

        # Reason field
        ctk.CTkLabel(content, text="Relevance Reason:", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=15, pady=(5, 2))
        reason_text = ctk.CTkTextbox(content, height=60, wrap="word")
        reason_text.pack(fill="x", padx=15, pady=(0, 5))
        reason_text.insert("1.0", article.get("relevance_reason", ""))

        # Include/Exclude checkboxes
        include_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(content, text="Include in results", variable=include_var).pack(anchor="w", padx=15, pady=(5, 0))

        # Feedback selector
        feedback_var = ctk.StringVar(value=article.get("feedback", ""))
        ctk.CTkLabel(content, text="Feedback:", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=15, pady=(5, 2))
        feedback_combo = ctk.CTkComboBox(
            content, width=200,
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
                    tags=tag_pills.get_string(),
                    relevance_reason=reason_text.get("1.0", "end").strip(),
                    include=include_var.get(),
                    feedback=feedback_var.get(),
                )
                dialog.destroy()
                self._last_count = -1
                self.refresh(force=True)
            except Exception as e:
                ctk.CTkLabel(content, text=f"Error: {e}", text_color="red").pack()

        # Buttons stay outside scrollable area
        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=(10, 15))
        ctk.CTkButton(btn_frame, text="Save", width=100, command=save_and_close).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="Cancel", width=100, command=dialog.destroy).pack(side="left", padx=10)
