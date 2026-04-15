"""端到端渲染测试 — Zepp20260404075746.fit + DJI_20260404105150_0004_D.MP4

测试环节：
  1. FIT 解析耗时
  2. 渲染预览随机帧耗时
  3. 渲染前 10s 切片耗时（h264, crf 23, medium 预设）

布局来自 saved_states/339a40ce7a32.yaml
"""

import os
import sys
import time
import random
import tempfile
import unittest

# 确保项目根目录在 sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from models.fit_data import FitData
from models.overlay_template import WidgetConfig
from models.video_config import TimeSyncConfig
from services.fit_parser import FitParserService
from services.frame_renderer import FrameRenderer
from services.render_pipeline import RenderPipeline
from services.video_analyzer import VideoAnalyzerService
import argparse
import cProfile
import pstats
import io

# ── 测试数据路径 ──────────────────────────────────────────
BASE_DIR = r"C:\Projects\20260406拼接骑行视频"
FIT_PATH = os.path.join(BASE_DIR, "Zepp20260404075746.fit")
VIDEO_PATH = os.path.join(BASE_DIR, "DJI_20260404105150_0004_D.MP4")

# ── YAML 中的布局配置 ─────────────────────────────────────
WIDGETS_DATA = [
    {
        "widget_type": "MapTrack",
        "x": 1552, "y": 77, "width": 350, "height": 350,
        "opacity": 1, "data_field": "track", "visible": True,
        "style": {
            "auto_aspect": False, "auto_center": True,
            "map_style": "dark", "marker_color": "#ff4444",
            "marker_size": 8, "track_color": "#00d4aa",
            "zoom": 13, "color": "#00d4aa", "font_size": 28,
            "unit": "", "tile_source": "carto_dark",
            "map_mode": "follow", "follow_zoom": 15,
        },
    },
    {
        "widget_type": "SpeedGauge",
        "x": 1755, "y": 780, "width": 150, "height": 80,
        "opacity": 1, "data_field": "speed", "visible": True,
        "style": {
            "color": "#00d4aa", "font_size": 32,
            "format": "arc", "max_val": 80, "min_val": 0, "unit": "km/h",
        },
    },
    {
        "widget_type": "HeartRateGauge",
        "x": 1755, "y": 890, "width": 150, "height": 80,
        "opacity": 1, "data_field": "heart_rate", "visible": True,
        "style": {
            "color": "#ff4444", "font_size": 32,
            "format": "arc", "max_val": 200, "min_val": 40, "unit": "bpm",
        },
    },
    {
        "widget_type": "CadenceGauge",
        "x": 1755, "y": 985, "width": 150, "height": 80,
        "opacity": 1, "data_field": "cadence", "visible": True,
        "style": {
            "color": "#4488ff", "font_size": 32,
            "format": "arc", "max_val": 150, "min_val": 0, "unit": "rpm",
        },
    },
    {
        "widget_type": "AltitudeChart",
        "x": 15, "y": 15, "width": 1890, "height": 50,
        "opacity": 1, "data_field": "altitude", "visible": True,
        "style": {
            "fill_color": "#aa88ff30", "line_color": "#aa88ff",
            "show_grid": False,
        },
    },
]

# 时间同步配置（来自 YAML：time_scale=30，延时摄影）
TIME_SYNC_DATA = {
    "video_start_time": "2026-04-03T23:38:19+00:00",
    "fit_start_time": "2026-04-03T23:57:46+00:00",
    "offset_seconds": 0,
    "time_scale": 30,
}

# 渲染参数
CODEC = "libx264"
PRESET = "medium"
CRF = 23
CANVAS_W, CANVAS_H = 1920, 1080


def _skip_if_no_data(test_case):
    """如果测试文件不存在则跳过"""
    if not os.path.isfile(FIT_PATH):
        test_case.skipTest(f"FIT 文件不存在: {FIT_PATH}")
    if not os.path.isfile(VIDEO_PATH):
        test_case.skipTest(f"视频文件不存在: {VIDEO_PATH}")


class TestEndToEndRender(unittest.TestCase):
    """端到端渲染测试"""

    @classmethod
    def setUpClass(cls):
        """一次性加载 FIT 数据（所有测试共用）"""
        if not os.path.isfile(FIT_PATH):
            return
        t0 = time.perf_counter()
        cls.fit_data = FitParserService.parse(FIT_PATH)
        cls.fit_parse_time = time.perf_counter() - t0

        cls.widgets = [WidgetConfig.from_dict(w) for w in WIDGETS_DATA]
        cls.time_sync = TimeSyncConfig.from_dict(TIME_SYNC_DATA)

    def test_01_fit_parse_performance(self):
        """FIT 解析耗时"""
        _skip_if_no_data(self)
        self.assertIsNotNone(self.fit_data, "FIT 解析失败")
        self.assertGreater(len(self.fit_data.sessions), 0, "FIT 无会话")

        session = self.fit_data.primary_session
        record_count = len(session.records) if session else 0
        self.assertGreater(record_count, 0, "FIT 无记录")

        print(f"\n{'='*60}")
        print(f"  FIT 解析耗时: {self.fit_parse_time:.3f}s")
        print(f"  会话数: {len(self.fit_data.sessions)}")
        print(f"  记录数: {record_count}")
        if session:
            print(f"  会话起始: {session.start_time}")
            print(f"  会话时长: {session.total_elapsed_time}s")
        print(f"{'='*60}")

        # 宽松上限：60s（含 sanitize + smooth）
        self.assertLess(self.fit_parse_time, 60, "FIT 解析耗时超过 60s")

    def test_02_preview_random_frames(self):
        """渲染预览随机帧耗时（5 帧，不含背景视频提取）"""
        _skip_if_no_data(self)

        duration = self.fit_data.primary_session.total_elapsed_time if self.fit_data.primary_session else 300
        random.seed(42)
        sample_times = [random.uniform(0, min(duration / 30, 60)) for _ in range(5)]

        timings = []
        for vt in sample_times:
            fit_time = self.time_sync.fit_time_at_video_seconds(vt)
            t0 = time.perf_counter()
            overlay_img = FrameRenderer.render_frame(
                fit_data=self.fit_data,
                fit_time=fit_time,
                widgets=self.widgets,
                canvas_width=CANVAS_W,
                canvas_height=CANVAS_H,
            )
            elapsed = time.perf_counter() - t0
            timings.append(elapsed)

            self.assertIsNotNone(overlay_img, f"帧 {vt:.1f}s 渲染返回 None")
            self.assertEqual(overlay_img.size, (CANVAS_W, CANVAS_H),
                             f"帧 {vt:.1f}s 尺寸不正确: {overlay_img.size}")

        avg_ms = sum(timings) / len(timings) * 1000
        max_ms = max(timings) * 1000

        print(f"\n{'='*60}")
        print(f"  预览帧渲染（{len(sample_times)} 帧）:")
        for i, (vt, ms) in enumerate(zip(sample_times, [t * 1000 for t in timings])):
            print(f"    帧 {i}: video_t={vt:.1f}s → {ms:.1f}ms")
        print(f"  平均: {avg_ms:.1f}ms / 帧")
        print(f"  最大: {max_ms:.1f}ms")
        print(f"{'='*60}")

        # 宽松上限：单帧 2s
        self.assertLess(max_ms, 2000, "单帧渲染超过 2s")

    def test_03_render_10s_slice(self):
        """渲染前 10s 切片（h264, crf 23, medium 预设）"""
        _skip_if_no_data(self)

        # 输出到临时文件
        out_dir = tempfile.mkdtemp(prefix="cycling_render_test_")
        output_path = os.path.join(out_dir, "test_10s_slice.mp4")

        pipeline = RenderPipeline()

        t0 = time.perf_counter()
        result = pipeline.render_video(
            video_path=VIDEO_PATH,
            fit_data=self.fit_data,
            widgets=self.widgets,
            time_sync=self.time_sync,
            output_path=output_path,
            canvas_width=CANVAS_W,
            canvas_height=CANVAS_H,
            start_sec=0,
            end_sec=10,
            codec=CODEC,
            preset=PRESET,
            crf=CRF,
            overlay_only=False,
            num_workers=4,
            batch_size=8,
        )
        elapsed = time.perf_counter() - t0

        # 打印渲染日志（最后 20 条）
        logs, _ = pipeline.get_logs(0)
        print(f"\n{'='*60}")
        print(f"  渲染日志（最后 20 条）:")
        for entry in logs[-20:]:
            print(f"    [{entry['level']:7s}] {entry['msg']}")
        print(f"{'='*60}")

        self.assertEqual(result["status"], "completed",
                         f"渲染未完成: {result.get('error', '')}")

        # 验证输出文件
        self.assertTrue(os.path.isfile(output_path),
                        f"输出文件不存在: {output_path}")

        file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
        stats = result["stats"]
        fps = stats["frames_rendered"] / elapsed if elapsed > 0 else 0

        print(f"\n{'='*60}")
        print(f"  渲染 10s 切片结果:")
        print(f"    状态: {result['status']}")
        print(f"    总耗时: {elapsed:.1f}s")
        print(f"    渲染帧数: {stats['frames_rendered']}")
        print(f"    编码帧数: {stats['frames_encoded']}")
        print(f"    渲染速度: {fps:.1f} fps")
        print(f"    输出文件: {output_path}")
        print(f"    文件大小: {file_size_mb:.2f} MB")
        print(f"    编码: {CODEC} {PRESET} crf={CRF}")
        print(f"{'='*60}")

        # 宽松上限：10s 视频在 5 分钟内完成
        self.assertLess(elapsed, 300, "10s 切片渲染超过 5 分钟")

        # 文件大小合理范围
        self.assertGreater(file_size_mb, 0.1, "输出文件过小，可能编码失败")

        # 清理
        try:
            os.remove(output_path)
            os.rmdir(out_dir)
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser(description="End-to-end render test with optional profiling")
    parser.add_argument("--profile", action="store_true", help="Enable cProfile profiling")
    args = parser.parse_args()

    if args.profile:
        profiler = cProfile.Profile()
        profiler.enable()

    # Run the tests
    suite = unittest.TestLoader().loadTestsFromName("test_e2e_render.TestEndToEndRender.test_03_render_10s_slice")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    if args.profile:
        profiler.disable()
        s = io.StringIO()
        ps = pstats.Stats(profiler, stream=s).sort_stats(pstats.SortKey.CUMULATIVE)
        ps.print_stats(50)  # Print top 50 functions by cumulative time
        print("\n" + "=" * 60)
        print("Profiling Results (Top 50 by cumulative time):")
        print("=" * 60)
        print(s.getvalue())

    # Exit with proper code
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
