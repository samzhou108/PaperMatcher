"""PubMed database scraper for searching articles by keyword and research profile."""

import logging
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Optional
import httpx

BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)


class PubMedScraper:
    """Scrape PubMed database for articles matching search criteria."""

    # --- Rate limits ---
    # Authenticated: 10 requests/sec (api_key present)
    # Unauthenticated: 3 requests/sec
    RATE_LIMIT_AUTH = 0.1
    RATE_LIMIT_ANON = 0.4

    def __init__(self, max_results: int = 500, session: Optional[httpx.Client] = None,
                 api_key: Optional[str] = None, batch_size: int = 200):
        self.max_results = max_results
        self.min_interval = self.RATE_LIMIT_AUTH if api_key else self.RATE_LIMIT_ANON
        self.batch_size = batch_size
        self._api_key = api_key
        self.last_request_time = 0.0

        self._owned_session = None
        if session is not None:
            self._session = session
        else:
            self._owned_session = httpx.Client(timeout=30.0)
            self._session = self._owned_session

    def close(self):
        """Release the HTTP session (only if we own it)."""
        try:
            if self._owned_session is not None:
                self._owned_session.close()
        except Exception:
            pass

    def _get_mesh_descriptor(self, keyword: str) -> Optional[str]:
        """Look up the preferred MeSH descriptor for a keyword via NCBI E-utilities.

        Uses esearch on db=mesh to find a matching descriptor UID, then esummary
        to get the canonical descriptor name. Returns None if no match or on error.
        """
        try:
            # Step 1: esearch in the MeSH database
            params: dict = {
                "db": "mesh",
                "term": keyword.strip(),
                "retmax": 1,
                "retmode": "json",
            }
            if self._api_key:
                params["api_key"] = self._api_key
            self._rate_limit()
            resp = self._session.get(f"{BASE_URL}/esearch.fcgi", params=params, timeout=10)
            if resp.status_code != 200:
                return None
            data = resp.json()
            id_list = data.get("esearchresult", {}).get("idlist", [])
            if not id_list:
                return None

            mesh_uid = id_list[0]

            # Step 2: esummary to retrieve the preferred descriptor name
            sum_params: dict = {
                "db": "mesh",
                "id": mesh_uid,
                "retmode": "json",
            }
            if self._api_key:
                sum_params["api_key"] = self._api_key
            self._rate_limit()
            resp2 = self._session.get(f"{BASE_URL}/esummary.fcgi", params=sum_params, timeout=10)
            if resp2.status_code != 200:
                return None
            summary = resp2.json()
            result = summary.get("result", {})
            uids = result.get("uids", [])
            if not uids:
                return None
            descriptor = result.get(str(uids[0]), {}).get("ds_name", "")
            return descriptor if descriptor else None
        except Exception:
            return None

    def _rate_limit(self):
        """Respect PubMed rate limits."""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_request_time = time.time()

    def _get_with_retry(self, url: str, params: dict, retries: int = 5,
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
                           do_not_include: Optional[List[str]] = None,
                           pub_types: Optional[List[str]] = None,
                           pub_type_exclude: bool = False,
                           use_mesh: bool = True,
                           exclude_pmids: Optional[set] = None) -> List[Dict]:
        """Search PubMed by keywords and return article dicts.

        Args:
            keywords: List of search keywords (OR'd together).
            since_days: Look back this many days.
            must_include: Terms that MUST appear in results (AND'd with query).
            include_to_expand: Additional terms to OR with the main query.
            do_not_include: Terms that must NOT appear in results.
            use_mesh: If True, expand keywords to MeSH descriptors where available.
            pub_types: Publication type filters (e.g. ["Review[Publication Type]"]).
            pub_type_exclude: If True, exclude pub_types (NOT); if False, include (AND).

        Returns:
            List of article dicts with title, authors, journal, date, pmid, doi, abstract
        """
        self._rate_limit()

        # Build search query.
        # Profile keywords and expand terms: no field tag = implicit [All Fields].
        #   This catches MeSH-indexed terms, title, abstract, author, journal, etc.
        # Must Include: [Title/Abstract] only — these are specific project filters.
        # Do Not Include: no field tag — exclude regardless of where the term appears.
        # MeSH form (when enabled): ("Descriptor"[MeSH Terms] OR "keyword")
        query_parts = []
        for kw in keywords:
            if not kw.strip():
                continue
            if use_mesh:
                mesh_term = self._get_mesh_descriptor(kw)
                if mesh_term and mesh_term.lower() != kw.strip().lower():
                    logger.info(f"MeSH expansion: '{kw}' → '{mesh_term}'")
                    query_parts.append(f'("{mesh_term}"[MeSH Terms] OR "{kw.strip()}")')
                else:
                    query_parts.append(f'"{kw.strip()}"')
            else:
                query_parts.append(f'"{kw.strip()}"')

        # Include-to-expand: OR'd into the main keyword block, all fields
        for term in (include_to_expand or []):
            if term.strip():
                query_parts.append(f'"{term.strip()}"')

        if not query_parts:
            return []

        main_query = "(" + " OR ".join(query_parts) + ")"

        # Must-include: AND each term, restricted to Title/Abstract
        must_parts = []
        for term in (must_include or []):
            if term.strip():
                must_parts.append(f'"{term.strip()}"[Title/Abstract]')
        if must_parts:
            main_query = f"{main_query} AND {' AND '.join(must_parts)}"

        # Do-not-include: NOT, all fields
        exclude_parts = []
        for term in (do_not_include or []):
            if term.strip():
                exclude_parts.append(f'"{term.strip()}"')
        if exclude_parts:
            main_query = f"{main_query} NOT ({' OR '.join(exclude_parts)})"

        # Publication type filters
        if pub_types:
            pub_clause = '(' + ' OR '.join(pub_types) + ')'
            if pub_type_exclude:
                main_query = f"{main_query} NOT {pub_clause}"
            else:
                main_query = f"{main_query} AND {pub_clause}"

        # Add date filter
        from datetime import datetime, timedelta
        since_date = (datetime.now() - timedelta(days=since_days)).strftime("%Y/%m/%d")
        full_query = f"{main_query} AND {since_date}:3000[Date - Publication]"

        logger.info(f"PubMed search query: {full_query}")

        # Step 1: esearch — fetch a larger pool so already-seen articles
        # don't permanently block newer ones from being processed.
        # We request up to 10× max_results (capped at 500 without API key,
        # 1000 with one) then filter out already-seen PMIDs before efetch.
        pool_size = min(500 if not self._api_key else 1000,
                        max(self.max_results * 10, 200))
        search_params = {
            "db": "pubmed",
            "term": full_query,
            "retmax": pool_size,
            "retmode": "json",
            "sort": "relevance",
        }
        if self._api_key:
            search_params["api_key"] = self._api_key

        try:
            resp = self._get_with_retry(f"{BASE_URL}/esearch.fcgi", search_params)
            data = resp.json()
        except Exception as e:
            logger.error(f"PubMed search error: {e}")
            return []

        id_list = data.get("esearchresult", {}).get("idlist", [])
        if not id_list:
            logger.info("PubMed search returned no results")
            return []

        total_found = len(id_list)
        # Filter out already-seen PMIDs before efetch
        if exclude_pmids:
            id_list = [p for p in id_list if p not in exclude_pmids]
            skipped = total_found - len(id_list)
            if skipped:
                logger.info(f"Skipped {skipped} already-seen articles; "
                            f"{len(id_list)} new articles available")
        # Cap at max_results for efetch
        id_list = id_list[:self.max_results]
        logger.info(f"PubMed search found {total_found} articles total, "
                    f"fetching {len(id_list)} new ones")

        # Step 2: fetch - get full details
        articles = []
        total = len(id_list)
        for i in range(0, total, self.batch_size):
            batch = id_list[i:i + self.batch_size]
            articles.extend(self._fetch_batch(batch))
            self._rate_limit()

        return articles

    @staticmethod
    def _split_top_level_and(query: str) -> List[str]:
        """Split a PubMed query on AND only at the top level (not inside parentheses).

        Returns a list of clauses. If there is only one clause, returns [query].
        """
        parts: List[str] = []
        depth = 0
        current: List[str] = []
        i = 0
        while i < len(query):
            ch = query[i]
            if ch == '(':
                depth += 1
                current.append(ch)
            elif ch == ')':
                depth -= 1
                current.append(ch)
            elif depth == 0 and query[i:i+5] == ' AND ':
                parts.append(''.join(current).strip())
                current = []
                i += 4  # skip ' AND' (loop will advance past the trailing space)
            else:
                current.append(ch)
            i += 1
        tail = ''.join(current).strip()
        if tail:
            parts.append(tail)
        return parts if len(parts) > 1 else [query]

    def _run_esearch(self, full_query: str) -> List[str]:
        """Execute an esearch and return a list of PMIDs, or [] on failure/no results."""
        search_params = {
            "db": "pubmed",
            "term": full_query,
            "retmax": self.max_results,
            "retmode": "json",
            "sort": "relevance",
        }
        if self._api_key:
            search_params["api_key"] = self._api_key
        try:
            resp = self._get_with_retry(f"{BASE_URL}/esearch.fcgi", search_params)
            return resp.json().get("esearchresult", {}).get("idlist", [])
        except Exception as e:
            logger.error(f"PubMed esearch error: {e}")
            return []

    def search_with_query(self, raw_query: str, since_days: int = 7,
                          exclude_pmids: Optional[set] = None) -> List[Dict]:
        """Search PubMed using a pre-built query string with auto-broadening fallback.

        If the query returns 0 results, AND clauses are stripped one by one from
        the right until results are found or only the core OR block remains.
        """
        from datetime import datetime, timedelta
        since_date = (datetime.now() - timedelta(days=since_days)).strftime("%Y/%m/%d")
        date_filter = f"{since_date}:3000[Date - Publication]"

        clauses = self._split_top_level_and(raw_query)

        for attempt in range(len(clauses), 0, -1):
            candidate = " AND ".join(clauses[:attempt])
            full_query = f"({candidate}) AND {date_filter}"
            logger.info("PubMed raw query search: %s", full_query)

            id_list = self._run_esearch(full_query)
            if id_list:
                if attempt < len(clauses):
                    logger.info(
                        "Auto-broadened: removed %d AND clause(s) to find results",
                        len(clauses) - attempt,
                    )
                total_found = len(id_list)
                if exclude_pmids:
                    id_list = [p for p in id_list if p not in exclude_pmids]
                    skipped = total_found - len(id_list)
                    if skipped:
                        logger.info(f"Skipped {skipped} already-seen articles; "
                                    f"{len(id_list)} new articles available")
                id_list = id_list[:self.max_results]
                logger.info("PubMed search found %d articles total, fetching %d new ones",
                            total_found, len(id_list))
                articles = []
                for i in range(0, len(id_list), self.batch_size):
                    articles.extend(self._fetch_batch(id_list[i:i + self.batch_size]))
                    self._rate_limit()
                return articles
            else:
                if attempt > 1:
                    logger.info(
                        "No results with %d AND clause(s), trying with %d...",
                        attempt, attempt - 1,
                    )

        logger.info("PubMed search returned no results after auto-broadening")
        return []

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

        logger.info(f"PubMed journal search: {query}")

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
        for i in range(0, len(id_list), self.batch_size):
            batch = id_list[i:i + self.batch_size]
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

    @staticmethod
    def _elem_text(elem) -> str:
        """Return all text content of an XML element, including child element text.

        ElementTree's .text only returns text before the first child element.
        PubMed titles and abstracts sometimes use inline markup (<i>, <b>, <sup>, etc.),
        so itertext() is needed to get the full string.
        """
        if elem is None:
            return ""
        return "".join(elem.itertext()).strip()

    def _parse_article(self, article) -> Optional[Dict]:
        """Parse a single PubMed article element."""
        try:
            # Title — use itertext() to handle inline markup in the XML
            title_elem = article.find(".//ArticleTitle")
            title = self._elem_text(title_elem)
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

            # Abstract — use itertext() per section to handle inline markup
            abstract_texts = []
            abstract_elem = article.find(".//Abstract")
            if abstract_elem is not None:
                for abst_text in abstract_elem.findall(".//AbstractText"):
                    label = abst_text.get("Label", "")
                    text = self._elem_text(abst_text)
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