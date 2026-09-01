import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.core.whitespace_moderation import is_excessive_whitespace


def test_ihsan_actual_nuke_message():
    # 1,517 newlines with _ _ spacers
    content = "_ _" + ("\n" * 1517) + "_ _"
    blocked, reason = is_excessive_whitespace(content)
    assert blocked, "Ihsan actual nuke message should be blocked"
    assert "1517" in reason


def test_consecutive_blank_lines():
    # 20 consecutive empty lines
    content = "Hello\n" + ("\n" * 18) + "World"
    blocked, reason = is_excessive_whitespace(content)
    assert blocked, "20 consecutive empty lines should be blocked"


def test_consecutive_spacer_lines():
    # Lines with _ _ markdown spacers and zero-width spaces
    content = "\n".join(["_ _", "\u200b", "_ _", "   ", "_ _"] * 4)
    blocked, reason = is_excessive_whitespace(content)
    assert blocked, "Consecutive spacer lines should be blocked"


def test_massive_whitespace_flood():
    # 300 spaces / zero width chars with no real text
    content = (" " * 200) + ("\u200b" * 100) + "test"
    blocked, reason = is_excessive_whitespace(content)
    assert blocked, "Massive whitespace flood should be blocked"


def test_normal_messages_are_not_blocked():
    # Normal short message
    assert not is_excessive_whitespace("Hello world!")[0]

    # Normal multi-line message (e.g. 5 lines)
    multi_line = "Line 1\nLine 2\n\nLine 3\nLine 4"
    assert not is_excessive_whitespace(multi_line)[0]

    # Code block with 30 lines of actual code
    code = "```python\n" + "\n".join(f"x = {i}" for i in range(30)) + "\n```"
    assert not is_excessive_whitespace(code)[0]


def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith('test_') and callable(f)]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f'PASS  {name}')
            passed += 1
        except AssertionError as e:
            print(f'FAIL  {name}: {e}')
        except Exception as e:
            import traceback
            print(f'ERROR {name}: {e!r}')
    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == '__main__':
    sys.exit(0 if _run_all() else 1)
