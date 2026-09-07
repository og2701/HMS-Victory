from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
WRITING_SOURCE = "\n".join(
    (ROOT / path).read_text(encoding="utf-8")
    for path in ("commands/social/roast.py", "lib/features/roasts.py",
                 "commands/social/glaze.py", "lib/features/glazes.py", "lib/features/member_context.py")
)


class MemberWritingPromptTestCase(unittest.TestCase):
    def test_roast_has_no_public_progress_placeholder(self) -> None:
        self.assertNotIn("thinking_messages", WRITING_SOURCE)
        self.assertNotIn("thinking_text", WRITING_SOURCE)
        self.assertNotIn("random.choice", WRITING_SOURCE)

    def test_roast_prompt_contains_no_seed_dialogue_examples(self) -> None:
        seeded_dialogue = re.compile(
            r"(?i)\b(?:good|bad)\s+examples?\b|\bfor\s+example\b|\be\.g\.|"
            r"\b(?:say|write|reply|respond)\s+(?:exactly\s+)?['\"]"
        )
        self.assertIsNone(seeded_dialogue.search(WRITING_SOURCE))


if __name__ == "__main__":
    unittest.main()
