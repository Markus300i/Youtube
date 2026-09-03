from __future__ import annotations

import unittest

from csp_studio.visual_qa import VisualQA


class VisualQAPlaceholderTests(unittest.TestCase):
    def test_specific_issue_placeholder_is_dropped(self) -> None:
        notes = VisualQA._normalize_notes(
            {
                "scene_notes": [
                    {
                        "scene_id": 4,
                        "severity": "warning",
                        "issue": "specific issue",
                        "recommendation": "specific fix",
                    }
                ]
            }
        )
        self.assertEqual(notes, [])

    def test_real_scene_note_is_preserved(self) -> None:
        notes = VisualQA._normalize_notes(
            {
                "scene_notes": [
                    {
                        "scene_id": 4,
                        "severity": "warning",
                        "issue": "Tenant hand has six fingers.",
                        "recommendation": "Regenerate the subject area.",
                    }
                ]
            }
        )
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].scene_id, 4)
        self.assertIn("six fingers", notes[0].issue)


if __name__ == "__main__":
    unittest.main()
