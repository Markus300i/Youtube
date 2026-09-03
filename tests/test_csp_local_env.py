from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from csp_studio.local_env import get_local_setting, setting_source
from csp_studio.providers.nvidia_nim import nvidia_nim_status


class LocalEnvTests(unittest.TestCase):
    def test_local_env_file_is_used_when_process_env_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                "NVIDIA_API_KEY=local-secret\nCSP_NIM_MODEL=local/model\nIGNORED_SECRET=nope\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"CSP_ENV_FILE": str(env_file)},
                clear=False,
            ):
                os.environ.pop("NVIDIA_API_KEY", None)
                os.environ.pop("CSP_NIM_MODEL", None)
                self.assertEqual(get_local_setting("NVIDIA_API_KEY"), "local-secret")
                self.assertEqual(setting_source("NVIDIA_API_KEY"), ".env")
                self.assertEqual(get_local_setting("CSP_NIM_MODEL"), "local/model")
                self.assertIsNone(get_local_setting("IGNORED_SECRET"))

    def test_process_env_takes_precedence_and_status_never_exposes_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("NVIDIA_API_KEY=file-secret\n", encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "CSP_ENV_FILE": str(env_file),
                    "NVIDIA_API_KEY": "process-secret",
                    "CSP_NIM_MODEL": "test/model",
                },
                clear=False,
            ):
                self.assertEqual(get_local_setting("NVIDIA_API_KEY"), "process-secret")
                self.assertEqual(setting_source("NVIDIA_API_KEY"), "environment")
                status = nvidia_nim_status()
                self.assertTrue(status["configured"])
                self.assertEqual(status["api_key_source"], "environment")
                self.assertEqual(status["chat_model"], "test/model")
                rendered = repr(status)
                self.assertNotIn("process-secret", rendered)
                self.assertNotIn("file-secret", rendered)


if __name__ == "__main__":
    unittest.main()
