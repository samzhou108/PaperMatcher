"""Content fetcher that wraps PubMed scraper for article retrieval."""

from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from app.pipeline.pubmed_scraper import PubMedScraper
from app.utils.pubmed import PubMedClient


class ContentFetcher:
    """Fetch articles from PubMed database based on search criteria."""

    def __init__(self):
        self.pubmed_scraper = PubMedScraper(max_results=50)
        self.pubmed_client = PubMedClient()
        self.request_count = 0

    def search_articles(self, keywords: List[str], journals: List[str] = None,
                        since_days: int = 7) -> List[Dict[str, Any]]:
        """Search for articles matching research profile keywords.

        Strategy:
        1. Search PubMed by keywords from research profile
        2. Optionally also search by specific journal names
        3. Return deduplicated article list

        Returns:
            List of article dicts with title, authors, journal, date,
            pmid, doi, abstract, url
        """
        all_articles = []
        seen_pmids = set()

        # Search by keywords
        if keywords:
            print(f"[ContentFetcher] Searching PubMed for keywords: {keywords}")
            keyword_articles = self.pubmed_scraper.search_by_keywords(
                keywords, since_days=since_days
            )
            for article in keyword_articles:
                pmid = article.get("pmid", "")
                if pmid and pmid not in seen_pmids:
                    seen_pmids.add(pmid)
                    all_articles.append(article)
                elif not pmid:
                    # Use title for dedup fallback
                    title = article.get("title", "")
                    if title not in {a.get("title", "") for a in all_articles}:
                        all_articles.append(article)

        # Also search by monitored journals
        if journals:
            for journal in journals:
                print(f"[ContentFetcher] Searching PubMed for journal: {journal}")
                journal_articles = self.pubmed_scraper.search_by_journal(
                    journal, since_days=since_days
                )
                for article in journal_articles:
                    pmid = article.get("pmid", "")
                    if pmid and pmid not in seen_pmids:
                        seen_pmids.add(pmid)
                        all_articles.append(article)
                    elif not pmid:
                        title = article.get("title", "")
                        if title not in {a.get("title", "") for a in all_articles}:
                            all_articles.append(article)

        print(f"[ContentFetcher] Total unique articles found: {len(all_articles)}")
        return all_articles

    def fetch_article(self, url: str = "", title: str = "",
                      authors: list = None) -> Dict[str, Any]:
        """Fetch article metadata by URL or title.

        Strategy:
        1. Try PubMed search by title
        2. Return whatever metadata we get

        Note: This method is kept for compatibility with the existing pipeline
        but the primary path now goes through search_articles().
        """
        result = {
            "title": title,
            "authors": authors or [],
            "journal": "",
            "volume": None,
            "issue": None,
            "date": "",
            "doi": "",
            "pmid": "",
            "url": url,
            "abstract": "",
            "article_type": "",
        }

        if not title:
            return result

        # Try PubMed search
        pubmed_data = self.pubmed_client.search_article(title, authors)
        if pubmed_data:
            for key in ["abstract", "pmid", "doi", "journal", "date",
                        "volume", "issue", "authors", "title"]:
                if pubmed_data.get(key):
                    result[key] = pubmed_data[key]

        return result

    def fetch_by_pmid(self, pmid: str) -> Dict[str, Any]:
        """Fetch a single article by PMID."""
        result = self.pubmed_scraper.fetch_article(pmid)
        if result:
            return result

        # Fallback to PubMed E-utilities client
        pubmed_data = self.pubmed_client.fetch_article(pmid)
        if pubmed_data:
            return {
                "title": pubmed_data.get("title", ""),
                "authors": pubmed_data.get("authors", []),
                "journal": pubmed_data.get("journal", ""),
                "volume": pubmed_data.get("volume"),
                "issue": pubmed_data.get("issue"),
                "date": pubmed_data.get("date", ""),
                "doi": pubmed_data.get("doi", ""),
                "pmid": pmid,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                "abstract": pubmed_data.get("abstract", ""),
                "article_type": "",
            }
        return {}