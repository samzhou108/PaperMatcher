"""LLM-assisted PubMed query generation.

Priority logic for query term sources
--------------------------------------
1. Advanced search filled (include_to_expand / must_include):
   Build mechanically — no LLM, no run_focus parsing.
   expand → OR block with MeSH expansion.
   must   → AND [Title/Abstract].
   Run focus is passed to Pass 2 scoring only.

2. Run focus only (no advanced terms):
   LLM extracts key terms from run_focus text.
   Strip filler ("the role of", conjunctions, etc.).
   Profile keywords and research description NOT in prompt.

3. Neither filled:
   LLM uses profile keywords.

Model preference
----------------
Uses Pass 2 cloud model when configured (stronger, follows instructions
reliably). Falls back to local llama3.2 when cloud is unavailable.
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import List, Optional

import httpx

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3.2:latest"
NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
MESH_CACHE_PATH = Path.home() / ".papermatcher" / "mesh_cache.json"

# -----------------------------------------------------------------------
# Prompt
# -----------------------------------------------------------------------

_SYSTEM = """\
You generate PubMed search queries. Output ONLY the query string — no explanation, no labels.

## PubMed field tag rules
| Context                          | Tag             |
|----------------------------------|-----------------|
| Broad keyword (default)          | no tag          |
| Known MeSH descriptor            | [MeSH Terms]    |
| Term that MUST appear in text    | [Title/Abstract]|
| Article type filter              | [Publication Type] |

## Query structure
Broad terms → OR block (no tag):          ("term1" OR "term2")
MeSH expansion (when descriptor known):   ("Descriptor"[MeSH Terms] OR "keyword")
Must-include terms (AND, restricted):     AND "term"[Title/Abstract]
Exclude terms (NOT):                      NOT ("term1" OR "term2")

## AND usage — be very conservative
AND halves your result set with every addition. Too many ANDs = zero results.
MAXIMUM 2 AND conditions total in the entire query beyond the core OR block.
When in doubt, OR is better than AND.

## [Title/Abstract] field tag — use sparingly
ONLY use [Title/Abstract] for single well-known scientific terms or short
established compound terms (e.g., "ChIP-seq", "CRISPR", "flow cytometry").
NEVER use [Title/Abstract] for:
- Multi-word descriptive phrases (e.g., "changes in blood nerve barrier")
- Words extracted verbatim from the focus description
- Common words that may not appear in every abstract
If a term needs a field tag and is not a specific technique/assay/method,
use no field tag instead (defaults to All Fields — broader match).

## Tentative vs required terms
Terms in `focus:` with soft language are OPTIONAL — add to OR block, never AND.
Soft markers: "best if", "ideally", "if possible", "preferably", "consider",
"when available", "optional", "would be nice".
Only `must:` terms become AND [Title/Abstract] filters.
Extract ONLY concrete scientific nouns from `focus:` — strip filler phrases
("the role of", "contributing to", "I want to know", conjunctions, articles).

## Examples
Input | keywords: neuropathic pain, TRPV1 | focus: peripheral sensitization | must: electrophysiology | mesh: Neuralgia → neuropathic pain
Output: ("Neuralgia"[MeSH Terms] OR "neuropathic pain" OR "TRPV1") AND "electrophysiology"[Title/Abstract]

Input | keywords: autophagy, mTOR | focus: drug resistance in solid tumours | must: (none) | mesh: Autophagy → autophagy
Output: ("Autophagy"[MeSH Terms] OR "autophagy" OR "mTOR" OR "rapamycin")

Input | keywords: (none) | focus: role of dendritic cells in pain generation and maintenance | must: (none) | mesh: Dendritic Cells → dendritic cells; Pain → pain
Output: ("Dendritic Cells"[MeSH Terms] OR "dendritic cells") AND ("Pain"[MeSH Terms] OR "pain" OR "nociception")

BAD example (do NOT do this — too many ANDs, [T/A] on long phrases → zero results):
Input | keywords: (none) | focus: changes in blood nerve barrier with aging | must: (none) | mesh: Blood-Nerve Barrier → blood nerve barrier; Aging → aging
WRONG: ("blood nerve barrier"[Title/Abstract]) AND ("changes in blood nerve barrier"[Title/Abstract]) AND ("with aging"[Title/Abstract])
RIGHT: ("Blood-Nerve Barrier"[MeSH Terms] OR "blood nerve barrier") AND ("Aging"[MeSH Terms] OR "aging")

Input | keywords: (none) | focus: role of zinc finger proteins in infection model and myeloid immunity, best if include ChIP-seq | must: (none) | mesh: Zinc Finger Proteins → zinc finger; Myeloid Cells → myeloid; Infection → infection
Output: ("Zinc Finger Proteins"[MeSH Terms] OR "zinc finger" OR "KZFP") AND ("Myeloid Cells"[MeSH Terms] OR "myeloid") AND ("Infection"[MeSH Terms] OR "infection model") OR "ChIP-seq"

Input | keywords: microglia, neuroinflammation | focus: TLR4 signalling in Alzheimer disease | must: mouse model, RNA-seq | mesh: Microglia → microglia; Neuroinflammatory Diseases → neuroinflammation; Alzheimer Disease → Alzheimer disease
Output: ("Microglia"[MeSH Terms] OR "microglia") AND ("Neuroinflammatory Diseases"[MeSH Terms] OR "neuroinflammation") AND ("Alzheimer Disease"[MeSH Terms] OR "Alzheimer") AND "TLR4" AND "mouse model"[Title/Abstract] AND "RNA-seq"[Title/Abstract]
"""


# -----------------------------------------------------------------------
# QueryBuilder
# -----------------------------------------------------------------------

class QueryBuilder:
    """Generate PubMed queries with priority-based term selection."""

    _NCBI_INTERVAL = 0.35  # NCBI rate limit (3 req/s unauthenticated)

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        http_client: Optional[httpx.Client] = None,
        ncbi_api_key: Optional[str] = None,
        cloud_model: Optional[str] = None,
        cloud_base_url: Optional[str] = None,
        cloud_api_key: Optional[str] = None,
    ):
        self.model = model
        self._ncbi_key = ncbi_api_key
        self._owned_client = http_client is None
        self._client = http_client or httpx.Client(timeout=60.0)
        self._last_ncbi = 0.0
        self._cloud_model = cloud_model
        self._cloud_base_url = cloud_base_url
        self._cloud_api_key = cloud_api_key

        self._mesh_cache: dict[str, Optional[str]] = {}
        self._load_mesh_cache()

    def close(self):
        if self._owned_client and self._client:
            try:
                self._client.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return True if a generation backend is reachable."""
        if self._cloud_model and self._cloud_base_url:
            return True  # assume cloud is reachable; errors surface at generation time
        try:
            resp = self._client.get(f"{OLLAMA_URL}/api/tags", timeout=3.0)
            if resp.status_code != 200:
                return False
            models = {m["name"] for m in resp.json().get("models", [])}
            base = self.model.split(":")[0].lower()
            return any(base in m.lower() for m in models)
        except Exception:
            return False

    def build(
        self,
        profile_keywords: List[str],
        research_description: str,
        run_focus: str,
        must_include: List[str],
        include_to_expand: Optional[List[str]] = None,
        topics: Optional[List[str]] = None,
        mesh_hint_keywords: Optional[List[str]] = None,
    ) -> str:
        """Generate a PubMed query string applying priority logic.

        Returns the query body (no date filter).
        Raises RuntimeError on failure or empty output.
        """
        include_to_expand = include_to_expand or []
        must_include = must_include or []
        has_advanced = bool(include_to_expand or must_include)

        # ------------------------------------------------------------------
        # Priority 1: Advanced search terms filled → build mechanically
        # No LLM, no run_focus parsing. Run focus is for Pass 2 only.
        # ------------------------------------------------------------------
        if has_advanced:
            logger.info("QueryBuilder: using advanced search terms directly (no LLM)")
            return self._build_mechanical(include_to_expand, must_include)

        # ------------------------------------------------------------------
        # Priority 2: Run focus only → LLM extracts key terms from focus text
        # Profile keywords provided as MeSH hints only (not in prompt keywords)
        # ------------------------------------------------------------------
        if run_focus.strip():
            all_hint_terms = list(mesh_hint_keywords or []) + list(profile_keywords or [])
            mesh_pairs = self._mesh_lookup_terms(all_hint_terms + [run_focus])
            mesh_line = "; ".join(f"{d} → {t}" for t, d in mesh_pairs) if mesh_pairs else "(none)"
            user_msg = (
                "### YOUR TASK (not an example):\n"
                f"Input | keywords: (none) | "
                f"focus: {run_focus.strip()} | "
                f"must: (none) | "
                f"expand: (none) | "
                f"mesh: {mesh_line}"
                "\nOutput:"
            )
            logger.info("QueryBuilder: run_focus mode — cloud=%s", bool(self._cloud_model))
            return self._generate(user_msg)

        # ------------------------------------------------------------------
        # Priority 3: Profile keywords fallback
        # ------------------------------------------------------------------
        all_terms = list(profile_keywords or []) + list(mesh_hint_keywords or [])
        mesh_pairs = self._mesh_lookup_terms(all_terms)
        mesh_line = "; ".join(f"{d} → {t}" for t, d in mesh_pairs) if mesh_pairs else "(none)"
        kw_str = ", ".join(profile_keywords) if profile_keywords else "(none)"
        user_msg = (
            "### YOUR TASK (not an example):\n"
            f"Input | keywords: {kw_str} | "
            f"focus: (none) | "
            f"must: (none) | "
            f"expand: (none) | "
            f"mesh: {mesh_line}"
            "\nOutput:"
        )
        logger.info("QueryBuilder: profile keywords fallback")
        return self._generate(user_msg)

    # ------------------------------------------------------------------
    # Mechanical query builder (Priority 1 — no LLM)
    # ------------------------------------------------------------------

    def _build_mechanical(self, include_to_expand: List[str], must_include: List[str]) -> str:
        """Build query directly from advanced search terms with MeSH expansion."""
        parts = []

        if include_to_expand:
            mesh_pairs = self._mesh_lookup_terms(include_to_expand)
            mesh_map = {t.lower(): d for t, d in mesh_pairs}

            or_clauses = []
            for term in include_to_expand:
                descriptor = mesh_map.get(term.lower())
                if descriptor:
                    or_clauses.append(f'("{descriptor}"[MeSH Terms] OR "{term}")')
                else:
                    or_clauses.append(f'"{term}"')
            if or_clauses:
                parts.append("(" + " OR ".join(or_clauses) + ")")

        for term in must_include:
            if term.strip():
                parts.append(f'AND "{term}"[Title/Abstract]')

        query = " ".join(parts)
        logger.info("QueryBuilder mechanical: %s", query)
        return query

    # ------------------------------------------------------------------
    # LLM generation (Priority 2 & 3)
    # ------------------------------------------------------------------

    def _generate(self, user_msg: str) -> str:
        """Call cloud or local LLM to produce a query string."""
        logger.debug("QueryBuilder prompt:\n%s", user_msg)
        raw = self._call_cloud(user_msg) if self._cloud_model else self._call_ollama(user_msg)
        query = self._clean_output(raw)
        if not query:
            raise RuntimeError(f"Model returned empty output. Raw: {raw!r}")
        logger.info("QueryBuilder generated: %s", query)
        return query

    def _call_cloud(self, user_msg: str) -> str:
        """Call the configured cloud model (OpenAI-compatible API)."""
        try:
            from openai import OpenAI
            client = OpenAI(
                base_url=self._cloud_base_url,
                api_key=self._cloud_api_key or "not-needed",
            )
            response = client.chat.completions.create(
                model=self._cloud_model,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": user_msg},
                ],
                max_tokens=350,
                temperature=0.1,
                timeout=25,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.warning("Cloud query generation failed (%s), falling back to Ollama", e)
            return self._call_ollama(user_msg)

    def _call_ollama(self, user_msg: str) -> str:
        """Call local Ollama model."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            "stream": False,
            "keep_alive": 0,
            "options": {
                "temperature": 0.05,
                "num_predict": 350,
                "stop": ["\n\n", "Input |", "##", "### "],
            },
        }
        resp = self._client.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=90.0)
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content", "").strip()

    # ------------------------------------------------------------------
    # MeSH lookup helpers
    # ------------------------------------------------------------------

    def _mesh_lookup_terms(self, terms: List[str]) -> List[tuple]:
        """Look up MeSH for a list of terms; return (input_term, descriptor) pairs."""
        seen: set[str] = set()
        unique: List[str] = []
        for t in terms:
            tl = t.lower().strip()
            if tl and tl not in seen:
                seen.add(tl)
                unique.append(t.strip())
        unique = unique[:10]

        pairs = []
        for term in unique:
            descriptor = self._lookup_mesh(term)
            if descriptor and descriptor.lower() != term.lower():
                pairs.append((term, descriptor))
        return pairs

    def _ncbi_rate_limit(self):
        elapsed = time.monotonic() - self._last_ncbi
        if elapsed < self._NCBI_INTERVAL:
            time.sleep(self._NCBI_INTERVAL - elapsed)
        self._last_ncbi = time.monotonic()

    def _lookup_mesh(self, term: str) -> Optional[str]:
        key = term.lower().strip()
        if key in self._mesh_cache:
            return self._mesh_cache[key]
        descriptor = self._fetch_mesh_descriptor(term)
        self._mesh_cache[key] = descriptor
        self._save_mesh_cache()
        return descriptor

    def _fetch_mesh_descriptor(self, term: str) -> Optional[str]:
        try:
            self._ncbi_rate_limit()
            params: dict = {"db": "mesh", "term": term.strip(), "retmax": 1, "retmode": "json"}
            if self._ncbi_key:
                params["api_key"] = self._ncbi_key
            r = self._client.get(f"{NCBI_BASE}/esearch.fcgi", params=params, timeout=10.0)
            if r.status_code != 200:
                return None
            id_list = r.json().get("esearchresult", {}).get("idlist", [])
            if not id_list:
                return None

            self._ncbi_rate_limit()
            sum_params: dict = {"db": "mesh", "id": id_list[0], "retmode": "json"}
            if self._ncbi_key:
                sum_params["api_key"] = self._ncbi_key
            r2 = self._client.get(f"{NCBI_BASE}/esummary.fcgi", params=sum_params, timeout=10.0)
            if r2.status_code != 200:
                return None
            result = r2.json().get("result", {})
            uids = result.get("uids", [])
            descriptor = result.get(str(uids[0]), {}).get("ds_name", "") if uids else ""
            return descriptor or None
        except Exception as exc:
            logger.debug("MeSH lookup failed for %r: %s", term, exc)
            return None

    def _load_mesh_cache(self):
        try:
            if MESH_CACHE_PATH.exists():
                with MESH_CACHE_PATH.open("r", encoding="utf-8") as f:
                    self._mesh_cache = json.load(f)
        except Exception:
            self._mesh_cache = {}

    def _save_mesh_cache(self):
        try:
            MESH_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with MESH_CACHE_PATH.open("w", encoding="utf-8") as f:
                json.dump(self._mesh_cache, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Output cleaning
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_output(raw: str) -> str:
        text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        text = re.sub(r"(?i)^output\s*:\s*", "", text.strip())
        text = " ".join(text.split())
        return text.strip()
