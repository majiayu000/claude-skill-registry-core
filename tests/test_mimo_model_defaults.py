"""Offline regression coverage for the current MiMo model and bounded requests."""

from __future__ import annotations

import importlib
import json
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

reviewer = importlib.import_module("review_category_plan_with_llm")
classifier = importlib.import_module("classify_residual_workset_with_llm")
builder = importlib.import_module("build_current_other_reclassification_batch")


class TestMimoModelDefaults(unittest.TestCase):
    def test_both_clis_default_to_current_model_with_bounded_requests(self):
        for module, required in (
            (reviewer, ["--plan", "plan.json"]),
            (classifier, ["--workset-jsonl", "input.jsonl"]),
        ):
            with self.subTest(module=module.__name__):
                args = module.parse_args(required)
                self.assertEqual(args.model, "mimo-v2.6-pro")
                self.assertEqual(args.max_completion_tokens, 1024)
                self.assertEqual(args.thinking, "disabled")
                self.assertEqual(args.temperature, 0.0)
                override = module.parse_args(required + ["--model", "custom-model"])
                self.assertEqual(override.model, "custom-model")

    def test_default_client_sends_current_model_and_explicit_budget(self):
        with patch.object(reviewer, "urlopen") as mocked_urlopen:
            response = mocked_urlopen.return_value.__enter__.return_value
            response.read.return_value = json.dumps(
                {"choices": [{"message": {"content": "{}"}}]}
            ).encode("utf-8")
            client = reviewer.OpenAICompatibleClient(api_key="offline-test-key")
            self.assertEqual(client.complete([{"role": "user", "content": "test"}]), "{}")
            request = mocked_urlopen.call_args.args[0]
            payload = json.loads(request.data)
            self.assertEqual(payload["model"], "mimo-v2.6-pro")
            self.assertEqual(payload["max_completion_tokens"], 1024)
            self.assertEqual(payload["thinking"], {"type": "disabled"})
            self.assertEqual(payload["temperature"], 0.0)
            self.assertFalse(payload["stream"])

    def test_new_batch_command_uses_current_model_and_preserves_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skills_dir = root / "skills"
            skills_dir.mkdir()
            manifest = builder.build_batch(
                skills_dir=skills_dir, output_dir=root / "batch", batch_id="offline-test"
            )
            command = shlex.split(manifest["commands"][0])
            self.assertEqual(command[:2], ["python", "scripts/classify_residual_workset_with_llm.py"])
            args = classifier.parse_args(command[2:])
            self.assertEqual(args.model, "mimo-v2.6-pro")
            self.assertEqual(args.max_completion_tokens, 1024)
            self.assertEqual(args.thinking, "disabled")
            self.assertEqual(args.temperature, 0.0)
            self.assertEqual(manifest["policy"]["apply_mode"], "review-only")


if __name__ == "__main__":
    unittest.main()
