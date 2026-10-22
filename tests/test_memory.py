"""Tests for the persistent memory system (tools/memory.py)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# We need to patch the MEMORY_PATH before importing the module.
_temp_dir = None
_original_path = None


def setUpModule():
    """Create a temp directory for test memory files."""
    global _temp_dir
    _temp_dir = tempfile.mkdtemp()


def tearDownModule():
    """Clean up the temp directory."""
    import shutil
    if _temp_dir and os.path.exists(_temp_dir):
        shutil.rmtree(_temp_dir)


class MemoryTestBase(unittest.TestCase):
    """Base class that redirects memory operations to a temp file."""

    def setUp(self):
        """Create a fresh temp memory file for each test."""
        self.temp_dir = tempfile.mkdtemp()
        self.memory_path = Path(self.temp_dir) / "memory.json"

        # Patch the module-level MEMORY_PATH.
        self.patcher = patch("tools.memory.MEMORY_PATH", self.memory_path)
        self.patcher.start()

    def tearDown(self):
        """Clean up temp files."""
        self.patcher.stop()
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def _write_memory(self, data: dict) -> None:
        """Write test memory data."""
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)
        with self.memory_path.open("w", encoding="utf-8") as f:
            json.dump(data, f)

    def _read_memory(self) -> dict:
        """Read memory data from disk."""
        with self.memory_path.open("r", encoding="utf-8") as f:
            return json.load(f)


class TestLoadMemory(MemoryTestBase):
    """Test loading memory from disk."""

    def test_creates_default_when_missing(self):
        from tools.memory import load_memory
        memory = load_memory()
        self.assertIsInstance(memory, dict)
        self.assertIn("preferred_roles", memory)
        self.assertIn("search_history", memory)
        self.assertEqual(memory["minimum_match_score"], 60)
        # File should now exist.
        self.assertTrue(self.memory_path.exists())

    def test_loads_existing_file(self):
        from tools.memory import load_memory
        self._write_memory({
            "preferred_roles": ["Backend Developer"],
            "preferred_locations": ["Mumbai"],
            "minimum_match_score": 75,
            "skills_to_learn": [],
            "excluded_companies": [],
            "search_history": [],
        })
        memory = load_memory()
        self.assertEqual(memory["minimum_match_score"], 75)
        self.assertEqual(memory["preferred_roles"], ["Backend Developer"])

    def test_adds_missing_fields(self):
        from tools.memory import load_memory
        # Write memory with only some fields.
        self._write_memory({
            "preferred_roles": ["Dev"],
            "minimum_match_score": 80,
        })
        memory = load_memory()
        # Missing fields should have defaults.
        self.assertIn("preferred_locations", memory)
        self.assertIsInstance(memory["preferred_locations"], list)
        self.assertIn("search_history", memory)
        # Existing values preserved.
        self.assertEqual(memory["preferred_roles"], ["Dev"])
        self.assertEqual(memory["minimum_match_score"], 80)

    def test_rejects_non_dict(self):
        from tools.memory import load_memory
        self._write_memory([1, 2, 3])  # type: ignore
        with self.assertRaises(ValueError):
            load_memory()

    def test_handles_malformed_json(self):
        from tools.memory import load_memory
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)
        with self.memory_path.open("w") as f:
            f.write("{invalid json")
        with self.assertRaises(json.JSONDecodeError):
            load_memory()


class TestUpdatePreferences(MemoryTestBase):
    """Test updating user preferences."""

    def test_update_single_preference(self):
        from tools.memory import update_preferences
        self._write_memory({
            "preferred_roles": ["Dev"],
            "preferred_locations": ["Remote"],
            "minimum_match_score": 60,
            "skills_to_learn": [],
            "excluded_companies": [],
            "search_history": [{"query": "test"}],
        })
        result = update_preferences({"minimum_match_score": 80})
        self.assertEqual(result["minimum_match_score"], 80)
        # Other fields preserved.
        self.assertEqual(result["preferred_roles"], ["Dev"])
        self.assertEqual(result["preferred_locations"], ["Remote"])
        self.assertEqual(len(result["search_history"]), 1)

    def test_update_multiple_preferences(self):
        from tools.memory import update_preferences
        self._write_memory({
            "preferred_roles": [],
            "preferred_locations": [],
            "minimum_match_score": 60,
            "skills_to_learn": [],
            "excluded_companies": [],
            "search_history": [],
        })
        result = update_preferences({
            "preferred_roles": ["Backend", "Frontend"],
            "preferred_locations": ["Mumbai", "Remote"],
            "minimum_match_score": 90,
        })
        self.assertEqual(result["minimum_match_score"], 90)
        self.assertEqual(result["preferred_roles"], ["Backend", "Frontend"])
        self.assertEqual(result["preferred_locations"], ["Mumbai", "Remote"])

    def test_score_zero(self):
        from tools.memory import update_preferences
        self._write_memory({
            "preferred_roles": [],
            "preferred_locations": [],
            "minimum_match_score": 60,
            "skills_to_learn": [],
            "excluded_companies": [],
            "search_history": [],
        })
        result = update_preferences({"minimum_match_score": 0})
        self.assertEqual(result["minimum_match_score"], 0)

    def test_score_100(self):
        from tools.memory import update_preferences
        self._write_memory({
            "preferred_roles": [],
            "preferred_locations": [],
            "minimum_match_score": 60,
            "skills_to_learn": [],
            "excluded_companies": [],
            "search_history": [],
        })
        result = update_preferences({"minimum_match_score": 100})
        self.assertEqual(result["minimum_match_score"], 100)

    def test_gemini_decimal_percentage_fix(self):
        """Gemini may send 0.75 when the user says 75%."""
        from tools.memory import update_preferences
        self._write_memory({
            "preferred_roles": [],
            "preferred_locations": [],
            "minimum_match_score": 60,
            "skills_to_learn": [],
            "excluded_companies": [],
            "search_history": [],
        })
        result = update_preferences({"minimum_match_score": 0.75})
        self.assertEqual(result["minimum_match_score"], 75)

    def test_rejects_boolean_score(self):
        from tools.memory import update_preferences
        self._write_memory({
            "preferred_roles": [],
            "preferred_locations": [],
            "minimum_match_score": 60,
            "skills_to_learn": [],
            "excluded_companies": [],
            "search_history": [],
        })
        with self.assertRaises(ValueError):
            update_preferences({"minimum_match_score": True})

    def test_rejects_negative_score(self):
        from tools.memory import update_preferences
        self._write_memory({
            "preferred_roles": [],
            "preferred_locations": [],
            "minimum_match_score": 60,
            "skills_to_learn": [],
            "excluded_companies": [],
            "search_history": [],
        })
        with self.assertRaises(ValueError):
            update_preferences({"minimum_match_score": -5})

    def test_rejects_score_above_100(self):
        from tools.memory import update_preferences
        self._write_memory({
            "preferred_roles": [],
            "preferred_locations": [],
            "minimum_match_score": 60,
            "skills_to_learn": [],
            "excluded_companies": [],
            "search_history": [],
        })
        with self.assertRaises(ValueError):
            update_preferences({"minimum_match_score": 150})

    def test_rejects_unsupported_fields(self):
        from tools.memory import update_preferences
        self._write_memory({
            "preferred_roles": [],
            "preferred_locations": [],
            "minimum_match_score": 60,
            "skills_to_learn": [],
            "excluded_companies": [],
            "search_history": [],
        })
        with self.assertRaises(ValueError):
            update_preferences({"favorite_color": "blue"})

    def test_rejects_empty_updates(self):
        from tools.memory import update_preferences
        self._write_memory({
            "preferred_roles": [],
            "preferred_locations": [],
            "minimum_match_score": 60,
            "skills_to_learn": [],
            "excluded_companies": [],
            "search_history": [],
        })
        with self.assertRaises(ValueError):
            update_preferences({})

    def test_rejects_malformed_list(self):
        from tools.memory import update_preferences
        self._write_memory({
            "preferred_roles": [],
            "preferred_locations": [],
            "minimum_match_score": 60,
            "skills_to_learn": [],
            "excluded_companies": [],
            "search_history": [],
        })
        with self.assertRaises(ValueError):
            update_preferences({"preferred_roles": "not a list"})

    def test_rejects_list_with_empty_strings(self):
        from tools.memory import update_preferences
        self._write_memory({
            "preferred_roles": [],
            "preferred_locations": [],
            "minimum_match_score": 60,
            "skills_to_learn": [],
            "excluded_companies": [],
            "search_history": [],
        })
        with self.assertRaises(ValueError):
            update_preferences({"preferred_roles": ["valid", ""]})

    def test_deduplicates_list_values(self):
        from tools.memory import update_preferences
        self._write_memory({
            "preferred_roles": [],
            "preferred_locations": [],
            "minimum_match_score": 60,
            "skills_to_learn": [],
            "excluded_companies": [],
            "search_history": [],
        })
        result = update_preferences({
            "preferred_roles": ["Dev", "Dev", "Backend", "Dev"],
        })
        self.assertEqual(result["preferred_roles"], ["Dev", "Backend"])

    def test_preserves_search_history(self):
        from tools.memory import update_preferences
        history = [{"query": "test1"}, {"query": "test2"}]
        self._write_memory({
            "preferred_roles": [],
            "preferred_locations": [],
            "minimum_match_score": 60,
            "skills_to_learn": [],
            "excluded_companies": [],
            "search_history": history,
        })
        result = update_preferences({"minimum_match_score": 90})
        self.assertEqual(len(result["search_history"]), 2)


class TestRecordSearch(MemoryTestBase):
    """Test search history recording."""

    def test_records_search(self):
        from tools.memory import record_search
        self._write_memory({
            "preferred_roles": [],
            "preferred_locations": [],
            "minimum_match_score": 60,
            "skills_to_learn": [],
            "excluded_companies": [],
            "search_history": [],
        })
        entry = record_search("Backend jobs in Mumbai", {"location": "Mumbai"}, 5)
        self.assertEqual(entry["query"], "Backend jobs in Mumbai")
        self.assertEqual(entry["results_count"], 5)
        self.assertIn("timestamp", entry)

    def test_rejects_empty_query(self):
        from tools.memory import record_search
        self._write_memory({
            "preferred_roles": [],
            "preferred_locations": [],
            "minimum_match_score": 60,
            "skills_to_learn": [],
            "excluded_companies": [],
            "search_history": [],
        })
        with self.assertRaises(ValueError):
            record_search("", None, 0)

    def test_rejects_negative_count(self):
        from tools.memory import record_search
        self._write_memory({
            "preferred_roles": [],
            "preferred_locations": [],
            "minimum_match_score": 60,
            "skills_to_learn": [],
            "excluded_companies": [],
            "search_history": [],
        })
        with self.assertRaises(ValueError):
            record_search("test", None, -1)

    def test_rejects_boolean_count(self):
        from tools.memory import record_search
        self._write_memory({
            "preferred_roles": [],
            "preferred_locations": [],
            "minimum_match_score": 60,
            "skills_to_learn": [],
            "excluded_companies": [],
            "search_history": [],
        })
        with self.assertRaises(ValueError):
            record_search("test", None, True)

    def test_caps_history_at_50(self):
        from tools.memory import record_search
        history = [{"query": f"search-{i}"} for i in range(50)]
        self._write_memory({
            "preferred_roles": [],
            "preferred_locations": [],
            "minimum_match_score": 60,
            "skills_to_learn": [],
            "excluded_companies": [],
            "search_history": history,
        })
        record_search("new search", None, 1)
        data = self._read_memory()
        self.assertEqual(len(data["search_history"]), 50)
        self.assertEqual(data["search_history"][-1]["query"], "new search")

    def test_persists_across_loads(self):
        from tools.memory import record_search, load_memory
        self._write_memory({
            "preferred_roles": [],
            "preferred_locations": [],
            "minimum_match_score": 60,
            "skills_to_learn": [],
            "excluded_companies": [],
            "search_history": [],
        })
        record_search("first search", None, 3)
        record_search("second search", None, 7)
        memory = load_memory()
        self.assertEqual(len(memory["search_history"]), 2)
        self.assertEqual(memory["search_history"][0]["query"], "first search")
        self.assertEqual(memory["search_history"][1]["query"], "second search")


if __name__ == "__main__":
    unittest.main()
