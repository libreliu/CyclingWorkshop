"""SharedFrameBuffer 资源释放回归测试。"""

import os
import sys
import unittest

import numpy as np
from PIL import Image


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from services.render_services import SharedFrameBuffer


class TestSharedFrameBuffer(unittest.TestCase):
    def test_close_after_releasing_frame_view(self):
        buf = SharedFrameBuffer(None, width=8, height=6, channels=4, n_slots=2)
        try:
            view = buf.get_frame_view(0)
            view[:] = 7
            del view

            # 不应抛出 BufferError
            buf.close()
            buf.unlink()
        finally:
            # 确保异常时也尽量清理
            try:
                buf.close()
            except Exception:
                pass
            try:
                buf.unlink()
            except Exception:
                pass

    def test_pil_copy_detaches_from_shared_memory_view(self):
        buf = SharedFrameBuffer(None, width=4, height=4, channels=4, n_slots=1)
        try:
            view = buf.get_frame_view(0)
            view[:] = np.arange(view.size, dtype=np.uint8).reshape(view.shape)
            img = Image.fromarray(np.array(view, copy=True), "RGBA")
            del view

            pixels = np.array(img)
            self.assertEqual(pixels.shape, (4, 4, 4))

            buf.close()
            buf.unlink()
        finally:
            try:
                buf.close()
            except Exception:
                pass
            try:
                buf.unlink()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
