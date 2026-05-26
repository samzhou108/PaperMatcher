"""Main application window with tab navigation."""

import customtkinter as ctk

from app.models.config import AppConfig
from app.version import __version__
from app.utils.db import ArticleDatabase
from .profile_tab import ProfileTab
from .results_tab import ResultsTab
from .run_tab import RunTab
from .settings_tab import SettingsTab
from app.gui.widgets.scrollable_frame import ScrollableFrame


class AppWindow:
    """Main application window with tab-based navigation."""

    def __init__(self, master: ctk.CTk, config: AppConfig):
        self.master = master
        self.config = config

        master.title(f"PaperMatcher v{__version__}")
        master.geometry("950x750")
        master.minsize(900, 700)

        self._build_ui()

    def _build_ui(self):
        """Build the main UI with tabs."""
        self.db = ArticleDatabase()

        # Header
        header = ctk.CTkFrame(self.master, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(15, 5))

        title = ctk.CTkLabel(
            header,
            text="PaperMatcher",
            font=ctk.CTkFont(size=28, weight="bold"),
        )
        title.pack(side="left")

        subtitle = ctk.CTkLabel(
            header,
            text="Search PubMed & auto-score articles for your research",
            font=ctk.CTkFont(size=13),
            text_color="gray",
        )
        subtitle.pack(side="left", padx=(15, 0))

        # Save button
        save_btn = ctk.CTkButton(
            header,
            text="Save Config",
            width=120,
            command=self._save_config,
        )
        save_btn.pack(side="right")

        # Separator
        sep = ctk.CTkFrame(self.master, height=2, fg_color="gray75")
        sep.pack(fill="x", padx=20, pady=5)

        # Tab view
        self.tabview = ctk.CTkTabview(self.master)
        self.tabview.pack(fill="both", expand=True, padx=20, pady=(5, 20))

        # Create tabs — no Email or Vault tabs
        self.tabview.add("Run Pipeline")
        self.tabview.add("Results")
        self.tabview.add("Profile")
        self.tabview.add("Settings")

        # Populate tabs
        self.results_tab = ResultsTab(self.tabview.tab("Results"), self.db)
        self.profile_tab = ProfileTab(self.tabview.tab("Profile"), self.config)
        self.settings_tab = SettingsTab(self.tabview.tab("Settings"), self.config)
        self.run_tab = RunTab(
            self.tabview.tab("Run Pipeline"), self.config, self.db,
            sync_config=self._sync_config,
        )

        # Refresh results when switching to that tab
        self.tabview.configure(command=self._on_tab_change)

        # Global trackpad/mousewheel scroll handler
        # Note: ScrollableFrame handles its own scrolling; this is a fallback
        def _on_scroll(event):
            if event.delta == 0:
                return
            direction = -1 if event.delta > 0 else 1
            widget = event.widget
            while widget is not None:
                if isinstance(widget, ScrollableFrame):
                    widget._parent_canvas.yview_scroll(direction, "units")
                    return
                if isinstance(widget, ctk.CTkScrollableFrame):
                    widget._parent_canvas.yview_scroll(direction, "units")
                    return
                try:
                    widget = widget.master
                except AttributeError:
                    break

        self.master.bind_all("<MouseWheel>", _on_scroll)

    def _on_tab_change(self):
        """Refresh results tab when selected."""
        try:
            if self.tabview.get() == "Results":
                self.results_tab.refresh()
        except Exception:
            pass

    def _sync_config(self):
        """Sync UI fields -> config object without writing to disk.
        Called automatically before each pipeline run."""
        self.profile_tab.save_to_config()
        self.settings_tab.save_to_config()

    def _save_config(self):
        """Save current configuration."""
        self.profile_tab.save_to_config()
        self.settings_tab.save_to_config()

        try:
            self.config.save()
            self._show_toast("Configuration saved!")
        except Exception as e:
            self._show_error(f"Failed to save: {e}")

    def _show_toast(self, message: str):
        """Show a temporary success message."""
        toast = ctk.CTkLabel(
            self.master,
            text=message,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#4CAF50",
            text_color="white",
            corner_radius=6,
        )
        toast.place(relx=0.5, rely=0.95, anchor="center")
        self.master.after(2000, toast.destroy)

    def _show_error(self, message: str):
        """Show an error message."""
        dialog = ctk.CTkToplevel(self.master)
        dialog.title("Error")
        dialog.geometry("400x150")
        dialog.transient(self.master)

        ctk.CTkLabel(
            dialog,
            text="Error",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="red",
        ).pack(pady=(15, 5))

        ctk.CTkLabel(
            dialog,
            text=message,
            wraplength=350,
        ).pack(pady=5)

        ctk.CTkButton(
            dialog,
            text="OK",
            command=dialog.destroy,
            width=100,
        ).pack(pady=10)