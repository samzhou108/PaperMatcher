"""Article dataclass for parsed paper data."""

from dataclasses import dataclass, field, asdict
from typing import List, Optional
from datetime import datetime


@dataclass
class Article:
    """Represents a scientific article extracted from a journal email."""
    
    title: str = ""
    authors: List[str] = field(default_factory=list)
    journal: str = ""
    volume: Optional[int] = None
    issue: Optional[int] = None
    date: Optional[str] = None  # YYYY-MM-DD
    doi: str = ""
    pmid: str = ""
    url: str = ""
    abstract: str = ""
    article_type: str = ""  # e.g., "Review", "Original Article", "News"
    
    # Scoring & summary fields
    relevance_score: int = 0
    relevance_reason: str = ""
    summary: str = ""
    relevance_note: str = ""
    key_points: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    
    # Processing metadata
    processed_date: Optional[str] = None
    source_email: str = ""
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "Article":
        return cls(**data)
    
    def authors_str(self) -> str:
        """Return authors as formatted string."""
        if not self.authors:
            return "Unknown"
        if len(self.authors) <= 3:
            return ", ".join(self.authors)
        return f"{self.authors[0]} et al."
    
    def sanitized_title(self, max_len: int = 80) -> str:
        """Sanitize title for safe filename use."""
        import re
        safe = re.sub(r'[^\w\s-]', '', self.title).strip()
        safe = re.sub(r'\s+', '-', safe)
        return safe[:max_len]
