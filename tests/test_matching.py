"""Tests for the personalized matching engine (tools/match_jobs.py)."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from tools.match_jobs import (
    calculate_match,
    calculate_role_score,
    calculate_location_score,
    get_skill_matches,
    match_jobs,
    normalize,
)


# ---------------------------------------------------------------------------
# Test profile
# ---------------------------------------------------------------------------

TEST_PROFILE = {
    "target_roles": [
        "Software Engineer",
        "Backend Developer",
        "Full Stack Developer",
        "Java Developer",
    ],
    "skills": [
        "Java", "Python", "JavaScript", "React", "Node.js",
        "Express", "MySQL", "MongoDB", "Git", "REST APIs",
        "Spring Boot", "HTML", "CSS",
    ],
    "experience_level": "fresher",
    "graduation_year": 2026,
    "preferred_locations": ["Remote, India", "Mumbai, India"],
    "willing_to_relocate": True,
    "preferred_employment_types": ["Full-time", "Internship"],
    "minimum_match_score": 75,
}


def make_job(**overrides):
    """Create a test job record with defaults."""
    job = {
        "id": 1,
        "title": "Software Engineer",
        "company": "TestCorp",
        "location": "Remote, India",
        "skills": ["Python", "JavaScript", "REST APIs"],
        "experience_years": 0,
        "employment_type": "Full-time",
        "is_fresher_friendly": True,
        "apply_url": "https://example.com/jobs/1",
    }
    job.update(overrides)
    return job


class TestNormalize(unittest.TestCase):
    """Test the normalize helper function."""

    def test_lowercase_and_strip(self):
        self.assertEqual(normalize("  Java  "), "java")

    def test_nodejs_normalization(self):
        self.assertEqual(normalize("Node.js"), "nodejs")

    def test_nextjs_normalization(self):
        self.assertEqual(normalize("Next.js"), "nextjs")

    def test_fullstack_normalization(self):
        self.assertEqual(normalize("Full-Stack"), "full stack")

    def test_springboot_normalization(self):
        self.assertEqual(normalize("SpringBoot"), "spring boot")

    def test_whitespace_collapsing(self):
        self.assertEqual(normalize("Spring   Boot"), "spring boot")


class TestSkillMatches(unittest.TestCase):
    """Test skill matching between job and profile."""

    def test_exact_matches(self):
        matched, missing, score = get_skill_matches(
            ["Python", "JavaScript"], TEST_PROFILE["skills"],
        )
        self.assertEqual(len(matched), 2)
        self.assertEqual(len(missing), 0)
        self.assertEqual(score, 100.0)

    def test_partial_matches(self):
        matched, missing, score = get_skill_matches(
            ["Python", "Django", "PostgreSQL"], TEST_PROFILE["skills"],
        )
        self.assertEqual(len(matched), 1)
        self.assertEqual(len(missing), 2)
        self.assertAlmostEqual(score, 100 / 3, places=1)

    def test_case_insensitive(self):
        matched, missing, score = get_skill_matches(
            ["python", "JAVASCRIPT"], TEST_PROFILE["skills"],
        )
        self.assertEqual(len(matched), 2)
        self.assertEqual(score, 100.0)

    def test_no_matches(self):
        matched, missing, score = get_skill_matches(
            ["Haskell", "Erlang"], TEST_PROFILE["skills"],
        )
        self.assertEqual(len(matched), 0)
        self.assertEqual(len(missing), 2)
        self.assertEqual(score, 0.0)

    def test_empty_job_skills(self):
        matched, missing, score = get_skill_matches(
            [], TEST_PROFILE["skills"],
        )
        self.assertEqual(score, 0.0)

    def test_empty_profile_skills(self):
        matched, missing, score = get_skill_matches(
            ["Python"], [],
        )
        self.assertEqual(score, 0.0)

    def test_nodejs_matching(self):
        """Node.js should match regardless of formatting."""
        matched, missing, score = get_skill_matches(
            ["Node.js"], TEST_PROFILE["skills"],
        )
        self.assertEqual(len(matched), 1)


class TestRoleScore(unittest.TestCase):
    """Test role compatibility scoring."""

    def test_exact_role_match(self):
        score = calculate_role_score(
            "Software Engineer", TEST_PROFILE["target_roles"],
        )
        self.assertEqual(score, 100.0)

    def test_partial_role_match(self):
        score = calculate_role_score(
            "Backend Developer Intern", TEST_PROFILE["target_roles"],
        )
        self.assertEqual(score, 100.0)

    def test_equivalent_title(self):
        score = calculate_role_score(
            "Backend Engineer", TEST_PROFILE["target_roles"],
        )
        self.assertEqual(score, 100.0)

    def test_role_mismatch(self):
        score = calculate_role_score(
            "Data Scientist", TEST_PROFILE["target_roles"],
        )
        self.assertEqual(score, 0.0)

    def test_react_matches_frontend(self):
        roles = ["Frontend Developer"]
        score = calculate_role_score("React Developer", roles)
        self.assertEqual(score, 100.0)

    def test_unrelated_sales_role_rejected(self):
        score = calculate_role_score("Inside Sales Contractor", TEST_PROFILE["target_roles"])
        self.assertEqual(score, 0.0)

    def test_unrelated_writer_role_rejected(self):
        score = calculate_role_score("Freelance Copywriter", TEST_PROFILE["target_roles"])
        self.assertEqual(score, 0.0)

    def test_unrelated_office_assistant_rejected(self):
        score = calculate_role_score("Remote Office Assistant", TEST_PROFILE["target_roles"])
        self.assertEqual(score, 0.0)

    def test_unrelated_content_reviewer_rejected(self):
        score = calculate_role_score("Content Reviewer - United States", TEST_PROFILE["target_roles"])
        self.assertEqual(score, 0.0)

    def test_senior_role_rejected_for_fresher(self):
        score = calculate_role_score(
            "Senior React Full-stack Developer",
            TEST_PROFILE["target_roles"],
            experience_level="fresher",
        )
        self.assertEqual(score, 0.0)

    def test_tech_lead_rejected_for_fresher(self):
        score = calculate_role_score(
            "Tech Lead Full-Stack Rails Engineer",
            TEST_PROFILE["target_roles"],
            experience_level="fresher",
        )
        self.assertEqual(score, 0.0)


class TestLocationScore(unittest.TestCase):
    """Test location scoring."""

    def test_remote_india_match(self):
        score = calculate_location_score("Remote, India", TEST_PROFILE)
        self.assertEqual(score, 100.0)

    def test_india_remote_match(self):
        score = calculate_location_score("India - Remote", TEST_PROFILE)
        self.assertEqual(score, 100.0)

    def test_worldwide_unrestricted_matches_remote_pref(self):
        score = calculate_location_score("Worldwide", TEST_PROFILE)
        self.assertEqual(score, 100.0)

    def test_anywhere_unrestricted_matches_remote_pref(self):
        score = calculate_location_score("Anywhere", TEST_PROFILE)
        self.assertEqual(score, 100.0)

    def test_work_from_anywhere_matches_remote_pref(self):
        score = calculate_location_score("Work from anywhere", TEST_PROFILE)
        self.assertEqual(score, 100.0)

    def test_global_matches_remote_pref(self):
        score = calculate_location_score("Global", TEST_PROFILE)
        self.assertEqual(score, 100.0)

    def test_usa_only_rejected_for_india_preference(self):
        score = calculate_location_score("USA only", TEST_PROFILE)
        self.assertEqual(score, 0.0)

    def test_us_residents_only_rejected(self):
        score = calculate_location_score("US residents only", TEST_PROFILE)
        self.assertEqual(score, 0.0)

    def test_remote_us_only_rejected(self):
        score = calculate_location_score("Remote - US Only", TEST_PROFILE)
        self.assertEqual(score, 0.0)

    def test_europe_only_rejected(self):
        score = calculate_location_score("Europe Only", TEST_PROFILE)
        self.assertEqual(score, 0.0)

    def test_worldwide_with_us_only_restriction_rejected(self):
        score = calculate_location_score("Worldwide (US Only)", TEST_PROFILE)
        self.assertEqual(score, 0.0)

    def test_bare_remote_does_not_assume_india_availability(self):
        score = calculate_location_score("Remote", TEST_PROFILE)
        self.assertEqual(score, 0.0)

    def test_mumbai_match(self):
        score = calculate_location_score("Mumbai, India", TEST_PROFILE)
        self.assertEqual(score, 100.0)

    def test_relocation_city(self):
        score = calculate_location_score("Bengaluru, India", TEST_PROFILE)
        self.assertEqual(score, 100.0)

    def test_no_match_no_relocate(self):
        profile = {**TEST_PROFILE, "willing_to_relocate": False}
        score = calculate_location_score("London, UK", profile)
        self.assertEqual(score, 0.0)

    def test_empty_location(self):
        score = calculate_location_score("", TEST_PROFILE)
        self.assertEqual(score, 100.0)


class TestCalculateMatch(unittest.TestCase):
    """Test the full match calculation."""

    def test_perfect_match(self):
        job = make_job(
            skills=["Python", "JavaScript", "REST APIs"],
            title="Software Engineer",
            location="Remote, India",
            experience_years=0,
            is_fresher_friendly=True,
            employment_type="Full-time",
        )
        result = calculate_match(job, TEST_PROFILE)
        self.assertEqual(result["match_score"], 100.0)
        self.assertTrue(result["meets_threshold"])
        self.assertTrue(result["eligible"])
        self.assertTrue(result["is_confirmed_match"])
        self.assertFalse(result["is_potential_match"])
        self.assertTrue(result["employment_type_match"])

    def test_fresher_friendly_true(self):
        job = make_job(is_fresher_friendly=True, experience_years=0)
        result = calculate_match(job, TEST_PROFILE)
        self.assertTrue(result["eligible"])
        self.assertEqual(result["eligibility_status"], "confirmed")

    def test_fresher_friendly_false(self):
        job = make_job(is_fresher_friendly=False, experience_years=3)
        result = calculate_match(job, TEST_PROFILE)
        self.assertFalse(result["eligible"])
        self.assertEqual(result["eligibility_status"], "ineligible")

    def test_fresher_friendly_true_none_experience(self):
        """A fresher-friendly job with None experience should be eligible."""
        job = make_job(is_fresher_friendly=True, experience_years=None)
        result = calculate_match(job, TEST_PROFILE)
        self.assertTrue(result["eligible"])
        self.assertEqual(result["eligibility_status"], "confirmed")

    def test_unknown_eligibility(self):
        """Missing is_fresher_friendly should not guarantee eligibility."""
        job = make_job()
        del job["is_fresher_friendly"]
        job["experience_years"] = None
        result = calculate_match(job, TEST_PROFILE)
        # Separate match quality from eligibility
        self.assertFalse(result["eligible"])
        self.assertEqual(result["eligibility_status"], "unknown")
        self.assertEqual(result["fresher_eligibility"], "unknown")
        # Match score is 100% so it meets threshold, but is potential match, not confirmed
        self.assertTrue(result["meets_threshold"])
        self.assertFalse(result["is_confirmed_match"])
        self.assertTrue(result["is_potential_match"])

    def test_contradictory_fresher_experience(self):
        """Job claiming fresher-friendly but requiring 3y experience is contradictory and ineligible."""
        job = make_job(is_fresher_friendly=True, experience_years=3)
        result = calculate_match(job, TEST_PROFILE)
        self.assertFalse(result["eligible"])
        self.assertEqual(result["eligibility_status"], "ineligible")
        self.assertFalse(result["is_confirmed_match"])
        self.assertFalse(result["is_potential_match"])
        self.assertIn("Contradictory", result["eligibility_reason"])

    def test_exact_75_threshold_boundary(self):
        """Score of exactly 75.0% normalized over 85 should meet threshold."""
        # skills: 7 of 8 in profile = 87.5% -> 50 * 87.5 = 4375
        # role: "Software Engineer" = 100% -> 20 * 100 = 2000
        # location: non-relocation London = 0% -> 15 * 0 = 0
        # Total = (4375 + 2000 + 0) / 85 = 6375 / 85 = 75.0%
        profile = {**TEST_PROFILE, "willing_to_relocate": False}
        job = make_job(
            skills=[
                "Java", "Python", "JavaScript", "React",
                "Node.js", "Express", "REST APIs", "UnknownSkill",
            ],
            title="Software Engineer",
            location="London, UK",
            experience_years=0,
            is_fresher_friendly=True,
        )
        result = calculate_match(job, profile)
        self.assertEqual(result["match_score"], 75.0)
        self.assertTrue(result["meets_threshold"])
        self.assertTrue(result["score_qualifies"])
        self.assertTrue(result["is_confirmed_match"])

    def test_just_below_75_threshold(self):
        """Score below 75% should not meet threshold."""
        # skills: 6 of 8 in profile = 75% -> 50 * 75 = 3750
        # role: 100% -> 20 * 100 = 2000
        # location: 0% -> 0
        # Total = (3750 + 2000 + 0) / 85 = 5750 / 85 = 67.65% < 75.0%
        profile = {**TEST_PROFILE, "willing_to_relocate": False}
        job = make_job(
            skills=[
                "Java", "Python", "JavaScript", "React",
                "Node.js", "Express", "UnknownA", "UnknownB",
            ],
            title="Software Engineer",
            location="London, UK",
            experience_years=0,
            is_fresher_friendly=True,
        )
        result = calculate_match(job, profile)
        self.assertLess(result["match_score"], 75.0)
        self.assertFalse(result["meets_threshold"])
        self.assertFalse(result["score_qualifies"])
        self.assertFalse(result["is_confirmed_match"])

    def test_experience_required_blocks_fresher(self):
        """Experience-required job must be ineligible and not a confirmed fresher match."""
        job = make_job(is_fresher_friendly=False, experience_years=2)
        result = calculate_match(job, TEST_PROFILE)
        self.assertFalse(result["eligible"])
        self.assertEqual(result["eligibility_status"], "ineligible")
        self.assertFalse(result["is_confirmed_match"])
        self.assertFalse(result["is_potential_match"])

    def test_employment_type_mismatch(self):
        job = make_job(employment_type="Contract")
        result = calculate_match(job, TEST_PROFILE)
        self.assertFalse(result["employment_type_match"])

    def test_minimum_score_filtering(self):
        job = make_job(
            skills=["Haskell"],
            title="Data Scientist",
            location="London, UK",
            is_fresher_friendly=False,
        )
        profile = {**TEST_PROFILE, "willing_to_relocate": False}
        result = calculate_match(job, profile)
        self.assertFalse(result["meets_threshold"])

    def test_missing_fields(self):
        """Jobs with missing fields should not crash."""
        job = {"id": 99}
        result = calculate_match(job, TEST_PROFILE)
        self.assertIn("match_score", result)
        self.assertIsInstance(result["match_score"], float)

    def test_apply_url_preserved(self):
        job = make_job(apply_url="https://careers.example.com/apply")
        result = calculate_match(job, TEST_PROFILE)
        self.assertEqual(result["apply_url"], "https://careers.example.com/apply")


class TestMatchJobs(unittest.TestCase):
    """Test the full match_jobs pipeline."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.profile_path = Path(self.temp_dir) / "profile.json"
        self.jobs_path = Path(self.temp_dir) / "jobs.json"

    def tearDown(self):
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def _write_json(self, path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_sorted_by_threshold_then_score(self):
        jobs = [
            make_job(id=1, skills=["Haskell"], title="Data Scientist",
                     location="London", is_fresher_friendly=False),
            make_job(id=2, skills=["Python", "JavaScript"],
                     title="Software Engineer", location="Remote, India"),
        ]
        self._write_json(self.profile_path, TEST_PROFILE)
        self._write_json(self.jobs_path, jobs)

        results = match_jobs(self.profile_path, self.jobs_path)
        self.assertEqual(len(results), 2)
        # Higher-scoring, threshold-meeting job first.
        self.assertGreaterEqual(
            results[0]["match_score"], results[1]["match_score"],
        )

    def test_empty_dataset(self):
        self._write_json(self.profile_path, TEST_PROFILE)
        self._write_json(self.jobs_path, [])
        results = match_jobs(self.profile_path, self.jobs_path)
        self.assertEqual(results, [])

    def test_invalid_profile_type(self):
        self._write_json(self.profile_path, [1, 2, 3])
        self._write_json(self.jobs_path, [make_job()])
        with self.assertRaises(ValueError):
            match_jobs(self.profile_path, self.jobs_path)

    def test_invalid_jobs_type(self):
        self._write_json(self.profile_path, TEST_PROFILE)
        self._write_json(self.jobs_path, {"not": "a list"})
        with self.assertRaises(ValueError):
            match_jobs(self.profile_path, self.jobs_path)


if __name__ == "__main__":
    unittest.main()
