import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image
from lib.core.blocked_media import (
    compute_frame_dhash,
    hamming_distance,
    is_blocked_image_bytes,
    BLOCKED_DOG_GIF_DHASHES,
    MAX_HAMMING_DISTANCE,
)


def test_dhash_on_target_fingerprint():
    # Target frame 0 hash
    target = BLOCKED_DOG_GIF_DHASHES[0]
    assert hamming_distance(target, target) == 0


def test_unrelated_image_is_not_blocked():
    # Create solid red image
    img = Image.new("RGB", (200, 200), color="red")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    blocked, reason = is_blocked_image_bytes(buf.getvalue())
    assert not blocked, f"Unrelated image was falsely blocked: {reason}"


def test_gradient_image_is_not_blocked():
    img = Image.linear_gradient("L")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    blocked, reason = is_blocked_image_bytes(buf.getvalue())
    assert not blocked, f"Gradient image was falsely blocked: {reason}"


def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {name}: {e}")
        except Exception as e:
            import traceback
            print(f"ERROR {name}: {e!r}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
