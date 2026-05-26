"""CTkScrollableFrame with reliable macOS trackpad and mouse wheel scrolling.

Uses Enter/Leave + bind_all: scroll activates globally while the mouse is
inside this frame, released when it leaves. This is the most reliable pattern
for CTk 5.2.x on macOS where child-widget event propagation is inconsistent.
"""

import customtkinter as ctk


class ScrollableFrame(ctk.CTkScrollableFrame):
    """CTkScrollableFrame that reliably handles trackpad scrolling on all platforms."""

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self._bind_scroll_events()

    def refresh_scroll_bindings(self):
        """No-op kept for API compatibility."""
        pass

    def _bind_scroll_events(self):
        self.bind("<Enter>", self._on_enter, add="+")
        self.bind("<Leave>", self._on_leave, add="+")
        self.bind("<MouseWheel>", self._on_scroll, add="+")
        self.bind("<Button-4>", self._on_linux_scroll, add="+")
        self.bind("<Button-5>", self._on_linux_scroll, add="+")

    def _on_enter(self, event):
        self.bind_all("<MouseWheel>", self._on_scroll)
        self.bind_all("<Button-4>", self._on_linux_scroll)
        self.bind_all("<Button-5>", self._on_linux_scroll)

    def _on_leave(self, event):
        # tkinter fires <Leave> on the frame whenever the cursor moves into a
        # child widget — even if it's still visually inside the scroll area.
        # Only unbind when the cursor has genuinely left the frame's bounds.
        try:
            x, y = event.x_root, event.y_root
            fx = self.winfo_rootx()
            fy = self.winfo_rooty()
            if fx <= x <= fx + self.winfo_width() and fy <= y <= fy + self.winfo_height():
                return  # still inside — spurious Leave from a child widget
        except Exception:
            pass
        self.unbind_all("<MouseWheel>")
        self.unbind_all("<Button-4>")
        self.unbind_all("<Button-5>")

    def _on_scroll(self, event):
        if event.widget.winfo_class() == "Text":
            # Let the textbox scroll when it has room; hand off to the frame
            # only when the textbox is already at its scroll boundary.
            try:
                first, last = event.widget.yview()
                scrolling_up = event.delta > 0
                at_top    = first <= 0.001
                at_bottom = last  >= 0.999
                if (scrolling_up and not at_top) or (not scrolling_up and not at_bottom):
                    return  # textbox still has room — let it scroll naturally
                # At boundary: fall through and scroll the frame instead
            except Exception:
                return  # can't determine — leave textbox alone
        try:
            delta = event.delta
            if delta:
                direction = -1 if delta > 0 else 1
                # Clamp 1-5 units: macOS trackpad (delta ~1-10), Windows wheel (~120)
                units = max(1, min(5, abs(delta) // 3))
                self._parent_canvas.yview_scroll(direction * units, "units")
        except Exception:
            pass

    def _on_linux_scroll(self, event):
        try:
            self._parent_canvas.yview_scroll(-1 if event.num == 4 else 1, "units")
        except Exception:
            pass
