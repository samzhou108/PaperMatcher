"""OpenAI-compatible LLM client wrapper with 2-pass pipeline support."""

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List

from openai import OpenAI, APIError, APITimeoutError

logger = logging.getLogger(__name__)

# Load ~/.papermatcher/.env if present — VIP keys live here, never in source.
try:
    from dotenv import load_dotenv
    _env_path = Path.home() / ".papermatcher" / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass  # python-dotenv not installed; env vars must be set externally

# Distribution tier: "vip" or "prototype"
# Set at build time — do not expose in UI.
# vip  → single bundled binary for personal/paid account users
# prototype → public / BYOK build
DISTRIBUTION_TIER = "prototype"

# Keys — loaded from ~/.papermatcher/.env, never hardcoded in source.
# VIP .env must contain:
#   PAPERPILOT_OPENROUTER_KEY=sk-or-...
#   PAPERPILOT_NCBI_KEY=...
VIP_OPENROUTER_KEY = os.environ.get("PAPERPILOT_OPENROUTER_KEY")

# NCBI API key: VIP build uses env var; prototype falls back to file
NCBI_API_KEY = os.environ.get("PAPERPILOT_NCBI_KEY") or None
if NCBI_API_KEY is None:
    _ncbi_key_path = Path.home() / ".papermatcher" / "ncbi_api_key.txt"
    if _ncbi_key_path.exists():
        NCBI_API_KEY = _ncbi_key_path.read_text().strip() or None

# Screener model defaults
SCREENER_LOCAL_MODEL = "llama3.2:latest"
SCREENER_CLOUD_DEFAULT = "deepseek/deepseek-v4-flash:free"


def _tier_display_name() -> str:
    """Human-readable tier label for UI."""
    return "VIP" if DISTRIBUTION_TIER == "vip" else "Prototype"


class LLMClient:
    """OpenAI-compatible LLM client for scoring and summarization.

    Supports a 2-pass pipeline:
    - Pass 1 (screening): always local Ollama llama3.2:latest
    - Pass 2 (scoring + summary): model depends on tier
      - vip: deepseek/deepseek-v4-flash:free via OpenRouter
      - prototype: user-configurable model (local or cloud)
    """

    def __init__(self, base_url: str = "https://api.openai.com/v1",
                 api_key: str = "", model: str = "gpt-4o-mini",
                 scoring_model: str = "local", openrouter_key: str = "",
                 screening_model: str = "local",
                 screening_model_name: str = "llama3.2:latest",
                 screening_base_url: str = "",
                 screening_api_key: str = ""):
        self.base_url = base_url
        self.model = model
        self.api_key = api_key or "not-needed"
        self.scoring_model = scoring_model
        self.openrouter_key = openrouter_key
        self.screening_model = screening_model
        self.screening_model_name = screening_model_name
        self.screening_base_url = screening_base_url or base_url
        self.screening_api_key = screening_api_key or openrouter_key or api_key or "not-needed"

        # Pass 2 cloud client
        self.client = OpenAI(base_url=base_url, api_key=self.api_key)

        # Pass 1 local Ollama client
        self._ollama_client = OpenAI(
            base_url="http://localhost:11434/v1",
            api_key="not-needed",
        )

        # Pass 1 cloud client (may differ from Pass 2 if separate provider configured)
        self._screener_cloud_client = OpenAI(
            base_url=self.screening_base_url,
            api_key=self.screening_api_key,
        )

    # ------------------------------------------------------------------
    # Pass 1 — Screening (always local)
    # ------------------------------------------------------------------

    def screen_article(self, title: str, abstract: str,
                       profile: Dict[str, Any],
                       current_keywords: Optional[List[str]] = None) -> bool:
        """Pass 1: Does this article touch the user's research area?

        Returns True (YES/MAYBE) or False (NO / screener unavailable).
        If screening_model == "cloud", uses the API screener instead of Ollama.
        """
        # --- Build Pass 1 prompt ---
        keywords_text = ", ".join(current_keywords) if current_keywords else "none"
        prompt = (
            f"You are a research screening assistant.\n\n"
            f"RESEARCHER PROFILE:\n"
            f"{profile.get('research_description', '')}\n\n"
            f"KEYWORDS (define the specific search scope):\n"
            f"{keywords_text}\n\n"
            f"Given a paper title and abstract, determine if it is relevant "
            f"to the researcher's interests and keywords.\n"
            f"Title: {title}\n"
            f"Abstract: {abstract[:2000]}\n\n"
            f"Answer YES, MAYBE, or NO only."
        )

        # --- Choose screener client ---
        use_cloud_screener = (
            self.screening_model == "cloud"
            and self.screening_base_url
        )

        if use_cloud_screener:
            # Cloud screener uses its own dedicated client and model name
            try:
                response = self._screener_cloud_client.chat.completions.create(
                    model=self.screening_model_name,
                    messages=[
                        {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=64,
                    temperature=0.1,
                    timeout=30,
                )
                answer = (response.choices[0].message.content or "").strip().upper()
                return answer in ("YES", "MAYBE")
            except Exception as e:
                print(f"[LLMClient] Pass 1 cloud screening failed: {e}")
                return False
        else:
            # Default: Ollama local screener with seed for reproducibility
            try:
                response = self._ollama_client.chat.completions.create(
                    model="llama3.2:latest",
                    messages=[
                        {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=64,
                    temperature=0.1,
                    seed=42,
                    timeout=30,
                )
                answer = (response.choices[0].message.content or "").strip().upper()
                return answer in ("YES", "MAYBE")
            except Exception as e:
                print(f"[LLMClient] Pass 1 screening failed (Ollama): {e}")
                return False  # Skip screening, go straight to Pass 2

    # ------------------------------------------------------------------
    # Pass 2 — Scoring
    # ------------------------------------------------------------------

    def score_relevance(self, profile: Dict[str, str],
                        article: Dict[str, str],
                        retries: int = 2,
                        feedback_context: str = "",
                        project_context: str = "",
                        must_include: Optional[List[str]] = None,
                        include_to_expand: Optional[List[str]] = None,
                        do_not_include: Optional[List[str]] = None) -> Tuple[int, str]:
        """Score article relevance. Model depends on tier + config."""

        system_msg = (
            "You are a strict research evaluator. Score papers on a 1-10 scale where:\n"
            "- 9-10: Paper DIRECTLY addresses the researcher's core keywords and topics.\n"
            "- 7-8: Paper is clearly relevant — shares most keywords or same subfield.\n"
            "- 5-6: Paper is in the same broad field but not a direct match.\n"
            "- 3-4: Paper is tangentially related.\n"
            "- 1-2: Paper is clearly unrelated.\n\n"
            "Use the FULL 1-10 range. Be strict."
        )

        if feedback_context:
            system_msg += feedback_context

        # Structured search terms (highest priority — determines relevance boundaries)
        search_constraints = ""
        if must_include:
            search_constraints += f"\n\nMUST INCLUDE (all must be present): {', '.join(must_include)}"
        if include_to_expand:
            search_constraints += f"\n\nINCLUDE TO EXPAND (any is a bonus): {', '.join(include_to_expand)}"
        if do_not_include:
            search_constraints += f"\n\nDO NOT INCLUDE (must be absent): {', '.join(do_not_include)}"
        if search_constraints:
            system_msg += f"\n\nSEARCH CONSTRAINTS:{search_constraints}"

        if project_context:
            system_msg += f"\n\nCURRENT RUN FOCUS (takes priority for this search):\n{project_context}\n"

        topics = profile.get("topics") or []
        topics_line = f"Research areas: {', '.join(topics)}\n" if topics else ""
        user_msg = (
            f"Researcher: {profile.get('role', '')} studying {profile.get('research_description', '')}\n"
            f"Keywords: {profile.get('keywords', '')}\n"
            f"{topics_line}"
            f"\nTitle: {article.get('title', '')[:300]}\n"
            f"Abstract: {article.get('abstract', 'Not available')[:3000]}\n\n"
            "Output ONLY: {\"score\": <int 1-10>, \"reason\": \"<1-sentence>\"}"
        )

        model = self._resolve_pass2_model()
        client = self._resolve_client(model)

        for attempt in range(retries + 1):
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": user_msg},
                    ],
                    max_tokens=300,
                    temperature=0.5,
                    timeout=25,
                )
                content = response.choices[0].message.content or ""
                result = self._parse_json_response(content)
                score = max(1, min(10, result.get("score", 0)))
                return score, result.get("reason", "No reason provided")
            except (APIError, APITimeoutError) as e:
                logger.error("Scoring API error (attempt %d/%d): %s", attempt + 1, retries + 1, e)
                if attempt < retries:
                    time.sleep(2 ** attempt)
                    continue
                # Auth errors (401/404) should stop the pipeline, not return default score
                if "404" in str(e) or "401" in str(e) or "not found" in str(e).lower():
                    raise
                return 0, f"API error: {e}"
            except Exception as e:
                logger.error("Scoring unexpected error: %s", e)
                return 0, f"Error: {e}"

        return 0, "Failed after retries"

    # ------------------------------------------------------------------
    # Summarization
    # ------------------------------------------------------------------

    def summarize_article(self, profile: Dict[str, str],
                          article: Dict[str, str],
                          retries: int = 2) -> Dict[str, Any]:
        """Generate structured summary and key points.

        Prompt structure adapted from:
        - fabric/analyze_paper_simple by Daniel Miessler (MIT License)
          https://github.com/danielmiessler/fabric
          Fields used: SUMMARY, IMPLICATIONS, METHODOLOGY, CONFLICT/BIAS, REPRODUCIBILITY
        - fabric/create_tags by Daniel Miessler (MIT License)
          Tag extraction logic: lowercase, specific, no spaces.
        """

        system_msg = (
            "You are a scientific paper analysis assistant for a research literature tool. "
            "Be concise and precise. Respond ONLY in valid JSON with no markdown formatting."
        )

        user_msg = (
            f"Analyze this paper for a {profile.get('role', 'researcher')} studying "
            f"{profile.get('research_description', 'general science')}.\n\n"
            f"Title: {article.get('title', '')}\n"
            f"Abstract: {article.get('abstract', 'Not available')[:3000]}\n\n"
            "Respond with this exact JSON structure:\n"
            "{\n"
            "  \"summary\": \"<16-word sentence: the paper's main claim or finding>\",\n"
            "  \"implications\": \"<20-word sentence: what this means for the field or researcher>\",\n"
            "  \"methodology\": \"<15-word description: study type, sample size, key methods>\",\n"
            "  \"conflict_bias\": \"<15-word assessment of funding sources, author affiliations, or potential bias. Write NONE DETECTED if clean>\",\n"
            "  \"reproducibility\": \"<score 1-5>/<brief reason, e.g. 3/5 — Methods described but key parameters missing>\",\n"
            "  \"relevance_note\": \"<15-word sentence: why this is relevant to the researcher's specific focus>\",\n"
            "  \"key_points\": [\"<point 1, 12 words max>\", \"<point 2>\", \"<point 3>\"],\n"
            "  \"tags\": [\"<lowercase_tag1>\", \"<lowercase_tag2>\", \"<tag3>\", \"<tag4>\", \"<tag5>\"]\n"
            "}\n\n"
            "Tag rules: lowercase, underscores instead of spaces, specific scientific terms only, 5-8 tags."
        )

        model = self._resolve_pass2_model()
        client = self._resolve_client(model)

        for attempt in range(retries + 1):
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": user_msg},
                    ],
                    max_tokens=500,
                    temperature=0.3,
                    timeout=25,
                )
                content = response.choices[0].message.content or ""
                result = self._parse_json_response(content)
                raw_tags = result.get("tags", [])
                clean_tags = [
                    t.strip().strip("_").replace(" ", "_").lower()
                    for t in raw_tags if isinstance(t, str) and t.strip().strip("_")
                ]
                return {
                    "summary": result.get("summary", "Summary not available."),
                    "implications": result.get("implications", ""),
                    "methodology": result.get("methodology", ""),
                    "conflict_bias": result.get("conflict_bias", ""),
                    "reproducibility": result.get("reproducibility", ""),
                    "relevance_note": result.get("relevance_note", ""),
                    "key_points": result.get("key_points", []),
                    "tags": clean_tags,
                }
            except (APIError, APITimeoutError) as e:
                logger.error("Summarization API error (attempt %d/%d): %s", attempt + 1, retries + 1, e)
                if attempt < retries:
                    time.sleep(2 ** attempt)
                    continue
                # Auth errors (401/404) should stop the pipeline, not return default summary
                if "404" in str(e) or "401" in str(e) or "not found" in str(e).lower():
                    raise
            except Exception as e:
                logger.error("Summarization unexpected error: %s", e)

        return {
            "summary": "Summary generation failed.",
            "implications": "",
            "methodology": "",
            "conflict_bias": "",
            "reproducibility": "",
            "relevance_note": "",
            "key_points": [],
            "tags": [],
        }

    # ------------------------------------------------------------------
    # Connection test
    # ------------------------------------------------------------------

    def test_connection(self, pass1: bool = False) -> Tuple[bool, str]:
        """Test LLM endpoint. pass1=True tests the screener, False tests scorer."""
        try:
            if pass1:
                if self.screening_model == "local":
                    client = self._ollama_client
                    model = self.screening_model_name or "llama3.2:latest"
                else:
                    client = self._screener_cloud_client
                    model = self.screening_model_name
            else:
                model = self._resolve_pass2_model()
                client = self._resolve_client(model)
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
                timeout=10,
            )
            if resp.choices and resp.choices[0].message:
                return True, f"Connected ({model})"
            return False, "No response from model"
        except Exception as e:
            return False, str(e)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_pass2_model(self) -> str:
        """Determine which model to use for Pass 2 based on tier and config."""
        if DISTRIBUTION_TIER == "vip":
            return "deepseek/deepseek-v4-flash:free"
        # prototype tier
        if self.scoring_model == "cloud" and self.base_url:
            return self.model
        return "llama3.2:latest"  # local fallback

    def _resolve_client(self, model: str) -> OpenAI:
        """Return the appropriate OpenAI client for the given model."""
        if model == "llama3.2:latest":
            return self._ollama_client
        if "openrouter" in self.base_url or "baidu" in model:
            # VIP tier: use key from ~/.papermatcher/.env
            # Prototype tier: use key entered in Settings
            key = VIP_OPENROUTER_KEY or self.openrouter_key or self.api_key or "not-needed"
            return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)
        return self.client

    @staticmethod
    def _parse_json_response(content: str) -> Dict[str, Any]:
        """Parse JSON from LLM response, handling code blocks."""
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
            if content.endswith("```"):
                content = content[:-3]
        elif content.startswith("```"):
            content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
        content = content.strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            import re
            match = re.search(r'\{[\s\S]*\}', content)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
        return {}