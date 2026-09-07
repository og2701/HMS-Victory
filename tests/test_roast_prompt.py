from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
ROAST_SOURCE = "\n".join(
    (ROOT / path).read_text(encoding="utf-8")
    for path in ("commands/social/roast.py", "lib/features/roasts.py")
)


class RoastPromptTestCase(unittest.TestCase):
    def test_roast_has_no_public_progress_placeholder(self) -> None:
        self.assertNotIn("thinking_messages", ROAST_SOURCE)
        self.assertNotIn("thinking_text", ROAST_SOURCE)
        self.assertNotIn("random.choice", ROAST_SOURCE)

    def test_roast_prompt_contains_no_seed_dialogue_examples(self) -> None:
        seeded_dialogue = re.compile(
            r"(?i)\b(?:good|bad)\s+examples?\b|\bfor\s+example\b|\be\.g\.|"
            r"\b(?:say|write|reply|respond)\s+(?:exactly\s+)?['\"]"
        )
        self.assertIsNone(seeded_dialogue.search(ROAST_SOURCE))


if __name__ == "__main__":
    unittest.main()
