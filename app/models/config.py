"""Application configuration management."""

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional

from .user_profile import UserProfile


@dataclass
class LLMConfig:
    """LLM API configuration."""

    mode: str = "cloud"  # "cloud" or "local" (legacy, kept for compat)
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    relevance_threshold: int = 6  # 1-10 scale
    scoring_model: str = "local"  # "local" or "cloud" (prototype tier)
    scoring_model_name: str = "llama3.2:latest"  # model name for Pass 2
    openrouter_key: str = ""
    # Screener model override: "local" = Ollama llama3.2, "cloud" = API for Pass 1
    screening_model: str = "local"
    screening_model_name: str = "llama3.2:latest"
    # History of previously successful (model, base_url) pairings
    previous_pairings: list = field(default_factory=list)

    def add_pairing(self, model: str, base_url: str):
        """Add a successful model+base_url pairing to history (no duplicates)."""
        entry = {"model": model, "base_url": base_url}
        if entry not in self.previous_pairings:
            self.previous_pairings.insert(0, entry)

    def model_options(self) -> list[str]:
        """Return ordered list of unique model names from pairing history."""
        seen: set[str] = set()
        result: list[str] = []
        for p in self.previous_pairings:
            m = p.get("model", "")
            if m and m not in seen:
                seen.add(m)
                result.append(m)
        return result

    def base_url_options(self) -> list[str]:
        """Return ordered list of unique base URLs from pairing history."""
        seen: set[str] = set()
        result: list[str] = []
        for p in self.previous_pairings:
            u = p.get("base_url", "")
            if u and u not in seen:
                seen.add(u)
                result.append(u)
        return result

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "LLMConfig":
        return cls(**data)


@dataclass
class PubMedConfig:
    """PubMed database scraper configuration."""

    search_keywords: List[str] = field(default_factory=list)
    journals_to_monitor: List[str] = field(default_factory=list)
    default_since_days: int = 7
    max_results_per_search: int = 50

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PubMedConfig":
        return cls(**data)


@dataclass
class AppConfig:
    """Main application configuration container."""

    profile: UserProfile = field(default_factory=UserProfile)
    pubmed: PubMedConfig = field(default_factory=PubMedConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    last_run: Optional[str] = None  # ISO datetime string

    def to_dict(self) -> dict:
        return {
            "profile": self.profile.to_dict(),
            "pubmed": self.pubmed.to_dict(),
            "llm": self.llm.to_dict(),
            "last_run": self.last_run,
        }

    def save(self, path: Optional[Path] = None):
        """Save config to JSON file."""
        config_path = path or (Path.home() / ".paperPilot" / "config.json")
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "AppConfig":
        """Load config from JSON file."""
        config_path = path or (Path.home() / ".paperPilot" / "config.json")
        with open(config_path, "r") as f:
            data = json.load(f)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        return cls(
            profile=UserProfile.from_dict(data.get("profile", {})),
            pubmed=PubMedConfig.from_dict(data.get("pubmed", {})),
            llm=LLMConfig.from_dict(data.get("llm", {})),
            last_run=data.get("last_run"),
        )