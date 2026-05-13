"""Parse article titles and links from journal email HTML."""

import re
import html
import warnings
from urllib.parse import unquote
from typing import List, Tuple, Dict
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


# Publisher-specific URL patterns
PUBLISHER_PATTERNS = {
    "elsevier": [
        "sciencedirect.com/science/article",
        "linkinghub.elsevier.com",
        "cell.com",
    ],
    "nature": [
        "nature.com/articles/",
    ],
    "springer": [
        "link.springer.com/article",
        "springer.com",
    ],
    "wiley": [
        "onlinelibrary.wiley.com",
    ],
    "science": [
        "science.org/doi/",
    ],
    "plos": [
        "journals.plos.org",
    ],
    "aacr": [
        "aacrjournals.org",
    ],
    "asm": [
        "journals.asm.org",
    ],
    "generic": [
        "doi.org/",
        "dx.doi.org",
    ],
}


def clean_quoted_printable(text: str) -> str:
    """Clean quoted-printable encoding artifacts from text."""
    text = text.replace("=3D", "=")
    text = text.replace("=20", " ")
    text = text.replace("=0A", "\n")
    text = text.replace("=0D", "\r")
    text = text.replace("=09", "\t")
    # Remove soft line breaks
    text = re.sub(r'=[\r\n]', '', text)
    return text


def is_article_link(href: str) -> bool:
    """Check if a URL is likely an article link."""
    if not href:
        return False
    
    href_lower = href.lower()
    
    for publisher, patterns in PUBLISHER_PATTERNS.items():
        for pattern in patterns:
            if pattern in href_lower:
                return True
    
    return False


def extract_doi_from_url(url: str) -> str:
    """Extract DOI from a URL if present."""
    # Match doi.org/... pattern
    match = re.search(r'doi\.org/(.+)', url, re.IGNORECASE)
    if match:
        return unquote(match.group(1).rstrip("/?"))
    
    # Match /doi/ pattern
    match = re.search(r'/doi/(10\.\d{4,}/[^?&\s]+)', url, re.IGNORECASE)
    if match:
        return match.group(1)
    
    # Match ScienceDirect pii
    match = re.search(r'pii/([^?&\s]+)', url, re.IGNORECASE)
    if match:
        return f"PPI:{match.group(1)}"
    
    return ""


class ArticleParser:
    """Parse journal email HTML to extract article links and metadata."""
    
    def __init__(self):
        self.seen_urls = set()
    
    def parse_email(self, html_content: str, source_email: str = "") -> List[Dict]:
        """
        Parse journal email HTML and extract article data.
        
        Returns list of dicts with title, url, doi, journal, etc.
        """
        # Clean QP encoding
        html_content = clean_quoted_printable(html_content)
        
        soup = BeautifulSoup(html_content, "lxml")
        articles = []
        self.seen_urls.clear()
        
        # Strategy 1: Find all <a> tags with article links
        links = soup.find_all("a", href=True)
        
        for link in links:
            href = link.get("href", "")
            title = link.get_text(strip=True)
            
            # Skip non-article links
            if not is_article_link(href):
                continue
            
            # Skip duplicate URLs
            if href in self.seen_urls:
                continue
            
            # Skip very short or empty titles
            if not title or len(title) < 15:
                continue
            
            # Skip navigation/utility links
            if self._is_navigation_link(title, href):
                continue
            
            self.seen_urls.add(href)
            
            article = {
                "title": html.unescape(title),
                "url": unquote(href),
                "doi": extract_doi_from_url(href),
                "journal": self._detect_journal(href, soup, link),
                "source_email": source_email,
                "authors": [],
                "abstract": "",
                "date": "",
                "article_type": "",
            }
            
            # Try to extract nearby metadata (authors, article type)
            article.update(self._extract_nearby_metadata(link))
            
            articles.append(article)
        
        # Strategy 2: If no articles found, try broader extraction
        if not articles:
            articles = self._fallback_extraction(soup, source_email)
        
        return articles
    
    def _is_navigation_link(self, title: str, href: str) -> bool:
        """Check if a link is a navigation/UI element rather than an article."""
        nav_keywords = [
            "unsubscribe", "manage preferences", "view in browser",
            "privacy policy", "terms of service", "contact us",
            "home", "about", "login", "register", "sign in",
            "read more", "continue reading", "click here",
            "forward", "share", "twitter", "facebook", "linkedin",
            "table of contents", "all articles", "previous", "next",
            "download", "pdf", "supplementary",
        ]
        
        title_lower = title.lower()
        for kw in nav_keywords:
            if kw in title_lower:
                return True
        
        # Very short titles are likely not articles
        if len(title.split()) < 3:
            return True
        
        return False
    
    def _detect_journal(self, href: str, soup: BeautifulSoup,
                        link_tag) -> str:
        """Detect journal name from URL or surrounding HTML."""
        href_lower = href.lower()
        
        # URL-based detection
        if "cell.com" in href_lower or "cellpress" in href_lower:
            return "Cell Press"
        if "nature.com" in href_lower:
            # Try to extract specific Nature journal
            match = re.search(r'nature\.com/([^/]+)/', href_lower)
            if match:
                return f"Nature {match.group(1).capitalize()}"
            return "Nature"
        if "sciencedirect" in href_lower:
            return "ScienceDirect"
        if "springer" in href_lower:
            return "Springer"
        if "science.org" in href_lower:
            return "Science"
        if "plos.org" in href_lower:
            return "PLOS"
        if "wiley" in href_lower:
            return "Wiley"
        if "pnas" in href_lower:
            return "PNAS"
        if "jbc.org" in href_lower:
            return "Journal of Biological Chemistry"
        if "aacrjournals" in href_lower:
            return "AACR Journals"
        
        # Try to find journal name in email header/subject area
        header = soup.find("title")
        if header and header.get_text():
            title_text = header.get_text()
            if ":" in title_text:
                return title_text.split(":")[0].strip()
        
        return "Unknown Journal"
    
    def _extract_nearby_metadata(self, link_tag) -> Dict[str, str]:
        """Extract metadata from elements near the article link."""
        metadata = {
            "authors": [],
            "article_type": "",
            "date": "",
        }
        
        # Look at parent element
        parent = link_tag.parent
        if parent:
            # Check for article type labels
            text = parent.get_text()
            
            type_patterns = [
                r"(Review|Article|Letter|News|Commentary|Perspective|Editorial|Research Article)",
                r"\((Original Article|Review Article|Short Article)\)",
            ]
            for pattern in type_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    metadata["article_type"] = match.group(1)
                    break
            
            # Look for author names (often in italic or specific class)
            for elem in parent.find_all(["em", "i", "span"]):
                text_content = elem.get_text(strip=True)
                if "," in text_content and len(text_content) < 100:
                    # Might be author list
                    potential_authors = [a.strip() for a in text_content.split(",")]
                    if all(len(a.split()) <= 4 for a in potential_authors):
                        metadata["authors"] = potential_authors[:5]  # Max 5 authors
        
        # Check grandparent for more context
        grandparent = link_tag.parent.parent if link_tag.parent else None
        if grandparent and not metadata["authors"]:
            gp_text = grandparent.get_text()
            # Look for author patterns: "Author et al." or "Author A, Author B"
            author_match = re.search(r'([A-Z][a-z]+\s+[A-Z][a-z]+(?:\s+et\s+al\.)?)', gp_text)
            if author_match:
                metadata["authors"] = [author_match.group(1)]
        
        return metadata
    
    def _fallback_extraction(self, soup: BeautifulSoup, source_email: str) -> List[Dict]:
        """Broader extraction strategy when primary fails."""
        articles = []
        
        # Look for any substantial <a> tag with DOI or sciencedirect
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            title = link.get_text(strip=True)
            
            if not href or not title:
                continue
            
            if len(title) < 20:
                continue
            
            if "doi" in href.lower() or "sciencedirect" in href.lower() or "journals.plos.org" in href.lower():
                if href not in self.seen_urls:
                    self.seen_urls.add(href)
                    articles.append({
                        "title": html.unescape(title),
                        "url": unquote(href),
                        "doi": extract_doi_from_url(href),
                        "journal": self._detect_journal(href, soup, link),
                        "source_email": source_email,
                        "authors": [],
                        "abstract": "",
                        "date": "",
                        "article_type": "",
                    })
        
        return articles
