"""Score article relevance against user profile using LLM with 2-pass pipeline."""

from typing import Dict, Any, Tuple, Optional, List

from app.utils.llm_client import LLMClient
from app.utils.db import ArticleDatabase


class RelevanceScorer:
    """Score articles for relevance to user's research profile.

    Implements a 2-pass pipeline:
    - Pass 1 (screening): Fast local check via Ollama llama3.2 — filters out
      clearly irrelevant articles before hitting the scoring model.
    - Pass 2 (scoring + summary): Full relevance scoring and summarization
      using the configured model (local or cloud depending on tier).
    """

    def __init__(self, llm_client: LLMClient, db: ArticleDatabase | None = None,
                 project_context: str = ""):
        self.llm = llm_client
        self.db = db
        self.project_context = project_context

    def _build_feedback_prompt(self) -> str:
        """Build a prompt section from user feedback history (Phase 2)."""
        if not self.db:
            return ""

        relevant = self.db.get_feedback_history(limit=10, feedback="relevant")
        not_relevant = self.db.get_feedback_history(limit=10, feedback="not_relevant")

        if not relevant and not not_relevant:
            return ""

        lines = ["\n\nPREVIOUS USER FEEDBACK (use to calibrate scoring):"]
        if relevant:
            lines.append("Articles this user marked as RELEVANT:")
            for a in relevant:
                tags = f" — tags: {a['tags']}" if a["tags"] else ""
                lines.append(f"  - {a['title']}{tags}")
        if not_relevant:
            lines.append("Articles this user marked as NOT RELEVANT:")
            for a in not_relevant:
                tags = f" — tags: {a['tags']}" if a["tags"] else ""
                lines.append(f"  - {a['title']}{tags}")
        lines.append("Take this feedback into account when scoring new articles.")
        return "\n".join(lines)

    def score_article(self, profile: Dict[str, Any],
                      article: Dict[str, Any],
                      current_keywords: Optional[List[str]] = None,
                      must_include: Optional[List[str]] = None,
                      include_to_expand: Optional[List[str]] = None,
                      do_not_include: Optional[List[str]] = None) -> Tuple[int, str]:
        """
        Score an article for relevance to the user profile.

        Args:
            profile: User profile dict with role, research_description, keywords, topics.
            article: Article dict with title, abstract, journal, etc.
            current_keywords: List of search keywords for this run.
            must_include: Terms that must appear in relevant articles.
            include_to_expand: Additional terms to broaden the search.
            do_not_include: Terms that must NOT appear in results.

        Returns (score, reason) tuple. Score is 1-10.
        """
        title = article.get("title", "")
        abstract = article.get("abstract", "Not available")

        # --- Pass 1: Screening ---
        # Only screen if abstract is available (screening without text is
        # unreliable). If screening says NO, return score=1 immediately.
        if abstract and abstract.strip() != "Not available":
            passed_screening = self.llm.screen_article(
                title, abstract, profile, current_keywords=current_keywords
            )
            if not passed_screening:
                return 1, "Screened out by Pass 1 — not relevant to research focus"

        # --- Pass 2: Full scoring ---
        profile_dict = {
            "role": profile.get("role", ""),
            "research_description": profile.get("research_description", ""),
            "keywords": profile.get("keywords", []),
            "topics": profile.get("topics", []),
        }

        article_dict = {
            "title": title,
            "journal": article.get("journal", ""),
            "abstract": abstract,
        }

        score, reason = self.llm.score_relevance(
            profile_dict, article_dict,
            feedback_context=self._build_feedback_prompt(),
            project_context=self.project_context,
            must_include=must_include,
            include_to_expand=include_to_expand,
            do_not_include=do_not_include,
        )
        return score, reason

    def should_save(self, score: int, threshold: int = 6) -> bool:
        """Check if article meets the relevance threshold."""
        return score >= threshold