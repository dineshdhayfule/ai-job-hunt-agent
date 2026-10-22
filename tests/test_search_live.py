"""Tests for the live job search tool (tools/search_live_jobs.py).

Covers the bridge between provider adapters and the matching engine,
focusing on the specific behaviors the user requested verification for:

- Unknown fresher eligibility is distinct from ineligible.
- Empty location filters don't accidentally exclude all jobs.
- Saved minimum_match_score (75) from memory is used by default.
- Source attribution is truthful (is_live_listing, source field).
- Adapter-to-matcher schema bridge (_adapt_for_matching) is correct.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.search_live_jobs import _adapt_for_matching, search_live_jobs


# ---------------------------------------------------------------------------
# Test profile for matching (mirrors the real profile structure)
# ---------------------------------------------------------------------------

TEST_PROFILE = {
    "target_roles": ["Software Engineer", "Backend Developer"],
    "skills": ["Python", "JavaScript", "REST APIs", "React"],
    "experience_level": "fresher",
    "graduation_year": 2026,
    "preferred_locations": ["Remote, India", "Mumbai, India"],
    "willing_to_relocate": True,
    "preferred_employment_types": ["Full-time", "Internship"],
    "minimum_match_score": 75,
}


def make_live_job(**overrides):
    """Create a normalized live job record with defaults."""
    job = {
        "id": "test-001",
        "source": "remotive",
        "source_job_id": "12345",
        "title": "Software Engineer",
        "company": "TestCorp",
        "location": "Remote, India",
        "description": "Python, JavaScript, REST APIs",
        "skills": ["Python", "JavaScript", "REST APIs"],
        "experience_years": None,
        "employment_type": "Full-time",
        "fresher_eligibility": "unknown",
        "posted_at": "2026-10-01T00:00:00",
        "updated_at": None,
        "apply_url": "https://remotive.com/remote-jobs/12345",
        "source_url": "https://remotive.com/remote-jobs/12345",
        "first_seen_at": "2026-10-01T00:00:00Z",
        "last_seen_at": "2026-10-01T00:00:00Z",
        "listing_status": "active",
    }
    job.update(overrides)
    return job


# ---------------------------------------------------------------------------
# Test _adapt_for_matching bridge
# ---------------------------------------------------------------------------

class TestAdaptForMatching(unittest.TestCase):
    """Test the schema bridge from live jobs to the matching engine."""

    def test_unknown_eligibility_does_not_penalize(self):
        """Unknown eligibility should be preserved as None / unknown, not fabricated as True."""
        job = make_live_job(fresher_eligibility="unknown")
        adapted = _adapt_for_matching(job)
        self.assertIsNone(adapted["is_fresher_friendly"])
        self.assertIsNone(adapted["experience_years"])
        self.assertEqual(adapted["fresher_eligibility"], "unknown")

    def test_confirmed_fresher_eligible(self):
        job = make_live_job(fresher_eligibility="confirmed_fresher")
        adapted = _adapt_for_matching(job)
        self.assertTrue(adapted["is_fresher_friendly"])

    def test_confirmed_experience_ineligible(self):
        job = make_live_job(
            fresher_eligibility="confirmed_experience",
            experience_years=3,
        )
        adapted = _adapt_for_matching(job)
        self.assertFalse(adapted["is_fresher_friendly"])
        self.assertEqual(adapted["experience_years"], 3)

    def test_preserves_skills(self):
        job = make_live_job(skills=["Python", "Django"])
        adapted = _adapt_for_matching(job)
        self.assertEqual(adapted["skills"], ["Python", "Django"])

    def test_preserves_apply_url(self):
        url = "https://example.com/apply"
        job = make_live_job(apply_url=url)
        adapted = _adapt_for_matching(job)
        self.assertEqual(adapted["apply_url"], url)


# ---------------------------------------------------------------------------
# Test search_live_jobs integration
# ---------------------------------------------------------------------------

class TestSearchLiveJobs(unittest.TestCase):
    """Integration tests for the live search pipeline."""

    def setUp(self):
        """Create temp profile and mock the live pipeline dependencies."""
        self.temp_dir = tempfile.mkdtemp()
        self.profile_path = Path(self.temp_dir) / "profile.json"
        with self.profile_path.open("w", encoding="utf-8") as f:
            json.dump(TEST_PROFILE, f)

    def tearDown(self):
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def _patch_pipeline(self, jobs, memory_score=75):
        """Return a context manager stack that mocks the live pipeline."""
        from unittest.mock import patch
        patches = {}

        # Mock initialize_default_adapters (avoid real adapter setup).
        patches["init"] = patch(
            "tools.search_live_jobs.initialize_default_adapters",
        )

        # Mock fetch_all_sources to return our jobs.
        patches["fetch"] = patch(
            "tools.search_live_jobs.fetch_all_sources",
            return_value={
                "jobs": jobs,
                "errors": [],
                "stats": {
                    "total_retrieved": len(jobs),
                    "total_valid": len(jobs),
                    "total_rejected": 0,
                    "total_duplicated": 0,
                    "total_returned": len(jobs),
                },
            },
        )

        # Mock persistence (don't write files).
        patches["load_live"] = patch(
            "tools.search_live_jobs.load_live_jobs", return_value=[],
        )
        patches["merge"] = patch(
            "tools.search_live_jobs.merge_live_jobs", return_value=jobs,
        )
        patches["save"] = patch(
            "tools.search_live_jobs.save_live_jobs",
        )

        # Mock profile loading to use our test profile.
        patches["load_json"] = patch(
            "tools.search_live_jobs.load_json",
            return_value=TEST_PROFILE,
        )

        # Mock memory loading.
        patches["load_memory"] = patch(
            "tools.search_live_jobs.load_memory",
            return_value={"minimum_match_score": memory_score},
        )

        return patches

    def _start_patches(self, patches):
        mocks = {}
        for key, p in patches.items():
            mocks[key] = p.start()
        return mocks

    def _stop_patches(self, patches):
        for p in patches.values():
            p.stop()

    def test_empty_location_includes_all_jobs(self):
        """Empty location filter should not exclude any jobs."""
        jobs = [
            make_live_job(id="a", location="Remote"),
            make_live_job(id="b", location="Mumbai, India"),
            make_live_job(id="c", location=""),
        ]
        patches = self._patch_pipeline(jobs)
        mocks = self._start_patches(patches)
        try:
            result = search_live_jobs(
                location="",   # Empty — should skip filter.
                min_match_score=0,
            )
            # All 3 jobs should be included (no location filtering).
            self.assertEqual(result["count"], 3)
        finally:
            self._stop_patches(patches)

    def test_none_location_includes_all_jobs(self):
        """None location filter should not exclude any jobs."""
        jobs = [
            make_live_job(id="a", location="Remote"),
            make_live_job(id="b", location="Mumbai"),
        ]
        patches = self._patch_pipeline(jobs)
        mocks = self._start_patches(patches)
        try:
            result = search_live_jobs(
                location=None,
                min_match_score=0,
            )
            self.assertEqual(result["count"], 2)
        finally:
            self._stop_patches(patches)

    def test_min_score_falls_back_to_memory(self):
        """When min_match_score is None, should use memory's value (75)."""
        # Create a job with a high match so it passes the 75 threshold.
        high_match_job = make_live_job(
            id="high",
            skills=["Python", "JavaScript", "REST APIs"],
            title="Software Engineer",
            location="Remote, India",
        )
        # Create a job with low skills match (will score below 75).
        low_match_job = make_live_job(
            id="low",
            skills=["Haskell", "Erlang"],
            title="Data Scientist",
            location="London, UK",
            fresher_eligibility="confirmed_experience",
        )

        patches = self._patch_pipeline(
            [high_match_job, low_match_job],
            memory_score=75,
        )
        mocks = self._start_patches(patches)
        try:
            result = search_live_jobs(
                min_match_score=None,  # Should fall back to 75.
            )
            # Only the high-scoring job should pass the 75 threshold.
            for job in result["jobs"]:
                self.assertGreaterEqual(job["match_score"], 75)
        finally:
            self._stop_patches(patches)

    def test_unknown_eligibility_included_with_fresher_only(self):
        """fresher_only should exclude confirmed_experience but keep unknown."""
        jobs = [
            make_live_job(
                id="unknown", fresher_eligibility="unknown",
            ),
            make_live_job(
                id="confirmed", fresher_eligibility="confirmed_experience",
            ),
            make_live_job(
                id="fresher", fresher_eligibility="confirmed_fresher",
            ),
        ]
        patches = self._patch_pipeline(jobs)
        mocks = self._start_patches(patches)
        try:
            result = search_live_jobs(
                fresher_only=True,
                min_match_score=0,
            )
            returned_ids = {j.get("title") for j in result["jobs"]}
            # unknown and confirmed_fresher should be included.
            # confirmed_experience should be excluded.
            # All have the same title so let's check by count.
            self.assertEqual(result["count"], 2)
        finally:
            self._stop_patches(patches)

    def test_source_attribution_is_truthful(self):
        """Live results must have is_live_listing=True and correct source."""
        job = make_live_job(source="remotive")
        patches = self._patch_pipeline([job])
        mocks = self._start_patches(patches)
        try:
            result = search_live_jobs(min_match_score=0)
            self.assertEqual(result["count"], 1)
            entry = result["jobs"][0]
            self.assertTrue(entry["is_live_listing"])
            self.assertEqual(entry["source"], "remotive")
            self.assertEqual(
                entry["apply_url"],
                "https://remotive.com/remote-jobs/12345",
            )
        finally:
            self._stop_patches(patches)

    def test_fresher_eligibility_preserved_in_output(self):
        """The fresher_eligibility field should be in the result entry."""
        job = make_live_job(fresher_eligibility="unknown")
        patches = self._patch_pipeline([job])
        mocks = self._start_patches(patches)
        try:
            result = search_live_jobs(min_match_score=0)
            self.assertEqual(result["count"], 1)
            self.assertEqual(
                result["jobs"][0]["fresher_eligibility"], "unknown",
            )
        finally:
            self._stop_patches(patches)

    def test_limit_caps_results(self):
        """Result count should respect the limit parameter."""
        jobs = [make_live_job(id=f"job-{i}") for i in range(10)]
        patches = self._patch_pipeline(jobs)
        mocks = self._start_patches(patches)
        try:
            result = search_live_jobs(min_match_score=0, limit=3)
            self.assertLessEqual(result["count"], 3)
        finally:
            self._stop_patches(patches)

    def test_location_filter_is_case_insensitive(self):
        """Location filter should match case-insensitively."""
        jobs = [
            make_live_job(id="a", location="Mumbai, India"),
            make_live_job(id="b", location="Remote"),
        ]
        patches = self._patch_pipeline(jobs)
        mocks = self._start_patches(patches)
        try:
            result = search_live_jobs(
                location="mumbai",  # lowercase
                min_match_score=0,
            )
            self.assertEqual(result["count"], 1)
            self.assertIn("Mumbai", result["jobs"][0]["location"])
        finally:
            self._stop_patches(patches)

    def test_employment_type_filter(self):
        """Employment type filter should work correctly."""
        jobs = [
            make_live_job(id="a", employment_type="Full-time"),
            make_live_job(id="b", employment_type="Internship"),
        ]
        patches = self._patch_pipeline(jobs)
        mocks = self._start_patches(patches)
        try:
            result = search_live_jobs(
                employment_type="Internship",
                min_match_score=0,
            )
            self.assertEqual(result["count"], 1)
            self.assertEqual(
                result["jobs"][0]["employment_type"], "Internship",
            )
        finally:
            self._stop_patches(patches)

    def test_freshness_filter_within_24h(self):
        """Recent postings within max_age_hours should pass freshness filter."""
        from datetime import datetime, timezone, timedelta
        recent_iso = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        job = make_live_job(posted_at=recent_iso)
        patches = self._patch_pipeline([job])
        mocks = self._start_patches(patches)
        try:
            result = search_live_jobs(max_age_hours=24, min_match_score=0)
            self.assertEqual(result["count"], 1)
            self.assertEqual(result["jobs"][0]["freshness_status"], "within_window")
        finally:
            self._stop_patches(patches)

    def test_freshness_filter_excludes_stale_job(self):
        """Postings older than max_age_hours must be excluded."""
        from datetime import datetime, timezone, timedelta
        old_iso = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        job = make_live_job(posted_at=old_iso)
        patches = self._patch_pipeline([job])
        mocks = self._start_patches(patches)
        try:
            result = search_live_jobs(max_age_hours=24, min_match_score=0)
            self.assertEqual(result["count"], 0)
        finally:
            self._stop_patches(patches)

    def test_freshness_filter_excludes_missing_or_invalid_date(self):
        """Jobs with missing or unparseable posted_at must be excluded when max_age_hours is set."""
        job_none = make_live_job(id="none", posted_at=None)
        job_invalid = make_live_job(id="invalid", posted_at="not-a-date")
        patches = self._patch_pipeline([job_none, job_invalid])
        mocks = self._start_patches(patches)
        try:
            result = search_live_jobs(max_age_hours=24, min_match_score=0)
            self.assertEqual(result["count"], 0)
        finally:
            self._stop_patches(patches)

    def test_url_reachability_verification(self):
        """URL reachability check should populate url_status and application_status."""
        job = make_live_job(apply_url="https://example.com/apply")
        patches = self._patch_pipeline([job])
        mocks = self._start_patches(patches)
        try:
            with patch("tools.search_live_jobs.verify_application_url") as mock_verify:
                mock_verify.return_value = {
                    "url_status": "url_reachable",
                    "http_status": 200,
                    "final_url": "https://example.com/apply",
                    "application_status": "application_status_unknown",
                    "checked_at": "2026-10-22T00:00:00Z",
                    "error": None,
                }
                result = search_live_jobs(verify_urls=True, min_match_score=0)
                self.assertEqual(result["count"], 1)
                entry = result["jobs"][0]
                self.assertEqual(entry["url_status"], "url_reachable")
                self.assertEqual(entry["application_status"], "application_status_unknown")
                self.assertIsNotNone(entry["url_checked_at"])
                mock_verify.assert_called_once_with("https://example.com/apply")
        finally:
            self._stop_patches(patches)

    def test_memory_roles_and_locations_merged(self):
        """Dynamic preferences from memory should merge into matching profile."""
        job = make_live_job(
            title="DevOps Specialist",
            location="Pune, India",
            skills=["Python", "JavaScript"],
            is_fresher_friendly=True,
            experience_years=0,
        )
        patches = self._patch_pipeline([job])
        # Override load_memory mock to return dynamic preferences
        mocks = self._start_patches(patches)
        mocks["load_memory"].return_value = {
            "preferred_roles": ["DevOps Specialist"],
            "preferred_locations": ["Pune, India"],
            "minimum_match_score": 60,
        }
        try:
            result = search_live_jobs(min_match_score=None)
            self.assertEqual(result["count"], 1)
            self.assertGreaterEqual(result["jobs"][0]["match_score"], 60)
        finally:
            self._stop_patches(patches)

    def test_confirmed_vs_potential_matches_separated(self):
        """Confirmed fresher matches and unknown-eligibility potential matches are clearly partitioned."""
        confirmed_job = make_live_job(
            id="job-confirmed",
            title="Software Engineer",
            skills=["Python", "JavaScript", "REST APIs"],
            location="Remote, India",
            fresher_eligibility="confirmed_fresher",
        )
        potential_job = make_live_job(
            id="job-potential",
            title="Backend Developer",
            skills=["Python", "JavaScript", "REST APIs"],
            location="Remote, India",
            fresher_eligibility="unknown",
        )
        ineligible_job = make_live_job(
            id="job-ineligible",
            title="Software Engineer",
            skills=["Python", "JavaScript", "REST APIs"],
            location="Remote, India",
            fresher_eligibility="confirmed_experience",
            experience_years=3,
        )
        patches = self._patch_pipeline([confirmed_job, potential_job, ineligible_job])
        mocks = self._start_patches(patches)
        try:
            result = search_live_jobs(min_match_score=75)
            # Ineligible job must be completely excluded
            self.assertEqual(result["count"], 2)

            confirmed = result["confirmed_matches"]
            potential = result["potential_matches"]

            self.assertEqual(len(confirmed), 1)
            self.assertEqual(len(potential), 1)

            self.assertEqual(confirmed[0]["match_category"], "confirmed_match")
            self.assertTrue(confirmed[0]["is_confirmed_match"])
            self.assertTrue(confirmed[0]["eligible"])
            self.assertEqual(confirmed[0]["eligibility_status"], "confirmed")

            self.assertEqual(potential[0]["match_category"], "potential_match")
            self.assertTrue(potential[0]["is_potential_match"])
            self.assertFalse(potential[0]["eligible"])
            self.assertEqual(potential[0]["eligibility_status"], "unknown")
        finally:
            self._stop_patches(patches)

    def test_explicit_freshness_windows_72h_and_168h(self):
        """Support explicit 72-hour and 168-hour (7-day) freshness windows."""
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        job_10h = make_live_job(id="j10", posted_at=(now - timedelta(hours=10)).isoformat())
        job_50h = make_live_job(id="j50", posted_at=(now - timedelta(hours=50)).isoformat())
        job_120h = make_live_job(id="j120", posted_at=(now - timedelta(hours=120)).isoformat())
        job_200h = make_live_job(id="j200", posted_at=(now - timedelta(hours=200)).isoformat())

        all_jobs = [job_10h, job_50h, job_120h, job_200h]
        patches = self._patch_pipeline(all_jobs)
        mocks = self._start_patches(patches)
        try:
            # 24h window
            res_24 = search_live_jobs(max_age_hours=24, min_match_score=0)
            self.assertEqual(res_24["count"], 1)

            # 72h window
            res_72 = search_live_jobs(max_age_hours=72, min_match_score=0)
            self.assertEqual(res_72["count"], 2)

            # 168h window (7 days)
            res_168 = search_live_jobs(max_age_hours=168, min_match_score=0)
            self.assertEqual(res_168["count"], 3)
        finally:
            self._stop_patches(patches)

    def test_freshness_boundary_cutoff(self):
        """Test boundary conditions: listing exactly at 24.0h cutoff vs 24.1h just outside."""
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        # Exactly at cutoff (e.g. 23.95h or 24.0h)
        job_exact = make_live_job(id="exact", posted_at=(now - timedelta(hours=23, minutes=59)).isoformat())
        # Just outside cutoff (24.2h)
        job_outside = make_live_job(id="outside", posted_at=(now - timedelta(hours=24, minutes=15)).isoformat())

        patches = self._patch_pipeline([job_exact, job_outside])
        mocks = self._start_patches(patches)
        try:
            result = search_live_jobs(max_age_hours=24, min_match_score=0)
            self.assertEqual(result["count"], 1)
            self.assertEqual(result["jobs"][0]["freshness_status"], "within_window")
        finally:
            self._stop_patches(patches)

    def test_timezone_naive_and_aware_handling(self):
        """Handle both timezone-naive and timezone-aware timestamps safely."""
        from datetime import datetime, timezone, timedelta
        # Timezone-aware with UTC offset
        job_aware = make_live_job(id="aware", posted_at="2026-10-22T08:00:00+00:00")
        # Timezone-naive
        job_naive = make_live_job(id="naive", posted_at="2026-10-22T08:00:00")

        patches = self._patch_pipeline([job_aware, job_naive])
        mocks = self._start_patches(patches)
        try:
            result = search_live_jobs(min_match_score=0)
            self.assertEqual(result["count"], 2)
            for j in result["jobs"]:
                self.assertIsNotNone(j["posting_age_hours"])
        finally:
            self._stop_patches(patches)


if __name__ == "__main__":
    unittest.main()
