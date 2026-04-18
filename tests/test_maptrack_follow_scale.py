"""MapTrack follow_scale 投影一致性测试。"""

import os
import sys
import unittest

import numpy as np


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from services.frame_renderer import FrameRenderer
from services.tile_service import latlon_to_pixel, pixel_to_latlon


class TestMapTrackFollowScale(unittest.TestCase):
    def test_follow_scale_projection_matches_zoomed_tile_background(self):
        widget_width = 240
        widget_height = 180
        follow_scale = 2.0
        zoom = 15

        center_lat = 22.55318
        center_lon = 113.85980
        center_px_x, center_px_y = latlon_to_pixel(center_lat, center_lon, zoom)

        # 选取一个距离中心固定像素偏移的地理点。
        delta_px_x = 18.0
        delta_px_y = -12.0
        point_lat, point_lon = pixel_to_latlon(
            center_px_x + delta_px_x,
            center_px_y + delta_px_y,
            zoom,
        )
        point_px_x, point_px_y = latlon_to_pixel(point_lat, point_lon, zoom)

        render_w, render_h, scale_x, scale_y = FrameRenderer._tile_follow_render_metrics(
            widget_width, widget_height, follow_scale
        )
        out_x, out_y = FrameRenderer._project_tile_pixels_to_widget(
            np.array([point_px_x], dtype=np.float64),
            np.array([point_px_y], dtype=np.float64),
            center_px_x,
            center_px_y,
            render_w,
            render_h,
            scale_x,
            scale_y,
        )

        # 底图 follow_scale 的实际效果是：
        # 先在 render_w/render_h 的逻辑视口中定位，再放大回 widget 大小。
        expected_x = int(round((delta_px_x + render_w / 2.0) * (widget_width / render_w)))
        expected_y = int(round((delta_px_y + render_h / 2.0) * (widget_height / render_h)))

        self.assertEqual(int(out_x[0]), expected_x)
        self.assertEqual(int(out_y[0]), expected_y)

        # follow_scale=2 时，离中心 18 像素的点应该在屏幕上更远离中心，
        # 即表现为“放大”而不是“缩小/缩回中心”。
        center_screen_x = widget_width // 2
        self.assertGreater(int(out_x[0]) - center_screen_x, int(round(delta_px_x)))


if __name__ == "__main__":
    unittest.main()
