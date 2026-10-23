"""Remotive API adapter — free public remote job listings.

API docs: https://github.com/remotive-com/remote-jobs-api
Endpoint: https://remotive.com/api/remote-jobs
Rate limit: max 2 requests/minute, recommended max 4x/day.
Data delay: 24 hours behind live site.
Attribution: required (link back to Remotive listing URL).
Auth: none required.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import requests

from .base import (
    JobSourceAdapter,
    extract_skills_from_text,
    generate_stable_id,
    make_job_template,
    normalize_employment_type,
    now_iso,
    validate_job,
    validate_url,
)

logger = logging.getLogger(__name__)

API_URL = "https://remotive.com/api/remote-jobs"
DEFAULT_TIMEOUT = (10, 30)  # (connect, read) seconds


DEFAULT_HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36 JobHuntAgent/1.0"
    ),
}


class RemotiveAdapter(JobSourceAdapter):
    """Fetch remote software jobs from the Remotive public API."""

    source_name = "remotive"

    def __init__(
        self,
        category: str = "software-dev",
        search: str | None = None,
        limit: int = 50,
    ) -> None:
        self.category = category
        self.search = search
        self.limit = limit

    def fetch_jobs(self, **kwargs: Any) -> dict[str, Any]:
        """Retrieve and normalize remote jobs from Remotive."""
        category = kwargs.get("category", self.category) or "software-dev"
        params: dict[str, Any] = {"category": category}
        if self.search:
            params["search"] = self.search
        if self.limit:
            params["limit"] = self.limit

        # Allow kwargs to override defaults for tool calls.
        if "search" in kwargs:
            params["search"] = kwargs["search"]
        if "limit" in kwargs:
            params["limit"] = kwargs["limit"]

        try:
            response = requests.get(
                API_URL,
                params=params,
                timeout=DEFAULT_TIMEOUT,
                headers=DEFAULT_HEADERS,
            )
            response.raise_for_status()
        except requests.exceptions.Timeout as exc:
            logger.warning(
                "Remotive API request timed out (timeout=%s): %s",
                DEFAULT_TIMEOUT,
                exc,
            )
            return self._make_error_result(
                f"Remotive API request timed out ({type(exc).__name__}: {exc})."
            )
        except requests.exceptions.ConnectionError as exc:
            logger.warning(
                "Remotive API connection error: %s (type: %s)",
                exc,
                type(exc).__name__,
            )
            return self._make_error_result(
                f"Could not connect to Remotive API ({type(exc).__name__}: {exc})."
            )
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response else "unknown"
            logger.warning("Remotive API HTTP error %s: %s", status, exc)
            return self._make_error_result(
                f"Remotive API returned HTTP {status}."
            )
        except requests.exceptions.RequestException as exc:
            logger.warning(
                "Remotive API request failed: %s (type: %s)",
                exc,
                type(exc).__name__,
            )
            return self._make_error_result(
                f"Remotive API request failed ({type(exc).__name__}: {exc})."
            )

        try:
            data = response.json()
        except ValueError as exc:
            logger.warning("Remotive API returned invalid JSON: %s", exc)
            return self._make_error_result(
                f"Remotive API returned invalid JSON ({type(exc).__name__}: {exc})."
            )

        raw_jobs = data.get("jobs", [])
        if not isinstance(raw_jobs, list):
            logger.warning(
                "Remotive API returned unexpected data format: expected list of jobs, got %s",
                type(raw_jobs).__name__,
            )
            return self._make_error_result(
                "Remotive API returned unexpected data format."
            )

        retrieved = len(raw_jobs)
        normalized: list[dict[str, Any]] = []
        errors: list[str] = []

        for raw in raw_jobs:
            try:
                job = self._normalize(raw)
                validation_errors = validate_job(job)
                if validation_errors:
                    errors.append(
                        f"Skipped job {raw.get('id', '?')}: "
                        f"{'; '.join(validation_errors)}"
                    )
                    continue
                normalized.append(job)
            except Exception as exc:
                errors.append(
                    f"Failed to normalize job {raw.get('id', '?')}: {exc}"
                )

        return self._make_result(normalized, errors, retrieved)

    def _normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Convert a Remotive API job to the normalized schema."""
        job = make_job_template()

        source_id = str(raw.get("id", ""))
        job["source"] = self.source_name
        job["source_job_id"] = source_id
        job["id"] = generate_stable_id(self.source_name, source_id)

        job["title"] = (raw.get("title") or "").strip()
        job["company"] = (raw.get("company_name") or "").strip()

        # Remotive uses candidate_required_location for location.
        location = (raw.get("candidate_required_location") or "").strip()
        if not location:
            location = "Remote"
        job["location"] = location

        # Description: Remotive returns HTML. Strip tags for text.
        description_html = raw.get("description") or ""
        description_text = re.sub(r"<[^>]+>", " ", description_html)
        description_text = re.sub(r"\s+", " ", description_text).strip()
        # Store truncated description (avoid huge payloads).
        job["description"] = description_text[:2000] if description_text else ""

        # Extract skills from the description.
        job["skills"] = extract_skills_from_text(description_text)

        # Employment type.
        raw_type = raw.get("job_type") or ""
        job["employment_type"] = normalize_employment_type(raw_type)

        # Fresher eligibility: Remotive does not provide this.
        # We cannot infer it, so mark as unknown.
        job["fresher_eligibility"] = "unknown"
        job["experience_years"] = None

        # Dates.
        job["posted_at"] = raw.get("publication_date") or None
        job["updated_at"] = None

        # URLs.
        apply_url = validate_url(raw.get("url") or "")
        job["apply_url"] = apply_url or ""

        # Source URL is the Remotive listing page.
        remotive_url = validate_url(
            f"https://remotive.com/remote-jobs/{raw.get('category', 'software-dev')}/{source_id}"
        )
        job["source_url"] = remotive_url or job["apply_url"]

        now = now_iso()
        job["first_seen_at"] = now
        job["last_seen_at"] = now
        job["listing_status"] = "unknown"
        job["url_status"] = "url_unchecked"
        job["application_status"] = "application_status_unknown"

        return job
