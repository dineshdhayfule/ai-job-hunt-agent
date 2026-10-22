"""Lever Postings API adapter.

API docs: https://github.com/lever/postings-api
Endpoint: https://api.lever.co/v0/postings/{company}?mode=json
Auth: none required.
Rate limit: not formally documented; use reasonable intervals.

NOTE: Lever is company-specific — each company has a unique slug.
Configure company slugs via JOB_SOURCES_CONFIG or pass them directly.
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
    now_iso,
    validate_job,
    validate_url,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://api.lever.co/v0/postings"
DEFAULT_TIMEOUT = (10, 30)


class LeverAdapter(JobSourceAdapter):
    """Fetch jobs from Lever public postings API."""

    source_name = "lever"

    def __init__(self, company_slugs: list[str] | None = None) -> None:
        """Initialize with a list of Lever company slugs.

        Example slugs: ["netlify", "twilio"]
        Each slug maps to a Lever career board at jobs.lever.co/{slug}.
        """
        self.company_slugs = company_slugs or []

    def fetch_jobs(self, **kwargs: Any) -> dict[str, Any]:
        """Fetch jobs from all configured Lever boards."""
        slugs = kwargs.get("company_slugs", self.company_slugs)
        if not slugs:
            return self._make_error_result(
                "No Lever company slugs configured. "
                "Set company_slugs to fetch jobs from specific companies."
            )

        all_jobs: list[dict[str, Any]] = []
        all_errors: list[str] = []
        total_retrieved = 0

        for slug in slugs:
            result = self._fetch_company(slug)
            all_jobs.extend(result["jobs"])
            all_errors.extend(result["errors"])
            total_retrieved += result["stats"]["retrieved"]

        return self._make_result(all_jobs, all_errors, total_retrieved)

    def _fetch_company(self, company_slug: str) -> dict[str, Any]:
        """Fetch jobs from a single Lever company board."""
        url = f"{BASE_URL}/{company_slug}"
        params = {"mode": "json"}

        try:
            response = requests.get(
                url,
                params=params,
                timeout=DEFAULT_TIMEOUT,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
        except requests.exceptions.Timeout:
            return self._make_error_result(
                f"Lever board '{company_slug}' request timed out."
            )
        except requests.exceptions.ConnectionError:
            return self._make_error_result(
                f"Could not connect to Lever for '{company_slug}'."
            )
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response else "unknown"
            if status == 404:
                return self._make_error_result(
                    f"Lever company '{company_slug}' not found (404)."
                )
            return self._make_error_result(
                f"Lever board '{company_slug}' returned HTTP {status}."
            )
        except requests.exceptions.RequestException as exc:
            return self._make_error_result(
                f"Lever request failed for '{company_slug}': {exc}"
            )

        try:
            data = response.json()
        except ValueError:
            return self._make_error_result(
                f"Lever board '{company_slug}' returned invalid JSON."
            )

        if not isinstance(data, list):
            return self._make_error_result(
                f"Lever board '{company_slug}' returned unexpected format."
            )

        retrieved = len(data)
        normalized: list[dict[str, Any]] = []
        errors: list[str] = []

        for raw in data:
            try:
                job = self._normalize(raw, company_slug)
                validation_errors = validate_job(job)
                if validation_errors:
                    errors.append(
                        f"Skipped Lever job {raw.get('id', '?')}: "
                        f"{'; '.join(validation_errors)}"
                    )
                    continue
                normalized.append(job)
            except Exception as exc:
                errors.append(
                    f"Failed to normalize Lever job "
                    f"{raw.get('id', '?')}: {exc}"
                )

        return self._make_result(normalized, errors, retrieved)

    def _normalize(
        self, raw: dict[str, Any], company_slug: str,
    ) -> dict[str, Any]:
        """Convert a Lever posting to the normalized schema."""
        job = make_job_template()

        source_id = str(raw.get("id", ""))
        job["source"] = self.source_name
        job["source_job_id"] = source_id
        job["id"] = generate_stable_id(self.source_name, source_id)

        job["title"] = (raw.get("text") or "").strip()
        job["company"] = company_slug.replace("-", " ").title()

        # Location.
        categories = raw.get("categories") or {}
        job["location"] = (categories.get("location") or "").strip()

        # Description: Lever provides description and lists blocks.
        desc_text = (raw.get("descriptionPlain") or "").strip()
        # Also check additional lists if descriptionPlain is sparse.
        additional = raw.get("additional") or ""
        if additional:
            additional_text = re.sub(r"<[^>]+>", " ", additional)
            additional_text = re.sub(r"\s+", " ", additional_text).strip()
            desc_text = f"{desc_text} {additional_text}".strip()

        # Lever also has 'lists' array with content blocks.
        lists_data = raw.get("lists") or []
        for block in lists_data:
            block_text = block.get("text", "")
            block_content = block.get("content", "")
            if block_content:
                clean = re.sub(r"<[^>]+>", " ", block_content)
                clean = re.sub(r"\s+", " ", clean).strip()
                desc_text = f"{desc_text} {block_text} {clean}".strip()

        job["description"] = desc_text[:2000] if desc_text else ""

        # Extract skills.
        job["skills"] = extract_skills_from_text(desc_text)

        # Employment type from categories.
        commitment = (categories.get("commitment") or "").strip()
        title_lower = job["title"].lower()
        if commitment:
            job["employment_type"] = commitment
        elif "intern" in title_lower:
            job["employment_type"] = "Internship"
        else:
            job["employment_type"] = "Full-time"

        # Fresher eligibility: Lever doesn't provide this.
        job["fresher_eligibility"] = "unknown"
        job["experience_years"] = None

        # Dates: Lever provides createdAt as a Unix timestamp (ms).
        created_at = raw.get("createdAt")
        if isinstance(created_at, (int, float)) and created_at > 0:
            from datetime import datetime, timezone
            try:
                dt = datetime.fromtimestamp(created_at / 1000, tz=timezone.utc)
                job["posted_at"] = dt.isoformat()
            except (OSError, ValueError, OverflowError):
                job["posted_at"] = None

        # URLs.
        apply_url = validate_url(raw.get("applyUrl") or "")
        hosted_url = validate_url(raw.get("hostedUrl") or "")
        job["apply_url"] = apply_url or hosted_url or ""
        job["source_url"] = hosted_url or apply_url or ""

        now = now_iso()
        job["first_seen_at"] = now
        job["last_seen_at"] = now
        job["listing_status"] = "active"

        return job
