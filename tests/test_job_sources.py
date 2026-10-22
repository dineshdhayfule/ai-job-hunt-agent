"""Tests for job source adapters and deduplication."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock
from typing import Any

import requests

from tools.job_sources.base import (
    validate_url,
    generate_stable_id,
    generate_fallback_id,
    validate_job,
    normalize_employment_type,
    extract_skills_from_text,
    make_job_template,
)
from tools.job_sources.registry import deduplicate_jobs


# ---------------------------------------------------------------------------
# Base module tests
# ---------------------------------------------------------------------------

class TestValidateUrl(unittest.TestCase):

    def test_valid_https(self):
        self.assertEqual(
            validate_url("https://example.com/jobs/1"),
            "https://example.com/jobs/1",
        )

    def test_valid_http(self):
        self.assertEqual(
            validate_url("http://example.com"),
            "http://example.com",
        )

    def test_rejects_javascript(self):
        self.assertIsNone(validate_url("javascript:alert(1)"))

    def test_rejects_empty(self):
        self.assertIsNone(validate_url(""))

    def test_rejects_none(self):
        self.assertIsNone(validate_url(None))

    def test_rejects_ftp(self):
        self.assertIsNone(validate_url("ftp://example.com/file"))

    def test_rejects_no_netloc(self):
        self.assertIsNone(validate_url("https://"))

    def test_strips_whitespace(self):
        self.assertEqual(
            validate_url("  https://example.com  "),
            "https://example.com",
        )


class TestGenerateIds(unittest.TestCase):

    def test_stable_id_deterministic(self):
        id1 = generate_stable_id("remotive", "12345")
        id2 = generate_stable_id("remotive", "12345")
        self.assertEqual(id1, id2)

    def test_stable_id_differs_by_source(self):
        id1 = generate_stable_id("remotive", "12345")
        id2 = generate_stable_id("greenhouse", "12345")
        self.assertNotEqual(id1, id2)

    def test_fallback_id_deterministic(self):
        id1 = generate_fallback_id("Corp", "Dev", "Mumbai", "https://x.com")
        id2 = generate_fallback_id("Corp", "Dev", "Mumbai", "https://x.com")
        self.assertEqual(id1, id2)


class TestValidateJob(unittest.TestCase):

    def test_valid_job(self):
        job = make_job_template()
        job.update({
            "id": "abc123",
            "source": "remotive",
            "title": "Dev",
            "company": "Corp",
            "apply_url": "https://example.com/apply",
        })
        errors = validate_job(job)
        self.assertEqual(errors, [])

    def test_missing_required_field(self):
        job = make_job_template()
        job["id"] = "abc123"
        # Missing title, company, source, apply_url.
        errors = validate_job(job)
        self.assertTrue(len(errors) > 0)

    def test_invalid_apply_url(self):
        job = make_job_template()
        job.update({
            "id": "abc123",
            "source": "test",
            "title": "Dev",
            "company": "Corp",
            "apply_url": "javascript:void(0)",
        })
        errors = validate_job(job)
        self.assertTrue(any("apply_url" in e for e in errors))

    def test_invalid_eligibility(self):
        job = make_job_template()
        job.update({
            "id": "abc123",
            "source": "test",
            "title": "Dev",
            "company": "Corp",
            "apply_url": "https://example.com",
            "fresher_eligibility": "maybe",
        })
        errors = validate_job(job)
        self.assertTrue(any("fresher_eligibility" in e for e in errors))


class TestNormalizeEmploymentType(unittest.TestCase):

    def test_full_time(self):
        self.assertEqual(normalize_employment_type("full-time"), "Full-time")

    def test_fulltime_no_dash(self):
        self.assertEqual(normalize_employment_type("fulltime"), "Full-time")

    def test_internship(self):
        self.assertEqual(normalize_employment_type("internship"), "Internship")

    def test_intern(self):
        self.assertEqual(normalize_employment_type("intern"), "Internship")

    def test_empty(self):
        self.assertEqual(normalize_employment_type(""), "")

    def test_unknown(self):
        self.assertEqual(normalize_employment_type("fellowship"), "fellowship")


class TestExtractSkills(unittest.TestCase):

    def test_extracts_python(self):
        skills = extract_skills_from_text("We need a Python developer")
        self.assertIn("Python", skills)

    def test_extracts_java_not_javascript(self):
        skills = extract_skills_from_text("Java developer needed")
        self.assertIn("Java", skills)
        self.assertNotIn("JavaScript", skills)

    def test_extracts_javascript(self):
        skills = extract_skills_from_text("JavaScript and TypeScript")
        self.assertIn("JavaScript", skills)
        self.assertIn("TypeScript", skills)

    def test_extracts_react(self):
        skills = extract_skills_from_text("React.js frontend framework")
        self.assertIn("React", skills)

    def test_empty_input(self):
        self.assertEqual(extract_skills_from_text(""), [])

    def test_none_input(self):
        self.assertEqual(extract_skills_from_text(None), [])


# ---------------------------------------------------------------------------
# Deduplication tests
# ---------------------------------------------------------------------------

class TestDeduplication(unittest.TestCase):

    def test_no_duplicates(self):
        jobs = [
            {"id": "a", "title": "Dev1"},
            {"id": "b", "title": "Dev2"},
        ]
        result, dups = deduplicate_jobs(jobs)
        self.assertEqual(len(result), 2)
        self.assertEqual(dups, 0)

    def test_removes_duplicates(self):
        jobs = [
            {"id": "a", "title": "Dev1"},
            {"id": "a", "title": "Dev1 Updated"},
            {"id": "b", "title": "Dev2"},
        ]
        result, dups = deduplicate_jobs(jobs)
        self.assertEqual(len(result), 2)
        self.assertEqual(dups, 1)

    def test_preserves_first_occurrence(self):
        jobs = [
            {"id": "a", "title": "Original"},
            {"id": "a", "title": "Duplicate"},
        ]
        result, dups = deduplicate_jobs(jobs)
        self.assertEqual(result[0]["title"], "Original")

    def test_empty_list(self):
        result, dups = deduplicate_jobs([])
        self.assertEqual(result, [])
        self.assertEqual(dups, 0)

    def test_jobs_without_ids(self):
        jobs = [
            {"id": "", "title": "No ID 1"},
            {"id": "", "title": "No ID 2"},
        ]
        result, dups = deduplicate_jobs(jobs)
        self.assertEqual(len(result), 2)


# ---------------------------------------------------------------------------
# Remotive adapter tests (mocked HTTP)
# ---------------------------------------------------------------------------

class TestRemotiveAdapter(unittest.TestCase):

    def _make_mock_response(
        self,
        status_code: int = 200,
        json_data: Any = None,
        raise_for_status: Exception | None = None,
    ) -> MagicMock:
        mock = MagicMock()
        mock.status_code = status_code
        mock.json.return_value = json_data or {"jobs": []}
        if raise_for_status:
            mock.raise_for_status.side_effect = raise_for_status
        return mock

    def _sample_remotive_job(self, **overrides) -> dict:
        job = {
            "id": 12345,
            "title": "Python Backend Developer",
            "company_name": "TechStartup",
            "candidate_required_location": "India",
            "job_type": "full_time",
            "url": "https://remotive.com/remote-jobs/12345",
            "publication_date": "2026-10-01T00:00:00",
            "description": "<p>Python, Django, REST APIs experience.</p>",
            "category": "software-dev",
        }
        job.update(overrides)
        return job

    @patch("tools.job_sources.remotive.requests.get")
    def test_successful_fetch(self, mock_get):
        from tools.job_sources.remotive import RemotiveAdapter

        mock_get.return_value = self._make_mock_response(
            json_data={"jobs": [self._sample_remotive_job()]},
        )

        adapter = RemotiveAdapter()
        result = adapter.fetch_jobs()

        self.assertEqual(len(result["jobs"]), 1)
        self.assertEqual(result["stats"]["retrieved"], 1)
        self.assertEqual(result["stats"]["valid"], 1)
        job = result["jobs"][0]
        self.assertEqual(job["source"], "remotive")
        self.assertEqual(job["company"], "TechStartup")
        self.assertIn("Python", job["skills"])
        self.assertEqual(job["fresher_eligibility"], "unknown")

    @patch("tools.job_sources.remotive.requests.get")
    def test_empty_results(self, mock_get):
        from tools.job_sources.remotive import RemotiveAdapter

        mock_get.return_value = self._make_mock_response(
            json_data={"jobs": []},
        )

        adapter = RemotiveAdapter()
        result = adapter.fetch_jobs()
        self.assertEqual(result["jobs"], [])
        self.assertEqual(result["stats"]["retrieved"], 0)

    @patch("tools.job_sources.remotive.requests.get")
    def test_timeout_error(self, mock_get):
        from tools.job_sources.remotive import RemotiveAdapter

        mock_get.side_effect = requests.exceptions.Timeout()

        adapter = RemotiveAdapter()
        result = adapter.fetch_jobs()
        self.assertEqual(result["jobs"], [])
        self.assertTrue(any("timed out" in e for e in result["errors"]))

    @patch("tools.job_sources.remotive.requests.get")
    def test_connection_error(self, mock_get):
        from tools.job_sources.remotive import RemotiveAdapter

        mock_get.side_effect = requests.exceptions.ConnectionError()

        adapter = RemotiveAdapter()
        result = adapter.fetch_jobs()
        self.assertEqual(result["jobs"], [])
        self.assertTrue(any("connect" in e.lower() for e in result["errors"]))

    @patch("tools.job_sources.remotive.requests.get")
    def test_http_500(self, mock_get):
        from tools.job_sources.remotive import RemotiveAdapter

        error_response = MagicMock()
        error_response.status_code = 500
        mock_get.side_effect = requests.exceptions.HTTPError(
            response=error_response,
        )

        adapter = RemotiveAdapter()
        result = adapter.fetch_jobs()
        self.assertEqual(result["jobs"], [])
        self.assertTrue(any("500" in e for e in result["errors"]))

    @patch("tools.job_sources.remotive.requests.get")
    def test_http_429_rate_limit(self, mock_get):
        from tools.job_sources.remotive import RemotiveAdapter

        error_response = MagicMock()
        error_response.status_code = 429
        mock_get.side_effect = requests.exceptions.HTTPError(
            response=error_response,
        )

        adapter = RemotiveAdapter()
        result = adapter.fetch_jobs()
        self.assertEqual(result["jobs"], [])
        self.assertTrue(any("429" in e for e in result["errors"]))

    @patch("tools.job_sources.remotive.requests.get")
    def test_invalid_json(self, mock_get):
        from tools.job_sources.remotive import RemotiveAdapter

        mock = MagicMock()
        mock.status_code = 200
        mock.raise_for_status.return_value = None
        mock.json.side_effect = ValueError("Invalid JSON")
        mock_get.return_value = mock

        adapter = RemotiveAdapter()
        result = adapter.fetch_jobs()
        self.assertEqual(result["jobs"], [])
        self.assertTrue(any("JSON" in e for e in result["errors"]))

    @patch("tools.job_sources.remotive.requests.get")
    def test_missing_url_field(self, mock_get):
        from tools.job_sources.remotive import RemotiveAdapter

        job = self._sample_remotive_job(url="")
        mock_get.return_value = self._make_mock_response(
            json_data={"jobs": [job]},
        )

        adapter = RemotiveAdapter()
        result = adapter.fetch_jobs()
        # Should be rejected due to missing apply_url.
        self.assertEqual(result["stats"]["rejected"], 1)

    @patch("tools.job_sources.remotive.requests.get")
    def test_missing_posting_date(self, mock_get):
        from tools.job_sources.remotive import RemotiveAdapter

        job = self._sample_remotive_job(publication_date=None)
        mock_get.return_value = self._make_mock_response(
            json_data={"jobs": [job]},
        )

        adapter = RemotiveAdapter()
        result = adapter.fetch_jobs()
        if result["jobs"]:
            self.assertIsNone(result["jobs"][0]["posted_at"])


# ---------------------------------------------------------------------------
# Greenhouse adapter tests (mocked HTTP)
# ---------------------------------------------------------------------------

class TestGreenhouseAdapter(unittest.TestCase):

    @patch("tools.job_sources.greenhouse.requests.get")
    def test_no_tokens_returns_error(self, mock_get):
        from tools.job_sources.greenhouse import GreenhouseAdapter

        adapter = GreenhouseAdapter(board_tokens=[])
        result = adapter.fetch_jobs()
        self.assertEqual(result["jobs"], [])
        self.assertTrue(len(result["errors"]) > 0)

    @patch("tools.job_sources.greenhouse.requests.get")
    def test_successful_fetch(self, mock_get):
        from tools.job_sources.greenhouse import GreenhouseAdapter

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "jobs": [{
                    "id": 999,
                    "title": "Software Engineer",
                    "location": {"name": "Remote"},
                    "absolute_url": "https://boards.greenhouse.io/test/jobs/999",
                    "content": "<p>Python, React, REST APIs</p>",
                    "updated_at": "2026-10-01T00:00:00Z",
                }]
            },
        )
        mock_get.return_value.raise_for_status = MagicMock()

        adapter = GreenhouseAdapter(board_tokens=["testcompany"])
        result = adapter.fetch_jobs()
        self.assertEqual(len(result["jobs"]), 1)
        self.assertEqual(result["jobs"][0]["source"], "greenhouse")

    @patch("tools.job_sources.greenhouse.requests.get")
    def test_404_board_not_found(self, mock_get):
        from tools.job_sources.greenhouse import GreenhouseAdapter

        error_response = MagicMock()
        error_response.status_code = 404
        mock_get.side_effect = requests.exceptions.HTTPError(
            response=error_response,
        )

        adapter = GreenhouseAdapter(board_tokens=["nonexistent"])
        result = adapter.fetch_jobs()
        self.assertEqual(result["jobs"], [])
        self.assertTrue(any("404" in e for e in result["errors"]))


# ---------------------------------------------------------------------------
# Lever adapter tests (mocked HTTP)
# ---------------------------------------------------------------------------

class TestLeverAdapter(unittest.TestCase):

    @patch("tools.job_sources.lever.requests.get")
    def test_no_slugs_returns_error(self, mock_get):
        from tools.job_sources.lever import LeverAdapter

        adapter = LeverAdapter(company_slugs=[])
        result = adapter.fetch_jobs()
        self.assertEqual(result["jobs"], [])
        self.assertTrue(len(result["errors"]) > 0)

    @patch("tools.job_sources.lever.requests.get")
    def test_successful_fetch(self, mock_get):
        from tools.job_sources.lever import LeverAdapter

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [{
                "id": "abc-123",
                "text": "Backend Developer",
                "categories": {
                    "location": "Mumbai, India",
                    "commitment": "Full-time",
                },
                "descriptionPlain": "Python, Django, PostgreSQL",
                "applyUrl": "https://jobs.lever.co/test/abc-123/apply",
                "hostedUrl": "https://jobs.lever.co/test/abc-123",
                "createdAt": 1696118400000,
                "additional": "",
                "lists": [],
            }],
        )
        mock_get.return_value.raise_for_status = MagicMock()

        adapter = LeverAdapter(company_slugs=["testcompany"])
        result = adapter.fetch_jobs()
        self.assertEqual(len(result["jobs"]), 1)
        self.assertEqual(result["jobs"][0]["source"], "lever")
        self.assertEqual(result["jobs"][0]["location"], "Mumbai, India")


# ---------------------------------------------------------------------------
# Multi-source registry tests
# ---------------------------------------------------------------------------

class TestFetchAllSources(unittest.TestCase):

    @patch("tools.job_sources.registry._ADAPTERS", {})
    def test_partial_failure_returns_successful_results(self):
        from tools.job_sources.registry import fetch_all_sources, register_adapter

        # Create a working adapter and a failing adapter.
        working = MagicMock()
        working.fetch_jobs.return_value = {
            "jobs": [{"id": "1", "title": "Good Job"}],
            "errors": [],
            "stats": {"retrieved": 1, "valid": 1, "rejected": 0},
        }

        failing = MagicMock()
        failing.fetch_jobs.side_effect = Exception("Network down")

        register_adapter("working", working)
        register_adapter("failing", failing)

        result = fetch_all_sources(sources=["working", "failing"])

        # Should have the job from the working source.
        self.assertEqual(len(result["jobs"]), 1)
        # Should report the error from the failing source.
        self.assertTrue(
            any("failed" in e.lower() for e in result["errors"])
        )

    @patch("tools.job_sources.registry._ADAPTERS", {})
    def test_unknown_source(self):
        from tools.job_sources.registry import fetch_all_sources

        result = fetch_all_sources(sources=["nonexistent"])
        self.assertEqual(result["jobs"], [])
        self.assertTrue(
            any("Unknown" in e for e in result["errors"])
        )


class TestVerifyApplicationUrl(unittest.TestCase):
    """Test the verify_application_url reachability helper."""

    @patch("requests.head")
    def test_reachable_200_head(self, mock_head):
        from tools.job_sources.base import verify_application_url
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "https://example.com/apply"
        mock_head.return_value = mock_resp

        result = verify_application_url("https://example.com/apply")
        self.assertEqual(result["url_status"], "url_reachable")
        self.assertEqual(result["http_status"], 200)
        self.assertEqual(result["application_status"], "application_status_unknown")
        self.assertIsNone(result["error"])

    @patch("requests.get")
    @patch("requests.head")
    def test_fallback_to_get_on_403(self, mock_head, mock_get):
        from tools.job_sources.base import verify_application_url
        head_resp = MagicMock()
        head_resp.status_code = 403
        mock_head.return_value = head_resp

        get_resp = MagicMock()
        get_resp.status_code = 200
        get_resp.url = "https://example.com/apply"
        mock_get.return_value = get_resp

        result = verify_application_url("https://example.com/apply")
        self.assertEqual(result["url_status"], "url_reachable")
        self.assertEqual(result["http_status"], 200)
        mock_get.assert_called_once()

    @patch("requests.head")
    def test_unreachable_404_marks_closed(self, mock_head):
        from tools.job_sources.base import verify_application_url
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.url = "https://example.com/apply"
        mock_head.return_value = mock_resp

        result = verify_application_url("https://example.com/apply")
        self.assertEqual(result["url_status"], "url_unreachable")
        self.assertEqual(result["http_status"], 404)
        self.assertEqual(result["application_status"], "closed")

    @patch("requests.head")
    def test_timeout_handling(self, mock_head):
        from tools.job_sources.base import verify_application_url
        mock_head.side_effect = requests.exceptions.Timeout("Read timeout")

        result = verify_application_url("https://example.com/apply")
        self.assertEqual(result["url_status"], "url_unreachable")
        self.assertIsNone(result["http_status"])
        self.assertIn("timed out", result["error"].lower())

    def test_invalid_url_scheme(self):
        from tools.job_sources.base import verify_application_url
        result = verify_application_url("ftp://example.com/apply")
        self.assertEqual(result["url_status"], "url_unreachable")
        self.assertEqual(result["application_status"], "closed")

    @patch("tools.job_sources.remotive.requests.get")
    def test_remotive_enforces_software_dev_category(self, mock_get):
        from tools.job_sources.remotive import RemotiveAdapter
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"jobs": []}
        mock_get.return_value = mock_resp

        adapter = RemotiveAdapter()
        adapter.fetch_jobs()

        args, kwargs = mock_get.call_args
        self.assertEqual(kwargs["params"]["category"], "software-dev")


class TestFetchCache(unittest.TestCase):
    """Test source fetch caching behavior and safety."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)
        self.status_file = self.temp_path / "fetch_status.json"
        self.live_jobs_file = self.temp_path / "live_jobs.json"

    def tearDown(self):
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_cache_hit_within_ttl_avoids_duplicate_request(self):
        """Repeated calls inside TTL do not trigger duplicate adapter calls."""
        from tools.job_sources.registry import (
            fetch_all_sources,
            register_adapter,
            _ADAPTERS,
        )

        mock_adapter = MagicMock()
        mock_job = {
            "id": "c1",
            "source": "cache_test",
            "title": "Software Engineer",
            "company": "CacheCorp",
            "apply_url": "https://example.com/apply",
            "location": "Remote, India",
            "posted_at": "2026-10-22T00:00:00Z",
        }
        mock_adapter.fetch_jobs.return_value = {
            "jobs": [mock_job],
            "errors": [],
            "stats": {"retrieved": 1, "valid": 1, "rejected": 0},
        }

        with patch("tools.job_sources.registry.FETCH_STATUS_PATH", self.status_file), \
             patch("tools.job_sources.registry.LIVE_JOBS_PATH", self.live_jobs_file), \
             patch.dict(_ADAPTERS, {"cache_test": mock_adapter}, clear=True):

            # First fetch (cache miss)
            res1 = fetch_all_sources(sources=["cache_test"], cache_ttl_seconds=120)
            self.assertEqual(len(res1["jobs"]), 1)
            mock_adapter.fetch_jobs.assert_called_once()

            # Persist live jobs so cache lookup finds them
            from tools.job_sources.registry import save_live_jobs
            save_live_jobs([mock_job])

            # Second fetch inside TTL (should hit cache)
            res2 = fetch_all_sources(sources=["cache_test"], cache_ttl_seconds=120)
            self.assertEqual(len(res2["jobs"]), 1)
            self.assertTrue(res2["stats"]["per_source"]["cache_test"]["cached"])
            # Adapter fetch_jobs should NOT be called again
            mock_adapter.fetch_jobs.assert_called_once()

    def test_expired_cache_refreshes_from_adapter(self):
        """Expired cache entry triggers a fresh adapter fetch."""
        from datetime import datetime, timezone, timedelta
        from tools.job_sources.registry import (
            fetch_all_sources,
            _ADAPTERS,
            save_live_jobs,
        )

        mock_adapter = MagicMock()
        mock_adapter.fetch_jobs.return_value = {
            "jobs": [{"id": "fresh", "source": "exp_test", "title": "SE"}],
            "errors": [],
            "stats": {"retrieved": 1, "valid": 1, "rejected": 0},
        }

        # Write an old status (fetched 300s ago)
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
        with self.status_file.open("w", encoding="utf-8") as f:
            json.dump({
                "exp_test": {
                    "last_fetch": old_time,
                    "retrieved": 1,
                    "valid": 1,
                    "success": True,
                    "error_count": 0,
                    "last_error": None,
                }
            }, f)

        with patch("tools.job_sources.registry.FETCH_STATUS_PATH", self.status_file), \
             patch("tools.job_sources.registry.LIVE_JOBS_PATH", self.live_jobs_file), \
             patch.dict(_ADAPTERS, {"exp_test": mock_adapter}, clear=True):

            # TTL is 60s, fetch was 300s ago -> must refresh
            res = fetch_all_sources(sources=["exp_test"], cache_ttl_seconds=60)
            self.assertEqual(len(res["jobs"]), 1)
            self.assertFalse(res["stats"]["per_source"]["exp_test"]["cached"])
            mock_adapter.fetch_jobs.assert_called_once()

    def test_force_refresh_bypasses_cache(self):
        """force_refresh=True always fetches live."""
        from tools.job_sources.registry import (
            fetch_all_sources,
            _ADAPTERS,
            save_live_jobs,
        )

        mock_adapter = MagicMock()
        mock_adapter.fetch_jobs.return_value = {
            "jobs": [{"id": "j1", "source": "force_test"}],
            "errors": [],
            "stats": {"retrieved": 1, "valid": 1, "rejected": 0},
        }

        with patch("tools.job_sources.registry.FETCH_STATUS_PATH", self.status_file), \
             patch("tools.job_sources.registry.LIVE_JOBS_PATH", self.live_jobs_file), \
             patch.dict(_ADAPTERS, {"force_test": mock_adapter}, clear=True):

            save_live_jobs([{"id": "j1", "source": "force_test"}])
            fetch_all_sources(sources=["force_test"], cache_ttl_seconds=120)
            self.assertEqual(mock_adapter.fetch_jobs.call_count, 1)

            # Call with force_refresh=True
            fetch_all_sources(sources=["force_test"], cache_ttl_seconds=120, force_refresh=True)
            self.assertEqual(mock_adapter.fetch_jobs.call_count, 2)

    def test_corrupted_cache_status_safely_falls_back(self):
        """Corrupted fetch_status.json does not crash and safely falls back to live fetch."""
        from tools.job_sources.registry import (
            fetch_all_sources,
            _ADAPTERS,
        )

        # Write corrupted JSON
        with self.status_file.open("w", encoding="utf-8") as f:
            f.write("{invalid-json-content")

        mock_adapter = MagicMock()
        mock_adapter.fetch_jobs.return_value = {
            "jobs": [{"id": "corrupt_ok", "source": "corrupt_test"}],
            "errors": [],
            "stats": {"retrieved": 1, "valid": 1, "rejected": 0},
        }

        with patch("tools.job_sources.registry.FETCH_STATUS_PATH", self.status_file), \
             patch("tools.job_sources.registry.LIVE_JOBS_PATH", self.live_jobs_file), \
             patch.dict(_ADAPTERS, {"corrupt_test": mock_adapter}, clear=True):

            res = fetch_all_sources(sources=["corrupt_test"], cache_ttl_seconds=120)
            self.assertEqual(len(res["jobs"]), 1)
            mock_adapter.fetch_jobs.assert_called_once()

    def test_failed_fetch_handles_errors_and_never_marks_success(self):
        """Failed fetch handles network error and does not produce cached false vacancies."""
        from tools.job_sources.registry import (
            fetch_all_sources,
            _ADAPTERS,
        )

        mock_adapter = MagicMock()
        mock_adapter.fetch_jobs.side_effect = ConnectionError("Remotive API unreachable")

        with patch("tools.job_sources.registry.FETCH_STATUS_PATH", self.status_file), \
             patch("tools.job_sources.registry.LIVE_JOBS_PATH", self.live_jobs_file), \
             patch.dict(_ADAPTERS, {"net_err": mock_adapter}, clear=True):

            res = fetch_all_sources(sources=["net_err"], cache_ttl_seconds=120)
            self.assertEqual(len(res["jobs"]), 0)
            self.assertTrue(any("failed unexpectedly" in e for e in res["errors"]))

            # Verify fetch_status recorded failure
            with self.status_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertFalse(data["net_err"]["success"])
            self.assertIn("Remotive API unreachable", data["net_err"]["last_error"])


if __name__ == "__main__":
    unittest.main()
