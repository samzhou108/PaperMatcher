"""CTkScrollableFrame with proper macOS trackpad and mouse wheel scrolling."""

import customtkinter as ctk


class ScrollableFrame(ctk.CTkScrollableFrame):
    """A CTkScrollableFrame that properly handles trackpad and mouse wheel scrolling
    on macOS, Windows, and Linux."""

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self._bind_scroll_events()

    def _bind_scroll_events(self):
        """Bind mousewheel and trackpad events for cross-platform scrolling."""
        # macOS two-finger scroll and mouse wheel
        self.bind("<MouseWheel>", self._on_mousewheel, add="+")

        # Windows mouse wheel
        self.bind("<MouseWheel>", self._on_mousewheel_win, add="+")

        # Linux button4/button5 scroll
        self.bind("<Button-4>", self._on_button_scroll_linux, add="+")
        self.bind("<Button-5>", self._on_button_scroll_linux, add="+")

        # Also bind to child widgets so scrolling works when hovering over them
        self._bind_children(self)

    def _bind_children(self, widget):
        """Recursively bind scroll events to all children."""
        for child in widget.winfo_children():
            child_type = child.winfo_class()
            # Only bind to frame-like widgets and text widgets
            if child_type in ("CTkScrollableFrame", "CTkFrame", "CTkTextbox",
                              "Frame", "LabelFrame", "Toplevel", "Canvas"):
                child.bind("<MouseWheel>", self._on_mousewheel, add="+")
                child.bind("<Button-4>", self._on_button_scroll_linux, add="+")
                child.bind("<Button-5>", self._on_button_scroll_linux, add="+")
                self._bind_children(child)

    def _on_mousewheel(self, event):
        """Handle macOS trackpad and mouse wheel (delta is ±120 on Windows, ±1 on macOS)."""
        try:
            # macOS: event.delta is typically 1 or -1 (or ±120 on some systems)
            if event.delta:
                direction = -1 if event.delta > 0 else 1
                self._parent_canvas.yview_scroll(direction, "units")
        except Exception:
            pass
        return "break"

    def _on_mousewheel_win(self, event):
        """Handle Windows mouse wheel (event.delta is ±120)."""
        try:
            if event.delta:
                direction = -1 if event.delta > 0 else 1
                self._parent_canvas.yview_scroll(direction, "units")
        except Exception:
            pass
        return "break"

    def _on_button_scroll_linux(self, event):
        """Handle Linux button4 (up) and button5 (down) scroll events."""
        try:
            if event.num == 4:
                self._parent_canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                self._parent_canvas.yview_scroll(1, "units")
        except Exception:
            pass
        return "break"