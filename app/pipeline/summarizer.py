"""Generate summaries and key points for articles using LLM."""

from typing import Dict, Any

from app.utils.llm_client import LLMClient


class Summarizer:
    """Generate structured summaries for relevant articles."""
    
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client
    
    def summarize(self, profile: Dict[str, Any],
                  article: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate a structured summary for an article.
        
        Returns dict with summary, relevance_note, key_points, tags.
        """
        profile_dict = {
            "role": profile.get("role", ""),
            "research_description": profile.get("research_description", ""),
            "keywords": ", ".join(profile.get("keywords", [])),
        }
        
        article_dict = {
            "title": article.get("title", ""),
            "journal": article.get("journal", ""),
            "abstract": article.get("abstract", "Not available"),
        }
        
        return self.llm.summarize_article(profile_dict, article_dict)
