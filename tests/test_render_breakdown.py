"""渲染管线分解性能测试 — PyAV 解码 / 编码 + Widget 渲染耗时

测试 test_03 的三个关键环节独立耗时：
  1. PyAV 解码随机帧
  2. PyAV 编码随机帧（H.264 crf 23 medium）
  3. Widget 渲染单帧（与 e2e render 相同布局）

数据：Zepp20260404075746.fit + DJI_20260404105150_0004_D.MP4
布局：saved_states/339a40ce7a32.yaml
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

import av
from fractions import Fraction
from PIL import Image

from models.fit_data import FitData
from models.overlay_template import WidgetConfig
from models.video_config import TimeSyncConfig
from services.fit_parser import FitParserService
from services.frame_renderer import FrameRenderer
from services.render_pipeline import RenderPipeline

# ── 测试数据路径 ──────────────────────────────────────────
BASE_DIR = r"C:\Projects\20260406拼接骑行视频"
FIT_PATH = os.path.join(BASE_DIR, "Zepp20260404075746.fit")
VIDEO_PATH = os.path.join(BASE_DIR, "DJI_20260404105150_0004_D.MP4")

# ── YAML 中的布局配置（同 test_e2e_render.py） ─────────────
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
FPS = 29.97

# 测试采样
NUM_DECODE_SAMPLES = 10   # 解码采样帧数
NUM_ENCODE_SAMPLES = 10   # 编码采样帧数
NUM_WIDGET_SAMPLES = 10   # Widget 渲染采样帧数


def _skip_if_no_data(test_case):
    """如果测试文件不存在则跳过"""
    if not os.path.isfile(FIT_PATH):
        test_case.skipTest(f"FIT 文件不存在: {FIT_PATH}")
    if not os.path.isfile(VIDEO_PATH):
        test_case.skipTest(f"视频文件不存在: {VIDEO_PATH}")


class TestRenderBreakdown(unittest.TestCase):
    """渲染管线分解性能测试"""

    @classmethod
    def setUpClass(cls):
        """一次性加载 FIT 数据和 Widget 配置"""
        if not os.path.isfile(FIT_PATH):
            return
        if not os.path.isfile(VIDEO_PATH):
            return

        # 解析 FIT
        cls.fit_data = FitParserService.parse(FIT_PATH)
        cls.widgets = [WidgetConfig.from_dict(w) for w in WIDGETS_DATA]
        cls.time_sync = TimeSyncConfig.from_dict(TIME_SYNC_DATA)

        # 获取视频基本信息
        cls.video_info = None
        try:
            from services.video_analyzer import VideoAnalyzerService
            cls.video_info = VideoAnalyzerService.analyze(VIDEO_PATH)
        except Exception:
            pass

        # 随机采样种子
        random.seed(42)

    # ══════════════════════════════════════════════════════════
    #  Test 1: PyAV 解码随机帧耗时
    # ══════════════════════════════════════════════════════════

    def test_01_pyav_decode_random_frames(self):
        """PyAV 解码随机帧耗时"""
        _skip_if_no_data(self)

        # 获取视频时长
        container = av.open(VIDEO_PATH)
        video_stream = container.streams.video[0]
        video_stream.thread_type = "AUTO"
        duration_sec = float(container.duration) / 1_000_000 if container.duration else 600
        container.close()

        # 生成随机 seek 时间点（0 ~ min(duration, 60s)）
        max_seek = min(duration_sec, 60.0)
        seek_times = [random.uniform(0, max_seek) for _ in range(NUM_DECODE_SAMPLES)]

        timings_decode = []      # seek + decode 耗时
        timings_to_image = []    # to_image() 转换耗时

        for seek_t in seek_times:
            # ── 打开 & seek ──
            container = av.open(VIDEO_PATH)
            v_stream = container.streams.video[0]
            v_stream.thread_type = "AUTO"

            target_ts = int(seek_t / float(v_stream.time_base))
            container.seek(target_ts, stream=v_stream)

            # ── 解码一帧 ──
            t0 = time.perf_counter()
            v_frame = next(container.decode(v_stream))
            t_decode = time.perf_counter() - t0
            timings_decode.append(t_decode)

            # ── 转为 PIL Image ──
            t1 = time.perf_counter()
            img = v_frame.to_image()
            t_to_image = time.perf_counter() - t1
            timings_to_image.append(t_to_image)

            container.close()

            self.assertIsNotNone(img, f"seek {seek_t:.1f}s 解码帧为 None")
            # PyAV auto-rotate，尺寸可能和原始不同
            self.assertGreater(img.size[0], 0)
            self.assertGreater(img.size[1], 0)

        avg_decode_ms = sum(timings_decode) / len(timings_decode) * 1000
        max_decode_ms = max(timings_decode) * 1000
        avg_to_image_ms = sum(timings_to_image) / len(timings_to_image) * 1000

        print(f"\n{'='*60}")
        print(f"  PyAV 解码随机帧 ({NUM_DECODE_SAMPLES} 帧):")
        print(f"    平均 decode:    {avg_decode_ms:.1f} ms/帧")
        print(f"    最大 decode:    {max_decode_ms:.1f} ms/帧")
        print(f"    平均 to_image:  {avg_to_image_ms:.1f} ms/帧")
        for i, (seek_t, td, ti) in enumerate(zip(seek_times, timings_decode, timings_to_image)):
            print(f"    帧 {i}: seek={seek_t:.1f}s, decode={td*1000:.1f}ms, to_image={ti*1000:.1f}ms")
        print(f"{'='*60}")

        # 宽松上限：单帧 decode 2s
        self.assertLess(max_decode_ms, 2000, "单帧解码超过 2s")

    # ══════════════════════════════════════════════════════════
    #  Test 2: PyAV 编码随机帧耗时
    # ══════════════════════════════════════════════════════════

    def test_02_pyav_encode_random_frames(self):
        """PyAV 编码随机帧耗时（H.264 crf 23 medium）"""
        _skip_if_no_data(self)

        # 准备合成帧：先解码背景 + 渲染 overlay
        container = av.open(VIDEO_PATH)
        v_stream = container.streams.video[0]
        v_stream.thread_type = "AUTO"
        duration_sec = float(container.duration) / 1_000_000 if container.duration else 600

        # 生成随机 seek 时间点
        max_seek = min(duration_sec, 60.0)
        seek_times = [random.uniform(0, max_seek) for _ in range(NUM_ENCODE_SAMPLES)]

        # 预先解码 + 合成好待编码的帧
        composite_frames = []
        for seek_t in seek_times:
            target_ts = int(seek_t / float(v_stream.time_base))
            container.seek(target_ts, stream=v_stream)
            v_frame = next(container.decode(v_stream))
            bg_img = v_frame.to_image().convert("RGBA")
            if bg_img.size != (CANVAS_W, CANVAS_H):
                bg_img = bg_img.resize((CANVAS_W, CANVAS_H), Image.LANCZOS)

            # 渲染 overlay
            video_time_sec = seek_t
            fit_time = self.time_sync.fit_time_at_video_seconds(video_time_sec)
            record = FitParserService.get_record_at(self.fit_data, fit_time)

            overlay_img = FrameRenderer.render_frame(
                fit_data=self.fit_data,
                fit_time=fit_time,
                widgets=self.widgets,
                canvas_width=CANVAS_W,
                canvas_height=CANVAS_H,
            )
            bg_img.alpha_composite(overlay_img)
            composite = bg_img.convert("RGB")
            composite_frames.append(composite)

        container.close()

        # ── 编码测试 ──
        out_dir = tempfile.mkdtemp(prefix="cycling_encode_test_")
        output_path = os.path.join(out_dir, "encode_test.mp4")

        out_container = av.open(output_path, "w")
        v_out = out_container.add_stream(CODEC, Fraction(FPS).limit_denominator(100000))
        v_out.width = CANVAS_W
        v_out.height = CANVAS_H
        v_out.pix_fmt = "yuv420p"
        v_out.options = {"preset": PRESET, "crf": str(CRF)}

        timings_encode = []      # from_image + encode 耗时
        timings_mux = []         # mux 写入耗时

        for i, composite in enumerate(composite_frames):
            # from_image
            t0 = time.perf_counter()
            out_frame = av.VideoFrame.from_image(composite)

            # encode
            packets = list(v_out.encode(out_frame))
            t_encode = time.perf_counter() - t0
            timings_encode.append(t_encode)

            # mux
            t1 = time.perf_counter()
            for pkt in packets:
                out_container.mux(pkt)
            t_mux = time.perf_counter() - t1
            timings_mux.append(t_mux)

        # flush
        for pkt in v_out.encode():
            out_container.mux(pkt)
        out_container.close()

        file_size_mb = os.path.getsize(output_path) / (1024 * 1024)

        avg_encode_ms = sum(timings_encode) / len(timings_encode) * 1000
        max_encode_ms = max(timings_encode) * 1000
        avg_mux_ms = sum(timings_mux) / len(timings_mux) * 1000
        total_encode_ms = sum(timings_encode) * 1000
        total_mux_ms = sum(timings_mux) * 1000

        print(f"\n{'='*60}")
        print(f"  PyAV 编码随机帧 ({NUM_ENCODE_SAMPLES} 帧, {CODEC} {PRESET} crf={CRF}):")
        print(f"    平均 encode (from_image+encode): {avg_encode_ms:.1f} ms/帧")
        print(f"    最大 encode:  {max_encode_ms:.1f} ms/帧")
        print(f"    平均 mux:     {avg_mux_ms:.1f} ms/帧")
        print(f"    总 encode:    {total_encode_ms:.1f} ms")
        print(f"    总 mux:       {total_mux_ms:.1f} ms")
        print(f"    输出文件:     {file_size_mb:.2f} MB")
        for i, (te, tm) in enumerate(zip(timings_encode, timings_mux)):
            print(f"    帧 {i}: encode={te*1000:.1f}ms, mux={tm*1000:.2f}ms")
        print(f"{'='*60}")

        self.assertGreater(file_size_mb, 0.01, "编码输出文件过小")
        self.assertLess(max_encode_ms, 2000, "单帧编码超过 2s")

        # 清理
        try:
            os.remove(output_path)
            os.rmdir(out_dir)
        except OSError:
            pass

    # ══════════════════════════════════════════════════════════
    #  Test 3: Widget 渲染单帧耗时
    # ══════════════════════════════════════════════════════════

    def test_03_widget_render_single_frame(self):
        """Widget 渲染单帧耗时（与 e2e render 相同布局）"""
        _skip_if_no_data(self)

        session = self.fit_data.primary_session
        self.assertIsNotNone(session, "FIT 无主会话")

        duration = session.total_elapsed_time if session.total_elapsed_time else 300

        # 生成随机采样时间点（video time, 0~60s）
        max_video_t = min(duration / 30, 60.0)  # time_scale=30
        sample_video_times = [random.uniform(0, max_video_t) for _ in range(NUM_WIDGET_SAMPLES)]

        # ── 预热 ──
        fit_time_warmup = self.time_sync.fit_time_at_video_seconds(0)
        FrameRenderer.render_frame(
            fit_data=self.fit_data,
            fit_time=fit_time_warmup,
            widgets=self.widgets,
            canvas_width=CANVAS_W,
            canvas_height=CANVAS_H,
        )

        # ── 正式采样 ──
        timings_total = []
        timings_per_widget = {w.widget_type: [] for w in self.widgets}

        for vt in sample_video_times:
            fit_time = self.time_sync.fit_time_at_video_seconds(vt)
            record = FitParserService.get_record_at(self.fit_data, fit_time)

            # ── 总渲染时间 ──
            t0 = time.perf_counter()
            overlay_img = FrameRenderer.render_frame(
                fit_data=self.fit_data,
                fit_time=fit_time,
                widgets=self.widgets,
                canvas_width=CANVAS_W,
                canvas_height=CANVAS_H,
            )
            t_total = time.perf_counter() - t0
            timings_total.append(t_total)

            self.assertIsNotNone(overlay_img, f"video_t={vt:.1f}s 渲染返回 None")
            self.assertEqual(overlay_img.size, (CANVAS_W, CANVAS_H))

            # ── 逐 Widget 渲染时间 ──
            for widget in self.widgets:
                if not widget.visible:
                    continue
                canvas_single = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
                tw0 = time.perf_counter()
                FrameRenderer._render_widget(canvas_single, widget, record, self.fit_data, fit_time)
                tw1 = time.perf_counter()
                timings_per_widget[widget.widget_type].append(tw1 - tw0)

        avg_total_ms = sum(timings_total) / len(timings_total) * 1000
        max_total_ms = max(timings_total) * 1000
        min_total_ms = min(timings_total) * 1000

        print(f"\n{'='*60}")
        print(f"  Widget 渲染单帧耗时 ({NUM_WIDGET_SAMPLES} 帧):")
        print(f"    平均总耗时: {avg_total_ms:.1f} ms/帧")
        print(f"    最小总耗时: {min_total_ms:.1f} ms/帧")
        print(f"    最大总耗时: {max_total_ms:.1f} ms/帧")
        print(f"    理论最大 FPS: {1000/avg_total_ms:.1f}" if avg_total_ms > 0 else "")
        print()
        print(f"  逐 Widget 平均耗时:")
        for wtype, times_list in timings_per_widget.items():
            if times_list:
                avg_w = sum(times_list) / len(times_list) * 1000
                max_w = max(times_list) * 1000
                pct = avg_w / avg_total_ms * 100 if avg_total_ms > 0 else 0
                print(f"    {wtype:20s}: {avg_w:6.1f} ms  (max {max_w:.1f}ms, {pct:.0f}%)")
        print()
        print(f"  逐帧详情:")
        for i, (vt, t_total) in enumerate(zip(sample_video_times, timings_total)):
            detail_parts = []
            for wtype, times_list in timings_per_widget.items():
                if i < len(times_list):
                    detail_parts.append(f"{wtype}={times_list[i]*1000:.1f}ms")
            print(f"    帧 {i}: video_t={vt:.1f}s, total={t_total*1000:.1f}ms  [{', '.join(detail_parts)}]")
        print(f"{'='*60}")

        # 宽松上限：单帧 2s
        self.assertLess(max_total_ms, 2000, "Widget 单帧渲染超过 2s")


    # ══════════════════════════════════════════════════════════
    #  Test 4: PyAV hwaccel vs 软解码对比
    # ══════════════════════════════════════════════════════════

    def test_04_pyav_hwaccel_decode_comparison(self):
        """PyAV 硬件加速解码 vs 软解码对比"""
        _skip_if_no_data(self)

        from av.codec.hwaccel import HWAccel, hwdevices_available

        # 获取可用硬件设备
        available_devices = hwdevices_available()
        if not available_devices:
            self.skipTest("无可用硬件加速设备")

        # 选择测试的 hwaccel 设备（d3d11va > dxva2 > cuda）
        test_devices = []
        for preferred in ['d3d11va', 'dxva2', 'cuda']:
            if preferred in available_devices:
                test_devices.append(preferred)

        if not test_devices:
            self.skipTest(f"无可用的 d3d11va/dxva2/cuda 设备，仅有: {available_devices}")

        # 固定 seek 时间点（确保可比性）
        seek_times = [1.0, 5.0, 10.0, 30.0, 60.0]
        NUM = len(seek_times)

        # ── 软件解码基准 ──
        sw_decode_times = []
        sw_to_image_times = []

        for seek_t in seek_times:
            container = av.open(VIDEO_PATH)
            v_stream = container.streams.video[0]
            v_stream.thread_type = "AUTO"

            target_ts = int(seek_t / float(v_stream.time_base))
            container.seek(target_ts, stream=v_stream)

            t0 = time.perf_counter()
            v_frame = next(container.decode(v_stream))
            t_decode = time.perf_counter() - t0

            t1 = time.perf_counter()
            img = v_frame.to_image()
            t_to_image = time.perf_counter() - t1

            sw_decode_times.append(t_decode)
            sw_to_image_times.append(t_to_image)
            container.close()

            self.assertIsNotNone(img)

        # ── 各 hwaccel 设备测试 ──
        hw_results = {}  # device_name -> (decode_times, to_image_times)

        for device_name in test_devices:
            hw = HWAccel(device_name, allow_software_fallback=True)
            decode_times = []
            to_image_times = []
            is_hwaccel = False

            for seek_t in seek_times:
                try:
                    container = av.open(VIDEO_PATH, hwaccel=hw)
                    v_stream = container.streams.video[0]
                    v_stream.thread_type = "AUTO"
                    is_hwaccel = v_stream.codec_context.is_hwaccel

                    target_ts = int(seek_t / float(v_stream.time_base))
                    container.seek(target_ts, stream=v_stream)

                    t0 = time.perf_counter()
                    v_frame = next(container.decode(v_stream))
                    t_decode = time.perf_counter() - t0

                    t1 = time.perf_counter()
                    img = v_frame.to_image()
                    t_to_image = time.perf_counter() - t1

                    decode_times.append(t_decode)
                    to_image_times.append(t_to_image)
                    container.close()
                except Exception as e:
                    decode_times.append(None)
                    to_image_times.append(None)
                    try:
                        container.close()
                    except:
                        pass

            hw_results[device_name] = (decode_times, to_image_times, is_hwaccel)

        # ── 输出对比结果 ──
        avg_sw_decode = sum(sw_decode_times) / len(sw_decode_times) * 1000
        avg_sw_img = sum(sw_to_image_times) / len(sw_to_image_times) * 1000

        print(f"\n{'='*60}")
        print(f"  PyAV 解码对比: 软解码 vs 硬件加速 ({NUM} 帧)")
        print(f"{'='*60}")

        print(f"\n  软件解码 (CPU):")
        print(f"    平均 decode:    {avg_sw_decode:.1f} ms/帧")
        print(f"    平均 to_image:  {avg_sw_img:.1f} ms/帧")
        for i, (seek_t, td, ti) in enumerate(zip(seek_times, sw_decode_times, sw_to_image_times)):
            print(f"    seek={seek_t:5.1f}s: decode={td*1000:7.1f}ms, to_image={ti*1000:6.1f}ms")

        for device_name, (dec_times, img_times, is_hw) in hw_results.items():
            valid_dec = [t for t in dec_times if t is not None]
            valid_img = [t for t in img_times if t is not None]

            if not valid_dec:
                print(f"\n  {device_name} 硬件解码: 全部失败")
                continue

            avg_hw_decode = sum(valid_dec) / len(valid_dec) * 1000
            avg_hw_img = sum(valid_img) / len(valid_img) * 1000
            speedup = avg_sw_decode / avg_hw_decode if avg_hw_decode > 0 else float('inf')

            print(f"\n  {device_name} 硬件解码 (is_hwaccel={is_hw}):")
            print(f"    平均 decode:    {avg_hw_decode:.1f} ms/帧  (加速 {speedup:.1f}x)")
            print(f"    平均 to_image:  {avg_hw_img:.1f} ms/帧")
            for i, (seek_t, td, ti) in enumerate(zip(seek_times, dec_times, img_times)):
                if td is not None:
                    print(f"    seek={seek_t:5.1f}s: decode={td*1000:7.1f}ms, to_image={ti*1000:6.1f}ms")
                else:
                    print(f"    seek={seek_t:5.1f}s: FAILED")

        # ── 汇总对比表 ──
        print(f"\n  {'='*50}")
        print(f"  {'设备':<12} {'avg decode':>12} {'avg to_image':>14} {'加速比':>8}")
        print(f"  {'-'*12} {'-'*12} {'-'*14} {'-'*8}")
        print(f"  {'CPU (sw)':<12} {avg_sw_decode:>10.1f}ms {avg_sw_img:>12.1f}ms {'1.0x':>8}")

        for device_name, (dec_times, img_times, is_hw) in hw_results.items():
            valid_dec = [t for t in dec_times if t is not None]
            valid_img = [t for t in img_times if t is not None]
            if valid_dec:
                avg_d = sum(valid_dec) / len(valid_dec) * 1000
                avg_i = sum(valid_img) / len(valid_img) * 1000
                sp = avg_sw_decode / avg_d if avg_d > 0 else 0
                print(f"  {device_name:<12} {avg_d:>10.1f}ms {avg_i:>12.1f}ms {sp:>7.1f}x")

        print(f"  {'='*50}")
        print(f"{'='*60}")

        # 至少一个 hwaccel 方式能正常解码
        any_valid = any(
            any(t is not None for t in hw_results[d][0])
            for d in hw_results
        )
        self.assertTrue(any_valid, "所有硬件加速解码方式均失败")

    # ══════════════════════════════════════════════════════════
    #  Test 5: PyAV hwaccel 顺序解码吞吐对比（无 seek）
    # ══════════════════════════════════════════════════════════

    def test_05_pyav_hwaccel_sequential_decode(self):
        """PyAV 硬件加速顺序解码吞吐对比（无 seek，连续读取）"""
        _skip_if_no_data(self)

        from av.codec.hwaccel import HWAccel, hwdevices_available

        available_devices = hwdevices_available()
        if not available_devices:
            self.skipTest("无可用硬件加速设备")

        test_devices = [d for d in ['d3d11va', 'dxva2', 'cuda'] if d in available_devices]
        if not test_devices:
            self.skipTest(f"无可用的 d3d11va/dxva2/cuda 设备")

        NUM_FRAMES = 100  # 连续解码帧数

        # ── 软件解码基准 ──
        container = av.open(VIDEO_PATH)
        v_stream = container.streams.video[0]
        v_stream.thread_type = "AUTO"

        sw_frames = 0
        t0 = time.perf_counter()
        for v_frame in container.decode(v_stream):
            img = v_frame.to_image()
            sw_frames += 1
            if sw_frames >= NUM_FRAMES:
                break
        t_sw_total = time.perf_counter() - t0
        container.close()

        sw_fps = sw_frames / t_sw_total if t_sw_total > 0 else 0
        sw_avg_ms = t_sw_total / sw_frames * 1000 if sw_frames > 0 else 0

        print(f"\n{'='*60}")
        print(f"  PyAV 顺序解码吞吐对比 ({NUM_FRAMES} 帧, 无 seek)")
        print(f"{'='*60}")
        print(f"\n  CPU (sw): {sw_frames}帧 / {t_sw_total:.3f}s = {sw_fps:.1f} fps ({sw_avg_ms:.1f} ms/帧)")

        # ── 各 hwaccel 设备 ──
        for device_name in test_devices:
            hw = HWAccel(device_name, allow_software_fallback=True)
            container = av.open(VIDEO_PATH, hwaccel=hw)
            v_stream = container.streams.video[0]
            v_stream.thread_type = "AUTO"
            is_hw = v_stream.codec_context.is_hwaccel

            hw_frames = 0
            try:
                t0 = time.perf_counter()
                for v_frame in container.decode(v_stream):
                    img = v_frame.to_image()
                    hw_frames += 1
                    if hw_frames >= NUM_FRAMES:
                        break
                t_hw_total = time.perf_counter() - t0
            except Exception as e:
                t_hw_total = 0
                hw_frames = 0
                print(f"\n  {device_name} (is_hwaccel={is_hw}): 解码失败: {e}")
                container.close()
                continue
            container.close()

            hw_fps = hw_frames / t_hw_total if t_hw_total > 0 else 0
            hw_avg_ms = t_hw_total / hw_frames * 1000 if hw_frames > 0 else 0
            speedup = sw_fps / hw_fps if hw_fps > 0 else 0

            print(f"\n  {device_name} (is_hwaccel={is_hw}): {hw_frames}帧 / {t_hw_total:.3f}s = {hw_fps:.1f} fps ({hw_avg_ms:.1f} ms/帧)  加速 {speedup:.1f}x")

        # ── 汇总表 ──
        print(f"\n  {'='*50}")
        print(f"  {'设备':<12} {'帧数':>6} {'总耗时':>10} {'FPS':>8} {'ms/帧':>8} {'加速比':>8}")
        print(f"  {'-'*12} {'-'*6} {'-'*10} {'-'*8} {'-'*8} {'-'*8}")
        print(f"  {'CPU (sw)':<12} {sw_frames:>6} {t_sw_total:>9.3f}s {sw_fps:>7.1f} {sw_avg_ms:>7.1f} {'1.0x':>8}")

        for device_name in test_devices:
            hw = HWAccel(device_name, allow_software_fallback=True)
            container = av.open(VIDEO_PATH, hwaccel=hw)
            v_stream = container.streams.video[0]
            v_stream.thread_type = "AUTO"
            is_hw = v_stream.codec_context.is_hwaccel

            hw_frames = 0
            try:
                t0 = time.perf_counter()
                for v_frame in container.decode(v_stream):
                    img = v_frame.to_image()
                    hw_frames += 1
                    if hw_frames >= NUM_FRAMES:
                        break
                t_hw_total = time.perf_counter() - t0
            except:
                container.close()
                continue
            container.close()

            hw_fps = hw_frames / t_hw_total if t_hw_total > 0 else 0
            hw_avg_ms = t_hw_total / hw_frames * 1000 if hw_frames > 0 else 0
            sp = sw_fps / hw_fps if hw_fps > 0 else 0
            print(f"  {device_name:<12} {hw_frames:>6} {t_hw_total:>9.3f}s {hw_fps:>7.1f} {hw_avg_ms:>7.1f} {sp:>7.1f}x")

        print(f"  {'='*50}")
        print(f"{'='*60}")

        self.assertGreater(sw_frames, 0, "软件解码未产出任何帧")


if __name__ == "__main__":
    unittest.main(verbosity=2)
