from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _load_generate_scene():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("csp_generate_scene_test_module", SCRIPTS / "generate_scene.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load scripts/generate_scene.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GenerateScenePollingTests(unittest.TestCase):
    def test_history_read_timeout_is_retried_until_result_arrives(self) -> None:
        module = _load_generate_scene()
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "prompt-123": {
                "status": {"status_str": "success"},
                "outputs": {"9": {"images": [{"filename": "scene.png"}]}},
            }
        }

        with patch.object(
            module.requests,
            "get",
            side_effect=[requests.exceptions.ReadTimeout("GPU busy"), response],
        ) as get_mock, patch.object(module.time, "sleep", return_value=None):
            history = module._resilient_wait_history(
                "http://127.0.0.1:8188",
                "prompt-123",
                timeout=120,
                poll=1,
            )

        self.assertEqual(history["status"]["status_str"], "success")
        self.assertEqual(get_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
