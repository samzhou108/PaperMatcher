"""PubMed database scraper for searching articles by keyword and research profile."""

import logging
import time
import xml.etree.ElementTree as ET
from typing import List, Dict, Optional
import httpx

BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
logger = logging.getLogger(__name__)


class PubMedScraper:
    """Scrape PubMed database for articles matching search criteria."""

    def __init__(self, max_results: int = 50):
        self.max_results = max_results
        self.last_request_time = 0
        self.min_interval = 0.4  # Max ~3 requests per second (NCBI rate limit)
        self._session = httpx.Client(timeout=30.0)

    def close(self):
        """Release the HTTP session."""
        try:
            self._session.close()
        except Exception:
            pass

    def _rate_limit(self):
        """Respect PubMed rate limits."""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_request_time = time.time()

    def _get_with_retry(self, url: str, params: dict, retries: int = 3,
                        base_delay: float = 2.0) -> httpx.Response:
        """GET with exponential backoff on 429/500 errors."""
        if self._session.is_closed:
            self._session = httpx.Client(timeout=30.0)
        for attempt in range(retries):
            self._rate_limit()
            try:
                resp = self._session.get(url, params=params)
                if resp.status_code == 429 or resp.status_code >= 500:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"HTTP {resp.status_code} on attempt {attempt+1}, "
                        f"retrying in {delay}s..."
                    )
                    time.sleep(delay)
                    continue
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as e:
                if attempt < retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"HTTP error {e}, retrying in {delay}s...")
                    time.sleep(delay)
                    continue
                raise
        raise Exception(f"Failed after {retries} retries")

    def search_by_keywords(self, keywords: List[str], since_days: int = 7,
                           must_include: Optional[List[str]] = None,
                           include_to_expand: Optional[List[str]] = None,
                           do_not_include: Optional[List[str]] = None) -> List[Dict]:
        """Search PubMed by keywords and return article dicts.

        Args:
            keywords: List of search keywords (OR'd together).
            since_days: Look back this many days.
            must_include: Terms that MUST appear in results (AND'd with query).
            include_to_expand: Additional terms to OR with the main query.
            do_not_include: Terms that must NOT appear in results.

        Returns:
            List of article dicts with title, authors, journal, date, pmid, doi, abstract
        """
        self._rate_limit()

        # Build search query
        query_parts = []
        for kw in keywords:
            if kw.strip():
                query_parts.append(f'"{kw.strip()}"[Title/Abstract]')

        # Expand with additional terms
        for term in (include_to_expand or []):
            if term.strip():
                query_parts.append(f'"{term.strip()}"[Title/Abstract]')

        if not query_parts:
            return []

        main_query = "(" + " OR ".join(query_parts) + ")"

        # Must-include: AND each required term
        must_parts = []
        for term in (must_include or []):
            if term.strip():
                must_parts.append(f'"{term.strip()}"[Title/Abstract]')
        if must_parts:
            main_query = f"{main_query} AND {' AND '.join(must_parts)}"

        # Do-not-include: AND NOT each excluded term
        exclude_parts = []
        for term in (do_not_include or []):
            if term.strip():
                exclude_parts.append(f'"{term.strip()}"[Title/Abstract]')
        if exclude_parts:
            main_query = f"{main_query} NOT ({' OR '.join(exclude_parts)})"

        # Add date filter
        from datetime import datetime, timedelta
        since_date = (datetime.now() - timedelta(days=since_days)).strftime("%Y/%m/%d")
        full_query = f"{main_query} AND {since_date}:3000[Date - Publication]"

        print(f"[PubMedScraper] Searching: {full_query[:200]}...")

        # Step 1: esearch - get PMIDs
        search_params = {
            "db": "pubmed",
            "term": full_query,
            "retmax": self.max_results,
            "retmode": "json",
            "sort": "relevance",
        }

        try:
            resp = self._get_with_retry(f"{BASE_URL}/esearch.fcgi", search_params)
            data = resp.json()
        except Exception as e:
            logger.error(f"PubMed search error: {e}")
            return []

        id_list = data.get("esearchresult", {}).get("idlist", [])
        if not id_list:
            print(f"[PubMedScraper] No results found")
            return []

        print(f"[PubMedScraper] Found {len(id_list)} articles")

        # Step 2: fetch - get full details
        articles = []
        # Fetch in batches of 20
        for i in range(0, len(id_list), 20):
            batch = id_list[i:i + 20]
            articles.extend(self._fetch_batch(batch))

        return articles

    def search_by_journal(self, journal_name: str, since_days: int = 7) -> List[Dict]:
        """Search PubMed by journal name.

        Args:
            journal_name: Journal name (e.g., "Cell", "Nature", "Science")
            since_days: Look back this many days

        Returns:
            List of article dicts
        """
        from datetime import datetime, timedelta
        since_date = (datetime.now() - timedelta(days=since_days)).strftime("%Y/%m/%d")

        query = f"{journal_name}[Journal] AND {since_date}:3000[Date - Publication]"

        print(f"[PubMedScraper] Searching journal: {query}")

        search_params = {
            "db": "pubmed",
            "term": query,
            "retmax": self.max_results,
            "retmode": "json",
            "sort": "date",
        }

        try:
            resp = self._get_with_retry(f"{BASE_URL}/esearch.fcgi", search_params)
            data = resp.json()
        except Exception as e:
            logger.error(f"PubMed journal search error: {e}")
            return []

        id_list = data.get("esearchresult", {}).get("idlist", [])
        if not id_list:
            return []

        articles = []
        for i in range(0, len(id_list), 20):
            batch = id_list[i:i + 20]
            articles.extend(self._fetch_batch(batch))

        return articles

    def fetch_article(self, pmid: str) -> Optional[Dict]:
        """Fetch a single article by PMID."""
        try:
            params = {
                "db": "pubmed",
                "id": pmid,
                "retmode": "xml",
            }
            resp = self._get_with_retry(f"{BASE_URL}/efetch.fcgi", params)
            results = self._parse_pubmed_xml(resp.text)
            return results[0] if results else None
        except Exception as e:
            logger.error(f"PubMed fetch error for PMID {pmid}: {e}")
            return None

    def _fetch_batch(self, pmid_list: List[str]) -> List[Dict]:
        """Fetch article details for a batch of PMIDs."""
        try:
            params = {
                "db": "pubmed",
                "id": ",".join(pmid_list),
                "retmode": "xml",
            }
            resp = self._get_with_retry(f"{BASE_URL}/efetch.fcgi", params)
            return self._parse_pubmed_xml(resp.text)
        except Exception as e:
            logger.error(f"PubMed batch fetch error: {e}")
            return []

    def _parse_pubmed_xml(self, xml_content: str) -> List[Dict]:
        """Parse PubMed XML response into article dicts."""
        articles = []

        try:
            root = ET.fromstring(xml_content)

            # Try with namespace first
            ns = {'ns': 'http://www.ncbi.nlm.nih.gov/entrez/eutils/xxml-utils'}
            articles_elem = root.findall(".//ns:PubmedArticle", ns)
            # Fall back to no namespace
            if not articles_elem:
                articles_elem = root.findall(".//PubmedArticle")

            for article in articles_elem:
                result = self._parse_article(article)
                if result:
                    articles.append(result)

        except Exception as e:
            print(f"[PubMedScraper] XML parse error: {e}")

        return articles

    def _parse_article(self, article) -> Optional[Dict]:
        """Parse a single PubMed article element."""
        try:
            # Title
            title_elem = article.find(".//ArticleTitle")
            title = title_elem.text if title_elem is not None and title_elem.text else ""
            if not title:
                return None

            # Authors
            authors = []
            author_list = article.find(".//AuthorList")
            if author_list is not None:
                for author in author_list.findall(".//Author"):
                    last_name = author.find(".//LastName")
                    fore_name = author.find(".//ForeName")
                    name_parts = []
                    if last_name is not None and last_name.text:
                        name_parts.append(last_name.text)
                    if fore_name is not None and fore_name.text:
                        name_parts.append(fore_name.text)

                    if name_parts:
                        authors.append(" ".join(name_parts))

            # Abstract
            abstract_texts = []
            abstract_elem = article.find(".//Abstract")
            if abstract_elem is not None:
                for abst_text in abstract_elem.findall(".//AbstractText"):
                    label = abst_text.get("Label", "")
                    text = "" if abst_text.text is None else abst_text.text
                    for child in abst_text:
                        if child.text:
                            text += " " + child.text
                        if child.tail:
                            text += child.tail
                    if label:
                        abstract_texts.append(f"{label}: {text}")
                    else:
                        abstract_texts.append(text)

            # DOI
            doi = ""
            for id_elem in article.findall(".//ArticleId"):
                if id_elem.get("IdType") == "doi":
                    doi = id_elem.text or ""
                    break

            # Journal
            journal_elem = article.find(".//Journal/Title")
            journal = journal_elem.text if journal_elem is not None and journal_elem.text else ""

            # Date
            pub_date = article.find(".//Journal/JournalIssue/PubDate")
            date = ""
            if pub_date is not None:
                year = pub_date.find(".//Year")
                month = pub_date.find(".//Month")
                day = pub_date.find(".//Day")

                date_parts = []
                if year is not None and year.text:
                    date_parts.append(year.text)
                if month is not None and month.text:
                    date_parts.append(month.text)
                if day is not None and day.text:
                    date_parts.append(day.text)

                date = "-".join(date_parts)

            # Volume & Issue
            volume = None
            issue = None
            volume_elem = article.find(".//Journal/JournalIssue/Volume")
            if volume_elem is not None and volume_elem.text:
                try:
                    volume = int(volume_elem.text)
                except ValueError:
                    pass

            issue_elem = article.find(".//Journal/JournalIssue/Issue")
            if issue_elem is not None and issue_elem.text:
                try:
                    issue = int(issue_elem.text)
                except ValueError:
                    pass

            # Article type
            article_type = ""
            article_type_elem = article.find(".//PublicationType")
            if article_type_elem is not None:
                article_type = article_type_elem.text or ""

            # PMID
            pmid_elem = article.find(".//PMID")
            pmid = pmid_elem.text if pmid_elem is not None and pmid_elem.text else ""

            return {
                "title": title,
                "authors": authors,
                "journal": journal,
                "volume": volume,
                "issue": issue,
                "date": date,
                "doi": doi,
                "pmid": pmid,
                "abstract": " ".join(abstract_texts) if abstract_texts else "",
                "article_type": article_type,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
                "source_email": "",
            }

        except Exception as e:
            print(f"[PubMedScraper] Article parse error: {e}")
            return None