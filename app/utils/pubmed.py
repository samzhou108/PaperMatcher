"""PubMed E-utilities API client for abstract fallback."""

import time
import xml.etree.ElementTree as ET
from typing import Optional, Dict, Any, List
import httpx


BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


class PubMedClient:
    """Client for NCBI PubMed E-utilities API."""
    
    def __init__(self):
        self.last_request_time = 0
        self.min_interval = 0.4  # Max ~3 requests per second
        self._client = httpx.Client(timeout=30.0)

    def close(self):
        try:
            self._client.close()
        except Exception:
            pass
    
    def _rate_limit(self):
        """Respect PubMed rate limits."""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_request_time = time.time()
    
    def search_article(self, title: str, authors: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """Search PubMed by title and return article metadata."""
        self._rate_limit()
        
        try:
            # Build search term
            term = f"{title}[Title]"
            if authors and len(authors) > 0:
                first_author = authors[0].split()[-1]  # Last name
                term += f" AND {first_author}[Author]"
            
            params = {
                "db": "pubmed",
                "term": term,
                "retmax": 3,
                "retmode": "json",
            }
            
            resp = self._client.get(f"{BASE_URL}/esearch.fcgi", params=params)
            resp.raise_for_status()
            data = resp.json()

            id_list = data.get("esearchresult", {}).get("idlist", [])

            if not id_list:
                # Try broader search with just title words
                broad_term = " ".join(title.split()[:6])
                params["term"] = f"{broad_term}[Title]"

                self._rate_limit()
                resp = self._client.get(f"{BASE_URL}/esearch.fcgi", params=params)
                resp.raise_for_status()
                data = resp.json()

                id_list = data.get("esearchresult", {}).get("idlist", [])
                
                if not id_list:
                    return None
            
            # Fetch details for the first matching PMID
            pmid = id_list[0]
            return self.fetch_article(pmid)
            
        except Exception as e:
            print(f"PubMed search error: {e}")
            return None
    
    def fetch_article(self, pmid: str) -> Optional[Dict[str, Any]]:
        """Fetch article details by PMID."""
        self._rate_limit()
        
        try:
            params = {
                "db": "pubmed",
                "id": pmid,
                "rettype": "abstract",
                "retmode": "xml",
            }
            
            resp = self._client.get(f"{BASE_URL}/efetch.fcgi", params=params)
            resp.raise_for_status()
            xml_content = resp.text
            
            return self._parse_pubmed_xml(xml_content, pmid)
            
        except Exception as e:
            print(f"PubMed fetch error for PMID {pmid}: {e}")
            return None
    
    def _parse_pubmed_xml(self, xml_content: str, pmid: str) -> Optional[Dict[str, Any]]:
        """Parse PubMed XML response."""
        try:
            root = ET.fromstring(xml_content)
            
            article = root.find(".//PubmedArticle/MedlineCitation/Article")
            if article is None:
                return None
            
            result = {
                "pmid": pmid,
                "title": "",
                "authors": [],
                "abstract": "",
                "doi": "",
                "journal": "",
                "date": "",
                "volume": None,
                "issue": None,
            }
            
            # Title
            title_elem = article.find("ArticleTitle")
            if title_elem is not None and title_elem.text:
                result["title"] = title_elem.text
            
            # Authors
            author_list = article.find("AuthorList")
            if author_list is not None:
                for author in author_list.findall("Author"):
                    last_name = author.find("LastName")
                    fore_name = author.find("ForeName")
                    
                    name_parts = []
                    if last_name is not None and last_name.text:
                        name_parts.append(last_name.text)
                    if fore_name is not None and fore_name.text:
                        name_parts.append(fore_name.text)
                    
                    if name_parts:
                        result["authors"].append(" ".join(name_parts))
                    elif author.find("CollectiveName") is not None:
                        coll_name = author.find("CollectiveName")
                        if coll_name.text:
                            result["authors"].append(coll_name.text)
            
            # Abstract
            abstract_elem = article.find("Abstract")
            if abstract_elem is not None:
                abstract_texts = []
                for abst_text in abstract_elem.findall("AbstractText"):
                    label = abst_text.get("Label", "")
                    text = "" if abst_text.text is None else abst_text.text
                    for child in abst_text:
                        if child.text:
                            text += child.text
                        if child.tail:
                            text += child.tail
                    if label:
                        abstract_texts.append(f"{label}: {text}")
                    else:
                        abstract_texts.append(text)
                result["abstract"] = " ".join(abstract_texts)
            
            # DOI
            for id_elem in root.findall(".//ArticleId"):
                if id_elem.get("IdType") == "doi":
                    result["doi"] = id_elem.text or ""
                    break
            
            # Journal
            journal_elem = article.find("Journal/Title")
            if journal_elem is not None and journal_elem.text:
                result["journal"] = journal_elem.text
            
            # Date
            pub_date = article.find("Journal/JournalIssue/PubDate")
            if pub_date is not None:
                year = pub_date.find("Year")
                month = pub_date.find("Month")
                day = pub_date.find("Day")
                
                date_parts = []
                if year is not None and year.text:
                    date_parts.append(year.text)
                if month is not None and month.text:
                    date_parts.append(month.text)
                if day is not None and day.text:
                    date_parts.append(day.text)
                
                result["date"] = "-".join(date_parts)
            
            # Volume & Issue
            volume = article.find("Journal/JournalIssue/Volume")
            issue = article.find("Journal/JournalIssue/Issue")
            if volume is not None and volume.text:
                try:
                    result["volume"] = int(volume.text)
                except ValueError:
                    pass
            if issue is not None and issue.text:
                try:
                    result["issue"] = int(issue.text)
                except ValueError:
                    pass
            
            return result
            
        except Exception as e:
            print(f"PubMed XML parse error: {e}")
            return None
