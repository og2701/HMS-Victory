from lib.core.image_processing import _screenshot_html_sync, get_browser, _render_count
import time

print("Testing browser render count...")
print(f"Initial render count: {_render_count}")
for i in range(16):
    _screenshot_html_sync("<h1>Test</h1>", (100, 100))
    print(f"Render {i+1}: Count is now {_render_count}")
    
print("\nTesting idle timeout...")
print("Waiting 190 seconds (more than 3 minutes)...")
# Time travel hacking for test: manually set the last render time
from lib.core import image_processing
image_processing._last_render_time = time.time() - 200
_screenshot_html_sync("<h1>Test Timeout</h1>", (100, 100))
print(f"Render after timeout: Count is now {_render_count}")
