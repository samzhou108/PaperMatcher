"""KeywordEntry — CTkEntry with inline autocomplete for comma-separated keywords.

Uses a plain CTkFrame (not a Toplevel) so it never intercepts clicks on other
widgets. The suggestion panel packs below the entry and pushes content down
while visible, then disappears on selection or focus loss.
"""

import customtkinter as ctk


KEYWORD_SUGGESTIONS = [
    # Immunology & immune therapy
    "T cell", "B cell", "macrophage", "cytokine", "innate immunity",
    "adaptive immunity", "inflammation", "immunotherapy", "vaccine",
    "regulatory T cells", "NK cells", "dendritic cells", "B7 molecules",
    "checkpoint inhibitor", "CAR-T", "antigen presentation", "MHC",
    "complement system", "toll-like receptor", "interferon", "interleukin",
    # Stem cells & regenerative medicine
    "stem cell therapy", "bone marrow stem cells", "mesenchymal stem cells",
    "induced pluripotent stem cells", "hematopoietic stem cells",
    "tissue engineering", "regenerative medicine", "cell transplantation",
    "organoid", "differentiation", "self-renewal",
    # Neuroscience & neurobiology
    "neurodegeneration", "neuronal plasticity", "synaptic transmission",
    "neurotrophic factor", "neural stem cell", "brain-derived neurotrophic factor",
    "axon", "dendrite", "astrocyte", "oligodendrocyte", "synapse",
    "neurotransmitter", "neuropathy", "neuroinflammation", "brain",
    # Molecular & genomic
    "single-cell RNA-seq", "epigenetics", "CRISPR", "gene expression",
    "chromatin remodeling", "DNA methylation", "histone modification",
    "transcriptomics", "proteomics", "metagenomics", "whole genome sequencing",
    "gene regulation", "non-coding RNA", "microRNA", "long non-coding RNA",
    # Cancer biology & oncology
    "cancer immunology", "tumor microenvironment", "metastasis",
    "cancer metabolism", "drug resistance", "personalized medicine",
    "oncogene", "tumor suppressor", "apoptosis", "cell cycle",
    # Developmental biology & embryology
    "embryo", "development", "morphogenesis", "cell migration",
    "developmental signaling", "axis formation", "pattern formation",
    "organogenesis", "cell fate", "stem cell niche",
    # Infectious diseases & microbiology
    "bacteria", "virus", "fungus", "parasite", "pathogenesis",
    "antibiotic resistance", "vaccine development", "infection",
    "host-pathogen interaction", "microbiome", "viral entry",
    # Physiology & metabolism
    "glucose metabolism", "fatty acid oxidation", "mitochondria",
    "metabolic syndrome", "obesity", "energy homeostasis",
    "circadian rhythm", "hormone", "endocrine", "pancreas",
    "liver metabolism", "kidney", "cardiovascular",
    # Cardiovascular research
    "heart", "hypertension", "atherosclerosis", "myocardial infarction",
    "cardiac regeneration", "angiogenesis", "vascular biology",
    # Structural biology & biophysics
    "protein structure", "X-ray crystallography", "cryo-EM", "NMR",
    "protein folding", "molecular dynamics", "structure-function",
    # Plant biology & botany
    "plant", "photosynthesis", "agriculture", "plant immunity",
    "plant hormone", "plant development", "crop improvement",
    # Ecology & environmental science
    "ecology", "conservation", "biodiversity", "climate change",
    "environmental toxicology", "ecosystem", "population dynamics",
    # Molecular pathways & signaling
    "signal transduction", "apoptosis", "oxidative stress", "autophagy",
    "protein misfolding", "ubiquitination", "phosphorylation",
    "Wnt signaling", "TGF-beta", "Notch", "Hedgehog",
    # Drug discovery & therapeutic approaches
    "monoclonal antibody", "gene therapy", "drug delivery", "small molecule",
    "biologics", "precision medicine", "targeted therapy", "combination therapy",
    "drug screening", "pharmaceutical", "pharmacokinetics",
    # Aging & longevity
    "aging", "senescence", "longevity", "age-related disease",
    "gerontology", "lifespan", "healthspan",
    # Behavioral science
    "behavior", "neurobehavior", "learning", "memory", "cognition",
    "brain function", "neural circuit", "addiction", "psychiatric disorder",
    # Synthetic biology & bioengineering
    "synthetic biology", "genetic engineering", "metabolic engineering",
    "bioengineering", "cell engineering", "biofuel",
    # Evolution & systematics
    "evolution", "phylogenetics", "comparative biology", "adaptation",
    # Nutrition & metabolism disorders
    "nutrition", "dietary", "metabolomics", "lipidome", "metabolic disorder",
    # Bioinformatics & computational biology
    "bioinformatics", "systems biology", "mathematical biology",
    "computational biology", "machine learning in biology",
    # Clinical research
    "clinical trial", "patient", "diagnosis", "prognosis", "biomarker",
    "personalized treatment", "clinical outcome",
]


class KeywordEntry(ctk.CTkFrame):
    """
    Drop-in replacement for CTkEntry for comma-separated keyword fields.

    Proxies get() / insert() / delete() / configure() to the inner CTkEntry.
    Shows an inline suggestion panel that packs directly below the entry —
    no Toplevel, so it never blocks clicks on the rest of the UI.
    """

    def __init__(self, parent, suggestions: list = None, on_add_keyword=None, **entry_kwargs):
        super().__init__(parent, fg_color="transparent")
        self._suggestions = suggestions if suggestions is not None else KEYWORD_SUGGESTIONS
        self._buttons: list[ctk.CTkButton] = []
        self._sel_idx: int = -1
        self._on_add_keyword = on_add_keyword

        self._entry = ctk.CTkEntry(self, **entry_kwargs)
        self._entry.pack(fill="x", padx=2, pady=2)
        self._entry.configure(width=400)

        # Suggestion panel — packed below entry only when there are matches
        self._panel = ctk.CTkFrame(self, corner_radius=4, fg_color=("gray88", "gray20"))
        self._panel_visible = False

        self._entry.bind("<KeyRelease>", self._on_key_release)
        self._entry.bind("<FocusOut>", self._on_focus_out)
        self._entry.bind("<Down>", self._on_down)
        self._entry.bind("<Up>", self._on_up)
        self._entry.bind("<Return>", self._on_return)
        self._entry.bind("<Escape>", lambda e: self._hide())

    # ------------------------------------------------------------------
    # Public API — proxy to inner entry
    # ------------------------------------------------------------------

    def get(self) -> str:
        return self._entry.get()

    def insert(self, index, text: str):
        self._entry.insert(index, text)

    def delete(self, first, last="end"):
        self._entry.delete(first, last)

    def configure(self, **kwargs):
        _entry_keys = {
            "placeholder_text", "state", "show", "font", "text_color",
            "border_color", "border_width", "width", "height", "corner_radius",
        }
        entry_kw = {k: v for k, v in kwargs.items() if k in _entry_keys}
        frame_kw = {k: v for k, v in kwargs.items() if k not in _entry_keys}
        if entry_kw:
            self._entry.configure(**entry_kw)
        if frame_kw:
            super().configure(**frame_kw)

    def focus(self):
        self._entry.focus()

    def focus_set(self):
        self._entry.focus_set()

    def cget(self, key: str):
        try:
            return self._entry.cget(key)
        except Exception:
            return super().cget(key)

    # ------------------------------------------------------------------
    # Dropdown logic
    # ------------------------------------------------------------------

    def _current_partial(self) -> str:
        """Return the partially-typed word after the last comma."""
        parts = self._entry.get().split(",")
        return parts[-1].strip().lower()

    def _on_key_release(self, event):
        if event.keysym in ("Return", "Tab", "Escape", "Down", "Up"):
            return
        partial = self._current_partial()
        if len(partial) < 1:
            self._hide()
            return
        already = {k.strip().lower() for k in self._entry.get().split(",") if k.strip()}
        matches = [
            s for s in self._suggestions
            if partial in s.lower() and s.lower() not in already
        ][:8]
        if matches:
            self._show(matches)
        else:
            self._hide()

    def _on_focus_out(self, event):
        # Small delay so a click on a panel button fires before we hide
        self._entry.after(150, self._hide)

    def _show(self, matches: list[str]):
        # Clear previous buttons
        for w in self._panel.winfo_children():
            w.destroy()
        self._buttons = []
        self._sel_idx = -1

        for match in matches:
            btn = ctk.CTkButton(
                self._panel,
                text=match,
                height=26,
                anchor="w",
                fg_color="transparent",
                hover_color=("gray78", "gray30"),
                text_color=("black", "white"),
                font=ctk.CTkFont(size=12),
                corner_radius=3,
                command=lambda m=match: self._select(m),
            )
            btn.pack(fill="x", padx=3, pady=1)
            self._buttons.append(btn)

        if not self._panel_visible:
            self._panel.pack(fill="x", pady=(2, 0))
            self._panel_visible = True

    def _hide(self):
        if self._panel_visible:
            self._panel.pack_forget()
            self._panel_visible = False
        for w in self._panel.winfo_children():
            w.destroy()
        self._buttons = []
        self._sel_idx = -1

    def _select(self, suggestion: str):
        if self._on_add_keyword:
            self._on_add_keyword(suggestion)
        self._entry.delete(0, "end")
        self._hide()
        self._entry.focus()
        self._entry.icursor("end")

    # ------------------------------------------------------------------
    # Keyboard navigation
    # ------------------------------------------------------------------

    def _on_down(self, event):
        if not self._buttons:
            return
        self._sel_idx = min(self._sel_idx + 1, len(self._buttons) - 1)
        self._highlight()

    def _on_up(self, event):
        if not self._buttons:
            return
        self._sel_idx = max(self._sel_idx - 1, 0)
        self._highlight()

    def _on_return(self, event):
        if self._panel_visible and 0 <= self._sel_idx < len(self._buttons):
            self._select(self._buttons[self._sel_idx].cget("text"))
            return "break"
        if self._on_add_keyword:
            text = self._entry.get().strip().rstrip(",").strip()
            if text:
                self._on_add_keyword(text)
                self._entry.delete(0, "end")
                self._hide()
                return "break"

    def _highlight(self):
        for i, btn in enumerate(self._buttons):
            btn.configure(
                fg_color=("gray78", "gray30") if i == self._sel_idx else "transparent"
            )
