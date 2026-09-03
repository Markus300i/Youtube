from __future__ import annotations

import unittest

from csp_studio.wizard_v2 import WizardV2Error, _extract_json_object


class WizardJsonExtractTests(unittest.TestCase):
    def test_valid_json_with_trailing_text_returns_first_object(self) -> None:
        payload = _extract_json_object('prefix {"project":{"id":"one"}} trailing explanation')
        self.assertEqual(payload, {"project": {"id": "one"}})

    def test_valid_json_with_second_json_returns_first_object(self) -> None:
        payload = _extract_json_object('{"project":{"id":"first"}} {"project":{"id":"second"}}')
        self.assertEqual(payload, {"project": {"id": "first"}})

    def test_broken_first_json_still_raises(self) -> None:
        with self.assertRaises(WizardV2Error):
            _extract_json_object('{"project":{"id":"broken"} trailing {"project":{"id":"second"}}')


if __name__ == "__main__":
    unittest.main()
