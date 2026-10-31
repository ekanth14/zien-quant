"""Formula grounding and local-only model configuration contracts."""

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from marketscope.quant_knowledge import formula_system_prompt, ranking_context

spec = importlib.util.spec_from_file_location("configure_quant_models", ROOT / "scripts" / "configure_quant_models.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class QuantKnowledgeTests(unittest.TestCase):
    def test_reference_separates_formulas_from_forecasting_claims(self):
        context = formula_system_prompt()
        for term in ("Wilder", "Supertrend", "Camarilla", "VWAP", "untrusted data", "implementation heuristic"):
            self.assertIn(term, context)
        self.assertIn("do not constitute a trained forecasting model", context.replace("\n", " "))

    def test_ranking_context_has_only_supplied_bounded_data(self):
        self.assertIn("unavailable", ranking_context(None))
        text = ranking_context({"best": {"symbol": "ABC", "score": 83}, "secret": "do not include", "worst": {"score": float("nan")}})
        self.assertNotIn("secret", text)
        self.assertNotIn("NaN", text)
        self.assertEqual(json.loads(text.split("\n", 1)[1])["best"]["symbol"], "ABC")

    def test_external_credentialed_and_ambiguous_hosts_are_rejected(self):
        for host in ("https://127.0.0.1:11434", "http://example.com:11434", "http://user:pw@localhost:11434", "http://127.0.0.1:11434/api", "http://localhost", "http://127.0.0.1:11434/?x=1"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                setup.local_host(host)
        self.assertEqual(setup.local_host("http://[::1]:11434/"), "http://[::1]:11434")

    def test_dry_run_skips_decision_model_and_never_creates_models(self):
        def request(host, path, body=None):
            if path == "/api/tags":
                return {"models": [{"name": "gemma4:12b"}, {"name": "laya:latest"}]}
            if path == "/api/show":
                return {"capabilities": ["decision"] if body["model"] == "laya:latest" else ["completion"]}
            self.fail("Dry run attempted a mutation")
        with patch.object(setup, "request_json", side_effect=request):
            result = setup.configure("http://127.0.0.1:11434")
        self.assertFalse(result["weights_trained"])
        self.assertIsNone(result["primary_model"])
        self.assertIn({"source": "gemma4:12b", "target": "laya-quant:latest", "status": "planned"}, result["models"])

    def test_apply_verifies_new_variant_and_preserves_sources(self):
        mutations = []
        def request(host, path, body=None):
            if path == "/api/tags":
                return {"models": [{"name": "gemma4:12b"}]}
            if path == "/api/show":
                return {"capabilities": ["completion"], "system": formula_system_prompt()}
            if path == "/api/create":
                mutations.append(body)
                return {"status": "success"}
            self.fail(path)
        with patch.object(setup, "request_json", side_effect=request):
            result = setup.configure("http://127.0.0.1:11434", apply=True)
        self.assertEqual(result["primary_model"], "laya-quant:latest")
        self.assertEqual(len(mutations), 2)
        for body in mutations:
            self.assertNotEqual(body["model"], body["from"])
            self.assertNotIn("template", body)
            self.assertNotIn("files", body)

    def test_existing_variant_with_other_settings_is_never_overwritten(self):
        def request(host, path, body=None):
            if path == "/api/tags":
                return {"models": [{"name": "gemma4:12b"}, {"name": "gemma4-quant:12b"}, {"name": "laya-quant:latest"}]}
            if path == "/api/show":
                return {"capabilities": ["completion"], "system": "other custom instructions"}
            self.fail("Unexpected overwrite")
        with patch.object(setup, "request_json", side_effect=request):
            result = setup.configure("http://127.0.0.1:11434", apply=True)
        self.assertIsNone(result["primary_model"])
        self.assertTrue(all(row["status"] == "retained_existing" for row in result["models"]))


if __name__ == "__main__":
    unittest.main()

