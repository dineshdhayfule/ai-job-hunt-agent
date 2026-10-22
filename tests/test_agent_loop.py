"""Tests for the multi-round agent execution loop."""

import json
import unittest
from unittest.mock import MagicMock, patch, PropertyMock
from types import SimpleNamespace


# ---------------------------------------------------------------------------
# Helper to build mock Gemini responses
# ---------------------------------------------------------------------------

def make_text_response(text: str) -> MagicMock:
    """Create a mock Gemini response with text only (no tool calls)."""
    response = MagicMock()
    response.function_calls = []
    response.text = text
    content = MagicMock()
    content.role = "model"
    content.parts = [MagicMock()]
    candidate = MagicMock()
    candidate.content = content
    response.candidates = [candidate]
    return response


def make_tool_call_response(*calls) -> MagicMock:
    """Create a mock Gemini response with tool calls.

    Each call should be (name, args_dict).
    """
    response = MagicMock()
    function_calls = []
    for name, args in calls:
        fc = MagicMock()
        fc.name = name
        fc.args = args
        function_calls.append(fc)
    response.function_calls = function_calls
    response.text = None
    content = MagicMock()
    content.role = "model"
    content.parts = function_calls[:]
    candidate = MagicMock()
    candidate.content = content
    response.candidates = [candidate]
    return response


# ---------------------------------------------------------------------------
# Test the execute_tool function
# ---------------------------------------------------------------------------

class TestExecuteTool(unittest.TestCase):
    """Test the tool dispatch and result formatting."""

    def test_unknown_tool(self):
        from agent import execute_tool
        result = execute_tool("nonexistent_tool", {})
        self.assertFalse(result["success"])
        self.assertIn("Unknown tool", result["error"])

    def test_search_jobs_success(self):
        from agent import execute_tool, TOOL_REGISTRY
        mock_search = MagicMock(
            return_value={"count": 2, "jobs": [{"id": 1}, {"id": 2}]}
        )
        original = TOOL_REGISTRY["search_jobs"]
        TOOL_REGISTRY["search_jobs"] = mock_search
        try:
            result = execute_tool("search_jobs", {"skills": ["Python"]})
            self.assertTrue(result.get("success", True))
            mock_search.assert_called_once_with(skills=["Python"])
        finally:
            TOOL_REGISTRY["search_jobs"] = original

    @patch("agent.get_memory")
    def test_get_memory_success(self, mock_get):
        from agent import execute_tool
        mock_get.return_value = {"minimum_match_score": 75}
        result = execute_tool("get_memory", {})
        self.assertTrue(result.get("success", True))

    @patch("agent.update_preferences")
    def test_update_preferences_success(self, mock_update):
        from agent import execute_tool
        mock_update.return_value = {"minimum_match_score": 80}
        result = execute_tool("update_preferences", {"minimum_match_score": 80})
        self.assertTrue(result.get("success", True))

    @patch("agent.update_preferences")
    def test_update_preferences_validation_error(self, mock_update):
        from agent import execute_tool
        mock_update.side_effect = ValueError("Invalid field")
        result = execute_tool("update_preferences", {"bad_field": "x"})
        self.assertFalse(result["success"])
        self.assertIn("Validation error", result["error"])

    @patch("agent.search_jobs")
    def test_type_error_handling(self, mock_search):
        from agent import execute_tool
        mock_search.side_effect = TypeError("unexpected arg")
        result = execute_tool("search_jobs", {"invalid_arg": 123})
        self.assertFalse(result["success"])
        self.assertIn("Invalid arguments", result["error"])

    @patch("agent.record_search")
    def test_record_search_success(self, mock_record):
        from agent import execute_tool
        mock_record.return_value = {"query": "test", "timestamp": "2026-01-01"}
        result = execute_tool("record_search", {
            "query": "Backend jobs",
            "results_count": 5,
        })
        self.assertTrue(result.get("success", True))


# ---------------------------------------------------------------------------
# Test the multi-round agent loop
# ---------------------------------------------------------------------------

class TestRunAgent(unittest.TestCase):
    """Test the bounded multi-round agent execution."""

    @patch("agent.client")
    def test_no_tool_calls_returns_text(self, mock_client):
        """A response with no tool calls should print text directly."""
        from agent import run_agent

        mock_client.models.generate_content.return_value = (
            make_text_response("Here are some tips for your job search.")
        )

        # Should not raise.
        run_agent("Tell me about job searching.")
        mock_client.models.generate_content.assert_called_once()

    @patch("agent.client")
    @patch("agent.execute_tool")
    def test_one_tool_call_then_answer(self, mock_exec, mock_client):
        """One tool call followed by a final text response."""
        from agent import run_agent

        # First call returns a tool request.
        tool_response = make_tool_call_response(
            ("get_memory", {}),
        )
        # Second call returns text.
        text_response = make_text_response(
            "Your minimum score is 75%."
        )

        mock_client.models.generate_content.side_effect = [
            tool_response, text_response,
        ]
        mock_exec.return_value = {
            "success": True,
            "memory": {"minimum_match_score": 75},
        }

        run_agent("What's my minimum match score?")
        self.assertEqual(mock_client.models.generate_content.call_count, 2)

    @patch("agent.client")
    @patch("agent.execute_tool")
    def test_multiple_tools_single_response(self, mock_exec, mock_client):
        """Multiple tool calls in one model response."""
        from agent import run_agent

        tool_response = make_tool_call_response(
            ("get_memory", {}),
            ("search_jobs", {"skills": ["Python"]}),
        )
        text_response = make_text_response("Found 2 jobs.")

        mock_client.models.generate_content.side_effect = [
            tool_response, text_response,
        ]
        mock_exec.side_effect = [
            {"success": True, "memory": {}},
            {"success": True, "count": 2, "jobs": []},
        ]

        run_agent("Search Python jobs with my preferences.")
        self.assertEqual(mock_exec.call_count, 2)

    @patch("agent.client")
    @patch("agent.execute_tool")
    def test_multi_round_chaining(self, mock_exec, mock_client):
        """Tool calls across multiple rounds."""
        from agent import run_agent

        # Round 1: get_memory
        round1 = make_tool_call_response(("get_memory", {}))
        # Round 2: search based on memory
        round2 = make_tool_call_response(
            ("search_live_jobs", {"search_query": "backend"}),
        )
        # Round 3: final answer
        round3 = make_text_response("Found 5 backend jobs.")

        mock_client.models.generate_content.side_effect = [
            round1, round2, round3,
        ]
        mock_exec.side_effect = [
            {"success": True, "memory": {"minimum_match_score": 75}},
            {"success": True, "jobs": [], "count": 5},
        ]

        run_agent("Find live backend jobs matching my profile.")
        self.assertEqual(mock_client.models.generate_content.call_count, 3)

    @patch("agent.client")
    @patch("agent.execute_tool")
    def test_tool_error_returns_error_result(self, mock_exec, mock_client):
        """A tool that returns an error should be passed back to the model."""
        from agent import run_agent

        tool_response = make_tool_call_response(
            ("search_live_jobs", {}),
        )
        text_response = make_text_response(
            "Sorry, I couldn't reach the job sources."
        )

        mock_client.models.generate_content.side_effect = [
            tool_response, text_response,
        ]
        mock_exec.return_value = {
            "success": False,
            "error": "Connection timeout",
        }

        run_agent("Search live jobs.")
        # The error should be passed to the model, not crash.
        self.assertEqual(mock_client.models.generate_content.call_count, 2)

    @patch("agent.client")
    @patch("agent.execute_tool")
    def test_max_round_limit(self, mock_exec, mock_client):
        """Should stop after MAX_TOOL_ROUNDS."""
        from agent import run_agent, MAX_TOOL_ROUNDS

        # Every response is a tool call — should hit the limit.
        tool_response = make_tool_call_response(
            ("get_memory", {}),
        )
        mock_exec.return_value = {"success": True, "memory": {}}

        # MAX_TOOL_ROUNDS tool calls + 1 final forced text response.
        responses = [tool_response] * MAX_TOOL_ROUNDS
        responses.append(make_text_response("Budget exhausted."))
        mock_client.models.generate_content.side_effect = responses

        run_agent("Keep calling tools forever.")
        # Should have called at most MAX_TOOL_ROUNDS + 1 times.
        self.assertLessEqual(
            mock_client.models.generate_content.call_count,
            MAX_TOOL_ROUNDS + 1,
        )

    @patch("agent.client")
    def test_gemini_api_error(self, mock_client):
        """API errors should be handled gracefully."""
        from agent import run_agent

        mock_client.models.generate_content.side_effect = Exception(
            "API quota exceeded"
        )

        # Should not raise.
        run_agent("Find jobs.")

    @patch("agent.client")
    @patch("agent.execute_tool")
    def test_unknown_tool_request(self, mock_exec, mock_client):
        """Model requests a tool that doesn't exist."""
        from agent import run_agent

        tool_response = make_tool_call_response(
            ("delete_everything", {"confirm": True}),
        )
        text_response = make_text_response(
            "I don't have that capability."
        )

        mock_client.models.generate_content.side_effect = [
            tool_response, text_response,
        ]
        mock_exec.return_value = {
            "success": False,
            "error": "Unknown tool: delete_everything",
        }

        run_agent("Delete everything.")
        # The unknown tool error is returned to the model, not a crash.
        self.assertEqual(mock_client.models.generate_content.call_count, 2)

    @patch("agent.MAX_TOTAL_TOOL_CALLS", 2)
    @patch("agent.client")
    @patch("agent.execute_tool")
    def test_total_call_budget(self, mock_exec, mock_client):
        """Should enforce total tool call budget."""
        from agent import run_agent

        # Response with 3 tool calls, but budget is 2.
        tool_response = make_tool_call_response(
            ("get_memory", {}),
            ("search_jobs", {}),
            ("record_search", {"query": "test"}),
        )
        text_response = make_text_response("Done.")

        mock_client.models.generate_content.side_effect = [
            tool_response, text_response,
        ]
        mock_exec.side_effect = [
            {"success": True, "memory": {}},
            {"success": True, "count": 0, "jobs": []},
        ]

        run_agent("Do everything at once.")
        # Only 2 actual tool executions (3rd should be budget-exceeded).
        self.assertEqual(mock_exec.call_count, 2)

    @patch("agent.client")
    @patch("agent.execute_tool")
    def test_intermediate_text_not_duplicated_in_history(self, mock_exec, mock_client):
        """Intermediate assistant text is stripped from model history during tool rounds."""
        from agent import run_agent

        tool_response = MagicMock()
        fc = MagicMock()
        fc.name = "get_memory"
        fc.args = {}
        text_part = MagicMock()
        text_part.text = "I am checking your saved memory first."

        content = MagicMock()
        content.role = "model"
        content.parts = [text_part, fc]
        candidate = MagicMock()
        candidate.content = content
        tool_response.candidates = [candidate]
        tool_response.function_calls = [fc]
        tool_response.text = "I am checking your saved memory first."

        final_response = make_text_response("Here are the results.")

        mock_client.models.generate_content.side_effect = [
            tool_response,
            final_response,
        ]
        mock_exec.return_value = {"minimum_match_score": 75}

        run_agent("Check memory.")

        second_call_contents = mock_client.models.generate_content.call_args_list[1].kwargs["contents"]
        self.assertEqual(second_call_contents[0].role, "user")
        model_turn = second_call_contents[1]
        self.assertEqual(model_turn.role, "model")
        for part in model_turn.parts:
            self.assertFalse(isinstance(getattr(part, "text", None), str) and getattr(part, "text", ""))
        self.assertEqual(second_call_contents[2].role, "user")

    @patch("agent.client")
    @patch("agent.execute_tool")
    def test_tool_responses_correspond_to_function_calls(self, mock_exec, mock_client):
        """Every tool response corresponds to the correct function call in order."""
        from agent import run_agent

        tool_response = make_tool_call_response(
            ("get_memory", {}),
            ("search_jobs", {"skills": ["Java"]}),
        )
        text_response = make_text_response("Matched jobs found.")

        mock_client.models.generate_content.side_effect = [
            tool_response,
            text_response,
        ]
        mock_exec.side_effect = [
            {"minimum_match_score": 75},
            {"count": 1, "jobs": []},
        ]

        run_agent("Search Java jobs with preferences.")

        second_call_contents = mock_client.models.generate_content.call_args_list[1].kwargs["contents"]
        tool_turn = second_call_contents[2]
        self.assertEqual(tool_turn.role, "user")
        self.assertEqual(len(tool_turn.parts), 2)
        resp1 = tool_turn.parts[0].function_response
        resp2 = tool_turn.parts[1].function_response
        self.assertEqual(resp1.name, "get_memory")
        self.assertEqual(resp2.name, "search_jobs")

    @patch("agent.MAX_TOTAL_TOOL_CALLS", 1)
    @patch("agent.client")
    @patch("agent.execute_tool")
    def test_tool_budget_exhaustion_single_user_turn(self, mock_exec, mock_client):
        """Tool budget exhaustion does not create consecutive user-role messages."""
        from agent import run_agent

        tool_response = make_tool_call_response(
            ("get_memory", {}),
            ("search_jobs", {}),
        )
        final_response = make_text_response("Done within budget.")

        mock_client.models.generate_content.side_effect = [
            tool_response,
            final_response,
        ]
        mock_exec.return_value = {"minimum_match_score": 75}

        run_agent("Do tasks within budget.")

        second_call_contents = mock_client.models.generate_content.call_args_list[1].kwargs["contents"]
        roles = [c.role for c in second_call_contents]
        for i in range(len(roles) - 1):
            self.assertNotEqual(roles[i], roles[i + 1], "Consecutive same-role messages detected!")
        self.assertEqual(roles, ["user", "model", "user"])

    @patch("agent.client")
    @patch("agent.execute_tool")
    def test_round_limit_fallback_preserves_system_instruction(self, mock_exec, mock_client):
        """Round limit fallback preserves SYSTEM_INSTRUCTION instead of overwriting it."""
        from agent import run_agent, MAX_TOOL_ROUNDS, SYSTEM_INSTRUCTION

        tool_response = make_tool_call_response(("get_memory", {}))
        mock_exec.return_value = {"minimum_match_score": 75}

        responses = [tool_response] * MAX_TOOL_ROUNDS
        responses.append(make_text_response("Final fallback summary."))
        mock_client.models.generate_content.side_effect = responses

        run_agent("Loop indefinitely.")

        final_call_config = mock_client.models.generate_content.call_args_list[-1].kwargs["config"]
        self.assertEqual(final_call_config.system_instruction, SYSTEM_INSTRUCTION)

    def test_search_live_jobs_tool_schema_exposes_max_age_hours(self):
        """search_live_jobs_tool exposes max_age_hours parameter in schema."""
        from agent import search_live_jobs_tool
        props = search_live_jobs_tool.parameters.properties
        self.assertIn("max_age_hours", props)
        self.assertEqual(props["max_age_hours"].type, "INTEGER")

    @patch("agent.time.sleep")
    @patch("agent.client")
    def test_http_429_rate_limit_user_facing_message(self, mock_client, mock_sleep):
        """HTTP 429 rate limit produces clear, specific message without leaking secrets."""
        import io
        from contextlib import redirect_stdout
        from agent import run_agent

        err = Exception("429 RESOURCE_EXHAUSTED: Rate limit exceeded")
        err.code = 429
        mock_client.models.generate_content.side_effect = err

        buf = io.StringIO()
        with redirect_stdout(buf):
            run_agent("Find jobs")

        output = buf.getvalue()
        self.assertIn("[Agent error: Gemini API rate limit exceeded (HTTP 429).", output)
        self.assertNotIn("API key", output)

    @patch("agent.client")
    def test_http_429_daily_quota_user_facing_message(self, mock_client):
        """HTTP 429 daily quota exhaustion produces distinct actionable message."""
        import io
        from contextlib import redirect_stdout
        from agent import run_agent

        err = Exception("Quota exceeded: Daily request quota limit per day reached")
        err.code = 429
        mock_client.models.generate_content.side_effect = err

        buf = io.StringIO()
        with redirect_stdout(buf):
            run_agent("Find jobs")

        output = buf.getvalue()
        self.assertIn("[Agent error: Gemini API daily quota exhausted (HTTP 429).", output)

    @patch("agent.client")
    def test_non_429_error_handling_preserved(self, mock_client):
        """Non-429 errors retain generic could not reach Gemini message with error type."""
        import io
        from contextlib import redirect_stdout
        from agent import run_agent

        mock_client.models.generate_content.side_effect = ValueError("Corrupt response format")

        buf = io.StringIO()
        with redirect_stdout(buf):
            run_agent("Find jobs")

        output = buf.getvalue()
        self.assertIn("[Agent error: could not reach Gemini (ValueError)]", output)

    @patch("agent.time.sleep")
    @patch("agent.client")
    def test_rate_limit_retry_bounded_with_mocks(self, mock_client, mock_sleep):
        """Transient 429 retries with bounded exponential backoff up to max_retries."""
        from agent import _call_gemini_with_retry

        err_429 = Exception("429 Too Many Requests")
        err_429.code = 429

        success_resp = make_text_response("Success after retry")
        # First call fails with 429, second call succeeds
        mock_client.models.generate_content.side_effect = [err_429, success_resp]

        res = _call_gemini_with_retry(
            client=mock_client,
            model="gemini-2.5-flash",
            contents=[],
            config=None,
            max_retries=2,
            initial_delay=1.0,
        )
        self.assertEqual(res.text, "Success after retry")
        mock_sleep.assert_called_once_with(1.0)
        self.assertEqual(mock_client.models.generate_content.call_count, 2)


if __name__ == "__main__":
    unittest.main()

