"""
ahca_client.py
--------------
Fetch and search content from https://ahca.myflorida.com/.

Provides two public functions:
  - search_ahca(query)        → ranked list of relevant resource pages
  - fetch_page_content(url)   → cleaned text + links from a single page
"""

from __future__ import annotations

import re
import time
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://ahca.myflorida.com"

# Domains that are permitted for fetching (security allowlist)
ALLOWED_DOMAINS = {
    "ahca.myflorida.com",
    "apps.ahca.myflorida.com",
    "bi.ahca.myflorida.com",
    "quality.healthfinder.fl.gov",
    "b.apps.ahca.myflorida.com",
}

# ---------------------------------------------------------------------------
# Curated resource registry
# ---------------------------------------------------------------------------

KNOWN_RESOURCES: List[Dict] = [
    # ── Medicaid ────────────────────────────────────────────────────────────
    {
        "title": "Provider Services",
        "url": f"{BASE_URL}/medicaid/medicaid-policy-quality-and-operations/medicaid-operations/recipient-and-provider-assistance/provider-services.html",
        "category": "Medicaid",
        "keywords": ["provider services", "medicaid provider", "provider assistance", "provider help"],
    },
    {
        "title": "Provider Enrollment",
        "url": f"{BASE_URL}/provider/",
        "category": "Medicaid",
        "keywords": ["provider enrollment", "enroll provider", "become a provider", "new provider", "enroll in medicaid"],
    },
    {
        "title": "Statewide Medicaid Managed Care (SMMC)",
        "url": f"{BASE_URL}/medicaid/statewide_mc/index.shtml",
        "category": "Medicaid",
        "keywords": ["managed care", "smmc", "health plan", "medicaid managed care", "hmo", "managed care organization"],
    },
    {
        "title": "Florida Medicaid Preferred Drug List (PDL)",
        "url": f"{BASE_URL}/medicaid/prescribed-drugs/medicaid-pharmaceutical-therapeutics-committee/florida-medicaid-preferred-drug-list-pdl.html",
        "category": "Medicaid",
        "keywords": ["drug list", "pdl", "preferred drug", "pharmacy", "prescription", "formulary", "pharmaceutical"],
    },
    {
        "title": "Provider Reimbursement Schedules and Billing Codes",
        "url": f"{BASE_URL}/medicaid/rules/rule-59g-4.002-provider-reimbursement-schedules-and-billing-codes",
        "category": "Medicaid",
        "keywords": ["reimbursement", "billing codes", "fee schedule", "payment", "rates", "cpt", "procedure codes"],
    },
    {
        "title": "Adopted Rules – Service-Specific Policies",
        "url": f"{BASE_URL}/medicaid/rules/adopted-rules-service-specific-policies.html",
        "category": "Medicaid",
        "keywords": ["rules", "policies", "adopted rules", "regulations", "service specific", "coverage policy"],
    },
    {
        "title": "Florida Medicaid Health Care Alerts",
        "url": f"{BASE_URL}/medicaid/alerts/alerts.shtml",
        "category": "Medicaid",
        "keywords": ["alerts", "notices", "updates", "medicaid alerts", "bulletins", "notifications"],
    },
    {
        "title": "Medicaid Recipient Resources",
        "url": f"{BASE_URL}/medicaid/Information.shtml",
        "category": "Medicaid",
        "keywords": ["recipient", "beneficiary", "consumer medicaid", "member", "medicaid recipient"],
    },
    {
        "title": "Agency Dashboards (Medicaid Data)",
        "url": f"{BASE_URL}/medicaid/agency-dashboards.html",
        "category": "Medicaid",
        "keywords": ["dashboard", "data", "statistics", "reports", "analytics", "metrics"],
    },

    # ── Health Quality Assurance ─────────────────────────────────────────────
    {
        "title": "Background Screening",
        "url": f"{BASE_URL}/health-quality-assurance/bureau-of-central-services/background-screening.html",
        "category": "Health Quality Assurance",
        "keywords": ["background screening", "background check", "fingerprint", "level 2", "screening"],
    },
    {
        "title": "Background Screening Clearinghouse",
        "url": f"{BASE_URL}/health-quality-assurance/bureau-of-central-services/background-screening/clearinghouse.html",
        "category": "Health Quality Assurance",
        "keywords": ["clearinghouse", "background screening clearinghouse", "employer registration", "employee screening"],
    },
    {
        "title": "Health Quality Assurance – Licensure Forms",
        "url": f"{BASE_URL}/health-quality-assurance/",
        "category": "Health Quality Assurance",
        "keywords": ["licensure", "license", "application", "forms", "facility license", "hqa", "health quality"],
    },
    {
        "title": "Regulated Health Care Provider Resources",
        "url": f"{BASE_URL}/mchq/licensee_provider_resources.shtml",
        "category": "Health Quality Assurance",
        "keywords": ["regulated provider", "licensee", "health care provider resources", "facility regulations"],
    },
    {
        "title": "Current Regulations in ASPEN – Survey & Compliance",
        "url": f"{BASE_URL}/health-quality-assurance/bureau-of-field-operations/current-regulations-in-aspen-survey.html",
        "category": "Health Quality Assurance",
        "keywords": ["aspen", "survey", "inspection", "regulations", "compliance", "field operations", "deficiency"],
    },

    # ── Consumers & Patients ────────────────────────────────────────────────
    {
        "title": "Find a Health Care Facility (Florida Cares)",
        "url": "https://quality.healthfinder.fl.gov/",
        "category": "Consumers",
        "keywords": ["find facility", "hospital", "nursing home", "search facility", "quality ratings", "assisted living", "florida cares"],
    },
    {
        "title": "Visitation Rights",
        "url": f"{BASE_URL}/visitation.html",
        "category": "Consumers",
        "keywords": ["visitation", "visit", "patient rights", "visitation rights", "facility visit"],
    },
    {
        "title": "Rural Health Transformation Program",
        "url": f"{BASE_URL}/rural-health-transformation-program.html",
        "category": "Consumers",
        "keywords": ["rural health", "rural", "transformation", "rural program", "rural care"],
    },
    {
        "title": "Let Kids Be Kids",
        "url": f"{BASE_URL}/let-kids-be-kids.html",
        "category": "Consumers",
        "keywords": ["let kids be kids", "children", "kids", "child health", "pediatric"],
    },

    # ── Agency Administration ───────────────────────────────────────────────
    {
        "title": "About the Agency (AHCA)",
        "url": f"{BASE_URL}/about-the-agency-for-health-care-administration.html",
        "category": "Agency",
        "keywords": ["about", "ahca", "mission", "agency", "administration", "who we are", "secretary"],
    },
    {
        "title": "Latest News and Press Releases",
        "url": f"{BASE_URL}/agency-administration/chief-of-staff-office/office-of-communications.html",
        "category": "Agency",
        "keywords": ["news", "press release", "announcements", "latest news", "communications"],
    },
    {
        "title": "Public Records Request",
        "url": f"{BASE_URL}/agency-administration/chief-of-staff-office/office-of-communications/public-records.html",
        "category": "Agency",
        "keywords": ["public records", "records request", "foia", "open records", "document request"],
    },
    {
        "title": "Public Meetings",
        "url": "https://apps.ahca.myflorida.com/public-meetings",
        "category": "Agency",
        "keywords": ["public meetings", "meeting", "agenda", "calendar", "scheduled meetings"],
    },
    {
        "title": "Contact AHCA",
        "url": f"{BASE_URL}/contact-ahca.html",
        "category": "Agency",
        "keywords": ["contact", "phone", "email", "address", "help", "support", "customer service"],
    },
    {
        "title": "Careers at AHCA",
        "url": "https://jobs.myflorida.com/go/Agency-for-Health-Care-Administration/2814500/",
        "category": "Agency",
        "keywords": ["careers", "jobs", "employment", "work at ahca", "hiring"],
    },
]

# ---------------------------------------------------------------------------
# HTTP session (shared, connection-pooled)
# ---------------------------------------------------------------------------

_http = requests.Session()
_http.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
)

# Simple in-process TTL cache: url → {data, ts}
_CACHE: Dict[str, Dict] = {}
_CACHE_TTL = 600  # seconds


def _cached(url: str) -> Optional[Dict]:
    entry = _CACHE.get(url)
    if entry and (time.monotonic() - entry["ts"]) < _CACHE_TTL:
        return entry["data"]
    return None


def _store(url: str, data: Dict) -> None:
    _CACHE[url] = {"data": data, "ts": time.monotonic()}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def fetch_page_content(url: str, max_chars: int = 4000) -> Dict:
    """
    Fetch a page from the AHCA domain, strip boilerplate, and return:
      - title   : page title string
      - url     : canonical URL
      - content : cleaned plaintext (≤ max_chars)
      - links   : list of {text, url} found on the page
      - error   : set only when the request fails
    """
    # Security: enforce domain allowlist
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    if not any(domain == d or domain.endswith("." + d) for d in ALLOWED_DOMAINS):
        return {"error": f"Domain not permitted: {domain}", "url": url}

    cached = _cached(url)
    if cached:
        return cached

    try:
        resp = _http.get(url, timeout=15, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException as exc:
        return {"error": str(exc), "url": url}

    soup = BeautifulSoup(resp.text, "html.parser")

    # Page title
    title_tag = soup.find("title")
    page_title = title_tag.get_text(strip=True) if title_tag else url

    # Remove navigation/chrome noise
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "iframe"]):
        tag.decompose()

    # Prefer a <main> or content-labelled container
    main = (
        soup.find("main")
        or soup.find("article")
        or soup.find(id=re.compile(r"main|content|body", re.I))
        or soup.find("div", class_=re.compile(r"main|content|body", re.I))
    )
    content_root = main if main else soup.find("body") or soup

    # Build clean text
    raw_text = content_root.get_text(separator="\n", strip=True)
    lines = [ln.strip() for ln in raw_text.splitlines() if len(ln.strip()) > 2]
    # Deduplicate consecutive identical lines
    deduped: List[str] = []
    for line in lines:
        if not deduped or deduped[-1] != line:
            deduped.append(line)
    clean_text = "\n".join(deduped)[:max_chars]

    # Extract internal links
    links: List[Dict] = []
    seen_urls: set = set()
    for anchor in content_root.find_all("a", href=True):
        href: str = anchor["href"].strip()
        link_text = anchor.get_text(strip=True)
        if not link_text or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        full_url = urljoin(url, href)
        parsed_link = urlparse(full_url)
        if full_url not in seen_urls and any(
            parsed_link.netloc.endswith(d) for d in ALLOWED_DOMAINS
        ):
            seen_urls.add(full_url)
            links.append({"text": link_text[:120], "url": full_url})
        if len(links) >= 25:
            break

    result: Dict = {
        "title": page_title,
        "url": url,
        "content": clean_text,
        "links": links,
    }
    _store(url, result)
    return result


def search_ahca(query: str, max_results: int = 8) -> Dict:
    """
    Search the curated AHCA resource registry for pages relevant to *query*.

    Returns:
      - query   : original query string
      - results : list of {title, url, category}
      - total   : number of results returned
    """
    query_lower = query.lower()
    tokens = [t for t in re.split(r"\W+", query_lower) if len(t) > 2]

    scored: List[tuple] = []
    for resource in KNOWN_RESOURCES:
        score = 0
        haystack = (
            resource["title"] + " "
            + " ".join(resource["keywords"]) + " "
            + resource["category"]
        ).lower()

        # Exact phrase match (high value)
        if query_lower in haystack:
            score += 12

        # Keyword list matches
        for kw in resource["keywords"]:
            if kw in query_lower:
                score += 6
            elif any(t in kw for t in tokens):
                score += 2

        # Token-level match against haystack
        for token in tokens:
            if token in haystack:
                score += 1

        if score > 0:
            scored.append((score, resource))

    scored.sort(key=lambda x: -x[0])
    top = [r for _, r in scored[:max_results]]

    # Fallback: return all categories overview
    if not top:
        categories = {}
        for r in KNOWN_RESOURCES:
            if r["category"] not in categories:
                categories[r["category"]] = r
        top = list(categories.values())[:max_results]

    return {
        "query": query,
        "results": [
            {"title": r["title"], "url": r["url"], "category": r["category"]}
            for r in top
        ],
        "total": len(top),
    }
