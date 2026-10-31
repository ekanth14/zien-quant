"""Guard the numerical ranking boundary and session-specific commentary."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from laya_ai import LayaAIEngine, _analysis_key, _pulse_key, daily_ranking_reply
from quant_knowledge import formula_system_prompt


def ranking(day="2026-10-23"):
    return {"formula_version": "pdf-quant-1", "session_date": day,
            "eligible_count": 2, "excluded_count": 1,
            "best": {"symbol": "AAA", "score": 76.25, "as_of": day + "T10:00:00+00:00"},
            "worst": {"symbol": "BBB", "score": 23.75, "as_of": day + "T10:00:00+00:00"}}


class LayaDailyRankingTests(unittest.TestCase):
    def test_ranking_query_uses_exact_engine_result_without_model_choice(self):
        engine = LayaAIEngine()
        with patch.object(engine, "_call_ollama") as call:
            result = engine.chat_copilot("Which are the best and worst stocks today?",
                                         snapshot_data={"daily_ranking": ranking()})
        call.assert_not_called()
        self.assertIn("AAA", result["reply"])
        self.assertIn("76.25", result["reply"])
        self.assertIn("BBB", result["reply"])
        self.assertEqual(result["source"], "rules_engine")

    def test_unavailable_ranking_cannot_produce_a_substitute_winner(self):
        result = daily_ranking_reply({"session_date": "2026-10-23", "reason": "Missing volume", "eligible_count": 0})
        self.assertIn("no distinct best/worst pair", result)
        self.assertIn("Missing volume", result)

    def test_session_and_calculation_change_invalidates_commentary_identity(self):
        a = {"session_date": "2026-10-23", "price": 100, "daily_quant": {"score": 70}}
        b = dict(a, session_date="2026-10-26")
        c = dict(a, daily_quant={"score": 71})
        self.assertNotEqual(_analysis_key("AAA", 5, a), _analysis_key("AAA", 5, b))
        self.assertNotEqual(_analysis_key("AAA", 5, a), _analysis_key("AAA", 5, c))
        self.assertNotEqual(_pulse_key({"daily_ranking": ranking()}), _pulse_key({"daily_ranking": ranking("2026-10-26")}))

    def test_completion_payload_contains_formula_context_for_every_selected_model(self):
        engine = LayaAIEngine(model="gemma4:12b")
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"response":"The supplied ranking is descriptive."}'
        with patch("urllib.request.urlopen", return_value=response) as request:
            engine._call_ollama("Explain the supplied ranking", num_ctx=2048)
        payload = json.loads(request.call_args.args[0].data)
        self.assertEqual(payload["system"], formula_system_prompt())
        self.assertGreaterEqual(payload["options"]["num_ctx"], 4096)


if __name__ == "__main__":
    unittest.main()

