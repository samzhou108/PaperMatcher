"""User profile dataclass for research preferences."""

from dataclasses import dataclass, field, asdict
from typing import List, Optional
import json


@dataclass
class UserProfile:
    """Researcher profile used for relevance scoring."""
    
    name: str = ""
    role: str = "PhD Student"  # PhD Student, Postdoc, Researcher, Clinician, Other
    research_description: str = ""
    keywords: List[str] = field(default_factory=list)
    topics: List[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "UserProfile":
        return cls(**data)
    
    def keywords_str(self) -> str:
        """Return keywords as comma-separated string."""
        return ", ".join(self.keywords)
    
    def topics_str(self) -> str:
        """Return topics as comma-separated string."""
        return ", ".join(self.topics)
