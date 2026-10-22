"""Greenhouse Job Board API adapter.

API docs: https://developers.greenhouse.io/job-board.html
Endpoint: https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs
Auth: none required for GET endpoints.
Rate limit: not formally documented; use reasonable intervals.

NOTE: Greenhouse is company-specific — each company has a unique board
token. The adapter requires at least one board token to fetch jobs.
Configure board tokens via the JOB_SOURCES_CONFIG or pass them directly.
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

BASE_URL = "https://boards-api.greenhouse.io/v1/boards"
DEFAULT_TIMEOUT = (10, 30)


class GreenhouseAdapter(JobSourceAdapter):
    """Fetch jobs from Greenhouse public job board API."""

    source_name = "greenhouse"

    def __init__(self, board_tokens: list[str] | None = None) -> None:
        """Initialize with a list of company board tokens.

        Example tokens: ["vaulttec", "example"]
        Each token maps to a Greenhouse career board.
        """
        self.board_tokens = board_tokens or []

    def fetch_jobs(self, **kwargs: Any) -> dict[str, Any]:
        """Fetch jobs from all configured boards."""
        tokens = kwargs.get("board_tokens", self.board_tokens)
        if not tokens:
            return self._make_error_result(
                "No Greenhouse board tokens configured. "
                "Set board_tokens to fetch jobs from specific companies."
            )

        all_jobs: list[dict[str, Any]] = []
        all_errors: list[str] = []
        total_retrieved = 0

        for token in tokens:
            result = self._fetch_board(token)
            all_jobs.extend(result["jobs"])
            all_errors.extend(result["errors"])
            total_retrieved += result["stats"]["retrieved"]

        return self._make_result(all_jobs, all_errors, total_retrieved)

    def _fetch_board(self, board_token: str) -> dict[str, Any]:
        """Fetch jobs from a single Greenhouse board."""
        url = f"{BASE_URL}/{board_token}/jobs"
        params = {"content": "true"}  # Include job descriptions.

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
                f"Greenhouse board '{board_token}' request timed out."
            )
        except requests.exceptions.ConnectionError:
            return self._make_error_result(
                f"Could not connect to Greenhouse for board '{board_token}'."
            )
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response else "unknown"
            if status == 404:
                return self._make_error_result(
                    f"Greenhouse board '{board_token}' not found (404)."
                )
            return self._make_error_result(
                f"Greenhouse board '{board_token}' returned HTTP {status}."
            )
        except requests.exceptions.RequestException as exc:
            return self._make_error_result(
                f"Greenhouse request failed for '{board_token}': {exc}"
            )

        try:
            data = response.json()
        except ValueError:
            return self._make_error_result(
                f"Greenhouse board '{board_token}' returned invalid JSON."
            )

        raw_jobs = data.get("jobs", [])
        if not isinstance(raw_jobs, list):
            return self._make_error_result(
                f"Greenhouse board '{board_token}' returned unexpected format."
            )

        retrieved = len(raw_jobs)
        normalized: list[dict[str, Any]] = []
        errors: list[str] = []

        for raw in raw_jobs:
            try:
                job = self._normalize(raw, board_token)
                validation_errors = validate_job(job)
                if validation_errors:
                    errors.append(
                        f"Skipped Greenhouse job {raw.get('id', '?')}: "
                        f"{'; '.join(validation_errors)}"
                    )
                    continue
                normalized.append(job)
            except Exception as exc:
                errors.append(
                    f"Failed to normalize Greenhouse job "
                    f"{raw.get('id', '?')}: {exc}"
                )

        return self._make_result(normalized, errors, retrieved)

    def _normalize(
        self, raw: dict[str, Any], board_token: str,
    ) -> dict[str, Any]:
        """Convert a Greenhouse job to the normalized schema."""
        job = make_job_template()

        source_id = str(raw.get("id", ""))
        job["source"] = self.source_name
        job["source_job_id"] = source_id
        job["id"] = generate_stable_id(self.source_name, source_id)

        job["title"] = (raw.get("title") or "").strip()

        # Company name is not directly in the job; infer from board token
        # or metadata if available. Greenhouse doesn't include company
        # name in the individual job response.
        job["company"] = board_token.replace("-", " ").title()

        # Location.
        location_obj = raw.get("location") or {}
        job["location"] = (location_obj.get("name") or "").strip()

        # Description: HTML content block.
        content = raw.get("content") or ""
        description_text = re.sub(r"<[^>]+>", " ", content)
        description_text = re.sub(r"\s+", " ", description_text).strip()
        job["description"] = description_text[:2000] if description_text else ""

        # Extract skills.
        job["skills"] = extract_skills_from_text(description_text)

        # Employment type: not a standard Greenhouse field.
        # Try to infer from title or departments.
        title_lower = job["title"].lower()
        if "intern" in title_lower:
            job["employment_type"] = "Internship"
        elif "contract" in title_lower or "contractor" in title_lower:
            job["employment_type"] = "Contract"
        elif "part-time" in title_lower or "part time" in title_lower:
            job["employment_type"] = "Part-time"
        else:
            job["employment_type"] = "Full-time"

        # Fresher eligibility: Greenhouse doesn't provide this.
        job["fresher_eligibility"] = "unknown"
        job["experience_years"] = None

        # Dates.
        job["updated_at"] = raw.get("updated_at") or None
        job["posted_at"] = None  # Not provided in board API.

        # URLs.
        absolute_url = validate_url(raw.get("absolute_url") or "")
        job["apply_url"] = absolute_url or ""
        job["source_url"] = absolute_url or ""

        now = now_iso()
        job["first_seen_at"] = now
        job["last_seen_at"] = now
        job["listing_status"] = "active"

        return job
