"""Base interface and shared utilities for job source adapters."""

from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Normalized job schema
# ---------------------------------------------------------------------------

REQUIRED_JOB_FIELDS = {"id", "source", "title", "company", "apply_url"}

VALID_EMPLOYMENT_TYPES = {
    "full-time", "part-time", "contract", "internship", "freelance",
}

VALID_ELIGIBILITY_STATES = {"confirmed_fresher", "confirmed_experience", "unknown"}

VALID_LISTING_STATUSES = {"active", "closed", "unknown"}

VALID_URL_STATUSES = {"url_unchecked", "url_reachable", "url_unreachable"}

VALID_APPLICATION_STATUSES = {"application_status_unknown", "open_confirmed", "closed"}


def make_job_template() -> dict[str, Any]:
    """Return a normalized job record with all fields set to defaults."""
    return {
        "id": "",
        "source": "",
        "source_job_id": "",
        "title": "",
        "company": "",
        "location": "",
        "description": "",
        "skills": [],
        "experience_years": None,
        "employment_type": "",
        "fresher_eligibility": "unknown",
        "posted_at": None,
        "updated_at": None,
        "apply_url": "",
        "source_url": "",
        "first_seen_at": None,
        "last_seen_at": None,
        "listing_status": "unknown",
        "url_status": "url_unchecked",
        "application_status": "application_status_unknown",
    }


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def validate_url(url: str) -> str | None:
    """Return the URL if it uses an allowed scheme, else None."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if not parsed.netloc:
        return None
    return url


def verify_application_url(
    url: str,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Verify application URL reachability with redirect following and safe fallbacks.

    Returns dict with:
        "url_status": "url_reachable" | "url_unreachable" | "url_unchecked"
        "http_status": int | None
        "final_url": str
        "application_status": "application_status_unknown" | "open_confirmed" | "closed"
        "checked_at": str (ISO timestamp)
        "error": str | None
    """
    valid_url = validate_url(url)
    now = now_iso()
    if not valid_url:
        return {
            "url_status": "url_unreachable",
            "http_status": None,
            "final_url": url or "",
            "application_status": "closed",
            "checked_at": now,
            "error": "Invalid URL scheme or format",
        }

    import requests

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36 JobHuntAgent/1.0"
        ),
        "Accept": "*/*",
    }

    try:
        # First attempt: lightweight HEAD request
        response = requests.head(
            valid_url,
            timeout=timeout,
            allow_redirects=True,
            headers=headers,
        )
        # If method not allowed or forbidden on HEAD, fall back to streaming GET
        if response.status_code in (403, 405, 501):
            response = requests.get(
                valid_url,
                timeout=timeout,
                allow_redirects=True,
                stream=True,
                headers=headers,
            )
            response.close()

        status_code = response.status_code
        final_url = response.url

        if 200 <= status_code < 400:
            return {
                "url_status": "url_reachable",
                "http_status": status_code,
                "final_url": final_url,
                "application_status": "application_status_unknown",
                "checked_at": now,
                "error": None,
            }
        elif status_code in (404, 410):
            return {
                "url_status": "url_unreachable",
                "http_status": status_code,
                "final_url": final_url,
                "application_status": "closed",
                "checked_at": now,
                "error": f"HTTP {status_code} Not Found/Gone",
            }
        else:
            return {
                "url_status": "url_unreachable",
                "http_status": status_code,
                "final_url": final_url,
                "application_status": "application_status_unknown",
                "checked_at": now,
                "error": f"HTTP {status_code}",
            }
    except requests.exceptions.Timeout:
        return {
            "url_status": "url_unreachable",
            "http_status": None,
            "final_url": valid_url,
            "application_status": "application_status_unknown",
            "checked_at": now,
            "error": "Request timed out",
        }
    except requests.exceptions.RequestException as exc:
        return {
            "url_status": "url_unreachable",
            "http_status": None,
            "final_url": valid_url,
            "application_status": "application_status_unknown",
            "checked_at": now,
            "error": str(exc),
        }


def generate_stable_id(source: str, source_job_id: str) -> str:
    """Create a deterministic ID from source and provider job ID."""
    raw = f"{source}:{source_job_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def generate_fallback_id(
    company: str, title: str, location: str, apply_url: str,
) -> str:
    """Create a conservative fallback ID when no stable provider ID exists."""
    parts = [
        company.strip().lower(),
        title.strip().lower(),
        location.strip().lower(),
        apply_url.strip().lower(),
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def validate_job(job: dict[str, Any]) -> list[str]:
    """Return a list of validation errors for a normalized job record."""
    errors: list[str] = []

    for field in REQUIRED_JOB_FIELDS:
        value = job.get(field)
        if not value or not isinstance(value, str) or not value.strip():
            errors.append(f"Missing or empty required field: {field}")

    url = job.get("apply_url", "")
    if url and not validate_url(url):
        errors.append(f"Invalid apply_url scheme: {url!r}")

    source_url = job.get("source_url", "")
    if source_url and not validate_url(source_url):
        errors.append(f"Invalid source_url scheme: {source_url!r}")

    eligibility = job.get("fresher_eligibility", "unknown")
    if eligibility not in VALID_ELIGIBILITY_STATES:
        errors.append(
            f"Invalid fresher_eligibility: {eligibility!r}"
        )

    listing_status = job.get("listing_status", "active")
    if listing_status not in VALID_LISTING_STATUSES:
        errors.append(
            f"Invalid listing_status: {listing_status!r}"
        )

    return errors


def normalize_employment_type(raw: str) -> str:
    """Normalize an employment type string to a canonical value."""
    if not raw or not isinstance(raw, str):
        return ""
    cleaned = raw.strip().lower().replace("_", "-").replace(" ", "-")
    # Map common variants.
    mapping = {
        "full-time": "Full-time",
        "fulltime": "Full-time",
        "part-time": "Part-time",
        "parttime": "Part-time",
        "contract": "Contract",
        "contractor": "Contract",
        "internship": "Internship",
        "intern": "Internship",
        "freelance": "Freelance",
    }
    return mapping.get(cleaned, raw.strip())


def extract_skills_from_text(text: str) -> list[str]:
    """Extract likely skill keywords from a job description.

    This is a simple keyword extraction approach. It matches against
    a curated set of common tech skills found in Indian software job
    listings. It will not catch every skill but avoids false positives.
    """
    if not text or not isinstance(text, str):
        return []

    # Common tech skills to look for (case-insensitive patterns).
    skill_patterns: dict[str, re.Pattern[str]] = {
        "Java": re.compile(r"\bjava\b(?!\s*script)", re.IGNORECASE),
        "Python": re.compile(r"\bpython\b", re.IGNORECASE),
        "JavaScript": re.compile(r"\bjavascript\b|\bjs\b", re.IGNORECASE),
        "TypeScript": re.compile(r"\btypescript\b|\bts\b", re.IGNORECASE),
        "C++": re.compile(r"\bc\+\+\b", re.IGNORECASE),
        "C#": re.compile(r"\bc#\b", re.IGNORECASE),
        "C": re.compile(r"\b[Cc]\b(?!\+|\#|\.)", re.IGNORECASE),
        "React": re.compile(r"\breact(?:\.?js)?\b", re.IGNORECASE),
        "Node.js": re.compile(r"\bnode\.?js\b|\bnode\b", re.IGNORECASE),
        "Express": re.compile(r"\bexpress(?:\.?js)?\b", re.IGNORECASE),
        "Next.js": re.compile(r"\bnext\.?js\b", re.IGNORECASE),
        "Angular": re.compile(r"\bangular\b", re.IGNORECASE),
        "Vue.js": re.compile(r"\bvue\.?js\b|\bvue\b", re.IGNORECASE),
        "Django": re.compile(r"\bdjango\b", re.IGNORECASE),
        "Flask": re.compile(r"\bflask\b", re.IGNORECASE),
        "FastAPI": re.compile(r"\bfastapi\b", re.IGNORECASE),
        "Spring Boot": re.compile(r"\bspring\s*boot\b", re.IGNORECASE),
        "Spring": re.compile(r"\bspring\b", re.IGNORECASE),
        "HTML": re.compile(r"\bhtml5?\b", re.IGNORECASE),
        "CSS": re.compile(r"\bcss3?\b", re.IGNORECASE),
        "Bootstrap": re.compile(r"\bbootstrap\b", re.IGNORECASE),
        "Tailwind": re.compile(r"\btailwind\b", re.IGNORECASE),
        "MySQL": re.compile(r"\bmysql\b", re.IGNORECASE),
        "PostgreSQL": re.compile(r"\bpostgres(?:ql)?\b", re.IGNORECASE),
        "MongoDB": re.compile(r"\bmongodb\b|\bmongo\b", re.IGNORECASE),
        "Redis": re.compile(r"\bredis\b", re.IGNORECASE),
        "Firebase": re.compile(r"\bfirebase\b", re.IGNORECASE),
        "Oracle": re.compile(r"\boracle\b", re.IGNORECASE),
        "SQL": re.compile(r"\bsql\b", re.IGNORECASE),
        "Git": re.compile(r"\bgit\b(?!\s*hub)", re.IGNORECASE),
        "GitHub": re.compile(r"\bgithub\b", re.IGNORECASE),
        "Docker": re.compile(r"\bdocker\b", re.IGNORECASE),
        "Kubernetes": re.compile(r"\bkubernetes\b|\bk8s\b", re.IGNORECASE),
        "AWS": re.compile(r"\baws\b", re.IGNORECASE),
        "Azure": re.compile(r"\bazure\b", re.IGNORECASE),
        "GCP": re.compile(r"\bgcp\b|\bgoogle\s+cloud\b", re.IGNORECASE),
        "REST APIs": re.compile(r"\brest\s*api", re.IGNORECASE),
        "GraphQL": re.compile(r"\bgraphql\b", re.IGNORECASE),
        "Linux": re.compile(r"\blinux\b", re.IGNORECASE),
        "Go": re.compile(r"\bgolang\b|\bgo\b", re.IGNORECASE),
        "Rust": re.compile(r"\brust\b", re.IGNORECASE),
        "Kotlin": re.compile(r"\bkotlin\b", re.IGNORECASE),
        "Swift": re.compile(r"\bswift\b", re.IGNORECASE),
        "Ruby": re.compile(r"\bruby\b", re.IGNORECASE),
        "PHP": re.compile(r"\bphp\b", re.IGNORECASE),
        "Scala": re.compile(r"\bscala\b", re.IGNORECASE),
    }

    found: list[str] = []
    for skill_name, pattern in skill_patterns.items():
        if pattern.search(text):
            found.append(skill_name)

    return found


def now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Abstract base class for job source adapters
# ---------------------------------------------------------------------------

class JobSourceAdapter(ABC):
    """Base class for all job source adapters.

    Each adapter retrieves postings from a single provider, parses the
    response, and returns normalized job records.
    """

    # Subclasses must set these.
    source_name: str = ""

    @abstractmethod
    def fetch_jobs(self, **kwargs: Any) -> dict[str, Any]:
        """Fetch and return normalized jobs from this source.

        Returns a dict with:
            "jobs":      list of normalized job dicts
            "errors":    list of error messages (non-fatal)
            "stats": {
                "retrieved": int,
                "valid":     int,
                "rejected":  int,
            }
        """
        ...

    def _make_result(
        self,
        jobs: list[dict[str, Any]],
        errors: list[str],
        retrieved: int,
    ) -> dict[str, Any]:
        """Build a standard result envelope."""
        valid = len(jobs)
        return {
            "jobs": jobs,
            "errors": errors,
            "stats": {
                "retrieved": retrieved,
                "valid": valid,
                "rejected": retrieved - valid,
            },
        }

    def _make_error_result(self, error_message: str) -> dict[str, Any]:
        """Build a result envelope for a complete failure."""
        return {
            "jobs": [],
            "errors": [error_message],
            "stats": {
                "retrieved": 0,
                "valid": 0,
                "rejected": 0,
            },
        }
