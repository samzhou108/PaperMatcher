"""PillFrame — displays a list of strings as rounded pill badges.

Supports two modes:
  read_only=True   — display-only, no interaction (for results tab tags)
  read_only=False  — each pill has an × button; on_change(new_list) fires on removal
"""

import customtkinter as ctk


class PillFrame(ctk.CTkFrame):
    """Wraps-horizontally... actually CTk doesn't do wrap layout.
    Uses a simple line-by-line approach: pills packed left inside a flow frame.
    """

    PILL_BG      = ("gray82", "gray28")
    PILL_BG_HVR  = ("#D32F2F", "#B71C1C")
    PILL_FG      = ("gray15", "gray90")
    X_FG         = ("gray40", "gray70")

    def __init__(self, master, items: list[str] = None,
                 read_only: bool = True,
                 on_change=None,
                 **kwargs):
        # Use a plain frame — pills wrap manually into rows
        kwargs.setdefault("fg_color", "transparent")
        kwargs.setdefault("corner_radius", 0)
        super().__init__(master, **kwargs)
        self._read_only = read_only
        self._on_change = on_change
        self._items: list[str] = list(items or [])
        self._render()

    def set_items(self, items: list[str]):
        self._items = [i for i in items if i.strip()]
        self._render()

    def get_items(self) -> list[str]:
        return list(self._items)

    def get_string(self) -> str:
        return ", ".join(self._items)

    def _render(self):
        for w in self.winfo_children():
            w.destroy()

        if not self._items:
            return

        # Pack pills into rows — simulate word-wrap by listening to configure
        # Simple approach: one row frame, pills wrap by being packed left
        # (CTk doesn't support true flow layout; we use a single frame with pack left)
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", anchor="w")

        for item in self._items:
            self._make_pill(row, item)

    def _make_pill(self, parent, text: str):
        pill = ctk.CTkFrame(
            parent,
            fg_color=self.PILL_BG,
            corner_radius=12,
        )
        pill.pack(side="left", padx=(0, 4), pady=2)

        ctk.CTkLabel(
            pill,
            text=text,
            font=ctk.CTkFont(size=11),
            text_color=self.PILL_FG,
            padx=8,
            pady=2,
        ).pack(side="left")

        if not self._read_only:
            x_btn = ctk.CTkButton(
                pill,
                text="×",
                width=18,
                height=18,
                font=ctk.CTkFont(size=12, weight="bold"),
                fg_color="transparent",
                hover_color=self.PILL_BG_HVR,
                text_color=self.X_FG,
                corner_radius=9,
                command=lambda t=text: self._remove(t),
            )
            x_btn.pack(side="left", padx=(0, 4))

    def _remove(self, text: str):
        if text in self._items:
            self._items.remove(text)
        self._render()
        if self._on_change:
            self._on_change(self._items)
