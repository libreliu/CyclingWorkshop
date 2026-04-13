"""渲染管线服务（PyAV 编解码 + Pillow overlay + 实时日志）

架构：
  - PyAV 读取背景视频（自动旋转）+ PyAV 编码输出（H.264 / qtrle / VP9）
  - Pillow 渲染 overlay 帧（多线程并行）
  - 完全在 Python 进程内完成，无需外部 ffmpeg 命令
  - 实时进度推送：帧数 / FPS / ETA

模式：
  - 合成模式（overlay_only=False）：overlay + 背景视频合成，输出 MP4
  - 仅 Overlay（overlay_only=True）：只输出 overlay 层（保留 alpha），
    输出 MOV（qtrle 无损）或 WebM（VP9），用户可自行在 NLE 中编辑

优势（相比 subprocess ffmpeg 方案）：
  - 旋转处理由 PyAV/libav 自动完成，方向可靠
  - 无需构建复杂的 ffmpeg 命令行和 filter_complex
  - 进度统计精确（Python 侧直接计数），无需解析 stderr
  - 音频直接转码复制，无需 ffprobe 预检测

注意事项：
  - PyAV add_stream(template=stream) 在某些版本不支持，
    音频转码使用 add_stream(codec_name, rate) + layout
  - PyAV 不支持音频流 copy（不重新编码），
    音频统一走 re-encode（AAC 256kbps），质量损失极小
"""
import io
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from models.fit_data import FitData, FitRecord, FitSession
from models.overlay_template import WidgetConfig
from models.video_config import TimeSyncConfig

from PIL import Image

import av  # PyAV
import numpy as np
from fractions import Fraction


class RenderPipeline:
    """渲染管线（PyAV 编解码 + Pillow overlay + 实时日志）"""

    def __init__(self):
        self._cancelled = False
        self.stats = {
            "frames_rendered": 0,
            "frames_encoded": 0,
            "total_frames": 0,
            "overlay_fps": 0.0,
            "encode_fps": 0.0,
            "elapsed_sec": 0.0,
            "eta_sec": 0.0,
            "progress_pct": 0.0,
            "phase": "idle",
        }
        self.logs = []
        self._log_lock = threading.Lock()

    def cancel(self):
        """取消渲染"""
        self._cancelled = True

    def add_log(self, msg: str, level: str = "info"):
        """添加一条日志（线程安全）"""
        ts = time.strftime("%H:%M:%S")
        entry = {"time": ts, "level": level, "msg": msg}
        with self._log_lock:
            self.logs.append(entry)
            if len(self.logs) > 500:
                self.logs = self.logs[-500:]

    def get_logs(self, since_index: int = 0):
        """获取日志（从指定索引开始）"""
        with self._log_lock:
            return list(self.logs[since_index:]), len(self.logs)

    def render_video(
        self,
        video_path: str,
        fit_data: FitData,
        widgets: list,
        time_sync: TimeSyncConfig,
        output_path: str,
        canvas_width: int = 1920,
        canvas_height: int = 1080,
        fps: Optional[float] = None,
        start_sec: float = 0,
        end_sec: Optional[float] = None,
        codec: str = "libx264",
        preset: str = "fast",
        crf: int = 23,
        audio_mode: str = "copy",
        overlay_only: bool = False,
        num_workers: int = 4,
        batch_size: int = 8,
        progress_callback=None,
    ) -> dict:
        self._cancelled = False
        self.logs = []
        start_time = time.time()

        try:
            # ── 0. 获取视频信息 & 预计算 ──────────────────────
            from services.fit_parser import FitParserService
            from services.video_analyzer import VideoAnalyzerService

            video_info = VideoAnalyzerService.analyze(video_path)
            if not video_info:
                return {"status": "error", "error": f"视频分析失败: {video_path}", "stats": self.stats}

            if fps is None or fps <= 0:
                fps = video_info.fps or 29.97

            if end_sec is None or end_sec <= 0:
                end_sec = video_info.duration

            total_duration = end_sec - start_sec
            total_frames = int(total_duration * fps)

            if total_frames <= 0:
                return {"status": "error", "error": f"渲染范围无效: {start_sec}s ~ {end_sec}s", "stats": self.stats}

            self.stats["total_frames"] = total_frames
            self.stats["phase"] = "rendering"

            self.add_log(f"🎬 渲染开始: {total_frames} 帧, {fps:.2f} fps, {total_duration:.1f}s")
            if overlay_only:
                self.add_log(f"   模式: 仅 Overlay（保留 Alpha 透明通道）")
            else:
                self.add_log(f"   模式: Overlay + 背景视频合成")
            self.add_log(f"   输入: {video_path}")
            self.add_log(f"   输出: {output_path}")
            self.add_log(f"   编码: {codec} {preset} crf={crf}")
            self.add_log(f"   画布: {canvas_width}×{canvas_height}")
            self.add_log(f"   引擎: PyAV {av.__version__}")

            # 预计算每帧的 FitRecord
            record_lookup = [None] * total_frames
            fit_time_lookup = [None] * total_frames
            for i in range(total_frames):
                video_elapsed = i / fps + start_sec
                fit_time = time_sync.fit_time_at_video_seconds(video_elapsed)
                fit_time_lookup[i] = fit_time
                if fit_time is not None:
                    record_lookup[i] = FitParserService.get_record_at(fit_data, fit_time)

            self.add_log(f"✅ 预计算完成: {total_frames} 帧的 FitRecord 查找表")

            # ── 1. 分模式渲染 ────────────────────────────────
            if overlay_only:
                actual_output = self._render_overlay_only(
                    fit_data, fit_time_lookup, record_lookup, widgets,
                    canvas_width, canvas_height, fps, total_frames,
                    output_path, codec, crf, num_workers, batch_size,
                    start_time, progress_callback,
                )
            else:
                actual_output = self._render_composite(
                    video_path, fit_data, fit_time_lookup, record_lookup, widgets,
                    canvas_width, canvas_height, fps, total_frames,
                    start_sec, end_sec, output_path, codec, preset, crf,
                    audio_mode, num_workers, batch_size,
                    start_time, progress_callback, video_info,
                )

            if self._cancelled:
                self.stats["phase"] = "cancelled"
                self.add_log("⚠️ 渲染已取消")
                return {"status": "cancelled", "error": None, "stats": self.stats}

            # 完成
            self.stats["phase"] = "done"
            self.stats["progress_pct"] = 100.0
            total_time = time.time() - start_time
            self.stats["elapsed_sec"] = round(total_time, 1)
            self.stats["frames_rendered"] = total_frames
            self.stats["frames_encoded"] = total_frames
            self.add_log(f"✅ 渲染完成: {total_frames} 帧, {total_time:.1f}s, {total_frames/total_time:.1f} fps")

            if progress_callback:
                progress_callback(dict(self.stats))

            return {
                "status": "completed",
                "error": None,
                "stats": self.stats,
                "output_path": actual_output,
            }

        except Exception as e:
            self.stats["phase"] = "error"
            self.add_log(f"❌ 渲染异常: {e}")
            import traceback
            self.add_log(traceback.format_exc(), "error")
            return {"status": "error", "error": str(e), "stats": self.stats}

    # ══════════════════════════════════════════════════════════
    #  合成模式：overlay + 背景视频 → MP4
    # ══════════════════════════════════════════════════════════

    def _render_composite(
        self, video_path, fit_data, fit_time_lookup, record_lookup, widgets,
        canvas_width, canvas_height, fps, total_frames,
        start_sec, end_sec, output_path, codec, preset, crf,
        audio_mode, num_workers, batch_size,
        start_time, progress_callback, video_info,
    ):
        """合成模式：读取背景视频 → 逐帧合成 overlay → 编码输出 MP4"""

        # 打开输入视频
        self.add_log(f"📖 打开背景视频: {video_path}")
        inp = av.open(video_path)
        in_video = inp.streams.video[0]
        in_video.thread_type = "AUTO"

        # seek 到起始位置
        if start_sec > 0:
            target_ts = int(start_sec / float(in_video.time_base))
            inp.seek(target_ts, stream=in_video)
            self.add_log(f"   seek 到 {start_sec:.1f}s")

        # 检测音频流
        in_audio = inp.streams.audio[0] if inp.streams.audio else None
        has_audio = in_audio is not None and audio_mode == "copy"
        self.add_log(f"   音频流: {'有 (AAC re-encode)' if has_audio else '无'}")

        # 创建输出
        out = av.open(output_path, "w")

        # 视频编码流
        v_out = out.add_stream(codec, Fraction(fps).limit_denominator(100000))
        v_out.width = canvas_width
        v_out.height = canvas_height
        v_out.pix_fmt = "yuv420p"
        v_out.options = {"preset": preset, "crf": str(crf)}

        # 音频编码流（PyAV 不支持 copy，统一 AAC re-encode）
        a_out = None
        if has_audio:
            a_out = out.add_stream(codec_name="aac", rate=in_audio.sample_rate)
            a_out.layout = "stereo"
            self.add_log(f"   音频: AAC {in_audio.sample_rate}Hz stereo (re-encode)")

        self.add_log(f"🔧 输出编码: {codec} {preset} crf={crf}, {canvas_width}×{canvas_height} @ {fps:.2f}fps")

        # ── 逐帧处理 ──────────────────────────────────────
        encoded_count = 0
        overlay_start = time.time()
        video_fps = float(in_video.average_rate) if in_video.average_rate else fps

        # 使用线程池并行渲染 overlay
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            # 预取一批 overlay 帧
            pending_overlays = {}  # frame_idx -> overlay Image
            frame_idx = 0
            video_frame_iter = inp.decode(in_video)

            for v_frame in video_frame_iter:
                if self._cancelled:
                    break
                if frame_idx >= total_frames:
                    break

                # ── 渲染 overlay ──
                overlay_img = self._render_overlay_frame(
                    fit_data, fit_time_lookup[frame_idx], record_lookup[frame_idx],
                    widgets, canvas_width, canvas_height,
                )

                # ── 合成背景 + overlay ──
                # PyAV 自动旋转了背景帧
                bg_img = v_frame.to_image()
                if bg_img.size != (canvas_width, canvas_height):
                    bg_img = bg_img.resize((canvas_width, canvas_height), Image.LANCZOS)

                bg_rgba = bg_img.convert("RGBA")
                bg_rgba.alpha_composite(overlay_img)
                composite = bg_rgba.convert("RGB")

                # ── 编码视频帧 ──
                out_frame = av.VideoFrame.from_image(composite)
                for packet in v_out.encode(out_frame):
                    out.mux(packet)

                encoded_count += 1

                # ── 更新进度 ──
                if encoded_count % 10 == 0 or encoded_count == total_frames:
                    elapsed = time.time() - start_time
                    current_fps = encoded_count / elapsed if elapsed > 0 else 0
                    self.stats["frames_rendered"] = encoded_count
                    self.stats["frames_encoded"] = encoded_count
                    self.stats["overlay_fps"] = round(current_fps, 1)
                    self.stats["encode_fps"] = round(current_fps, 1)
                    self.stats["elapsed_sec"] = round(elapsed, 1)
                    if current_fps > 0:
                        remaining = (total_frames - encoded_count) / current_fps
                        self.stats["eta_sec"] = round(remaining, 0)
                    self.stats["progress_pct"] = round(
                        min(encoded_count, total_frames) / total_frames * 100, 1
                    )

                    self.add_log(
                        f"📊 进度: {encoded_count}/{total_frames} 帧, "
                        f"{current_fps:.1f} fps, ETA {self.stats['eta_sec']:.0f}s",
                        "progress"
                    )

                    if progress_callback and encoded_count % 20 == 0:
                        progress_callback(dict(self.stats))

                frame_idx += 1

        # flush 视频编码器
        for packet in v_out.encode():
            out.mux(packet)
        self.add_log(f"🎬 视频编码器 flush 完成")

        # ── 音频转码 ──
        if has_audio and a_out and not self._cancelled:
            self.add_log("🔊 开始音频转码...")
            # 需要重新 seek 到起始位置读音频
            # 关闭当前输入，重新打开读音频
            inp.close()
            inp2 = av.open(video_path)
            in_audio2 = inp2.streams.audio[0] if inp2.streams.audio else None

            if in_audio2:
                # seek 音频到对应位置
                if start_sec > 0:
                    try:
                        audio_ts = int(start_sec / float(in_audio2.time_base))
                        inp2.seek(audio_ts, stream=in_audio2)
                    except Exception as e:
                        self.add_log(f"   音频 seek 失败（将从开头编码）: {e}", "warning")

                a_count = 0
                for a_frame in inp2.decode(in_audio2):
                    if self._cancelled:
                        break
                    a_frame.pts = None  # 让编码器自动分配 pts
                    for packet in a_out.encode(a_frame):
                        out.mux(packet)
                    a_count += 1

                # flush 音频编码器
                for packet in a_out.encode():
                    out.mux(packet)

                self.add_log(f"✅ 音频转码完成: {a_count} 帧")

            inp2.close()

        out.close()
        if not has_audio or a_out is None:
            inp.close()

        self.add_log(f"💾 输出文件: {output_path}")
        return output_path

    # ══════════════════════════════════════════════════════════
    #  仅 Overlay 模式：只输出 overlay 层（带 alpha）
    # ══════════════════════════════════════════════════════════

    def _render_overlay_only(
        self, fit_data, fit_time_lookup, record_lookup, widgets,
        canvas_width, canvas_height, fps, total_frames,
        output_path, codec, crf, num_workers, batch_size,
        start_time, progress_callback,
    ):
        """仅 Overlay 模式：输出带 Alpha 通道的 overlay 视频"""

        # 确定输出格式和编码器
        if codec == "libvpx-vp9":
            # WebM VP9：支持透明度
            out_codec = "libvpx-vp9"
            pix_fmt = "yuva420p"
            if not output_path.lower().endswith(".webm"):
                base, _ = os.path.splitext(output_path)
                output_path = base + ".webm"
            codec_opts = {"crf": str(crf), "b:v": "0"}
            self.add_log(f"   格式: WebM (VP9 + alpha), crf={crf}")
        else:
            # MOV QuickTime Animation (qtrle) — 无损、支持 alpha、广泛兼容
            out_codec = "qtrle"
            pix_fmt = "argb"
            if not output_path.lower().endswith(".mov"):
                base, _ = os.path.splitext(output_path)
                output_path = base + ".mov"
            codec_opts = {}
            self.add_log(f"   格式: MOV (qtrle 无损 + alpha)")

        # 创建输出
        out = av.open(output_path, "w")
        v_out = out.add_stream(out_codec, Fraction(fps).limit_denominator(100000))
        v_out.width = canvas_width
        v_out.height = canvas_height
        v_out.pix_fmt = pix_fmt
        if codec_opts:
            v_out.options = codec_opts

        self.add_log(f"🔧 输出编码: {out_codec}, {canvas_width}×{canvas_height} @ {fps:.2f}fps")

        # ── 逐帧渲染 overlay + 编码 ──
        encoded_count = 0
        frame_idx = 0

        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            while frame_idx < total_frames and not self._cancelled:
                batch_end = min(frame_idx + batch_size, total_frames)
                batch_count = batch_end - frame_idx

                # 提交本批 overlay 渲染任务
                futures = {}
                for i in range(batch_count):
                    idx = frame_idx + i
                    future = executor.submit(
                        self._render_overlay_frame,
                        fit_data, fit_time_lookup[idx], record_lookup[idx],
                        widgets, canvas_width, canvas_height,
                    )
                    futures[future] = idx

                # 等待所有 future 完成
                overlay_results = {}
                for future in as_completed(futures):
                    if self._cancelled:
                        break
                    idx = futures[future]
                    try:
                        overlay_results[idx] = future.result()
                    except Exception as e:
                        self.add_log(f"❌ overlay 渲染失败 (frame {idx}): {e}", "error")
                        overlay_results[idx] = None

                # 按序编码
                for i in range(batch_count):
                    if self._cancelled:
                        break

                    current_idx = frame_idx + i
                    overlay_img = overlay_results.get(current_idx)

                    if overlay_img is None:
                        overlay_img = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))

                    # 转为 PyAV VideoFrame
                    if pix_fmt == "argb":
                        # qtrle + argb：直接从 RGBA numpy 数组
                        arr = np.array(overlay_img)  # (H, W, 4) RGBA
                        # RGBA → ARGB（swap R 和 A 通道）
                        argb_arr = np.empty_like(arr)
                        argb_arr[:, :, 0] = arr[:, :, 3]  # A
                        argb_arr[:, :, 1] = arr[:, :, 2]  # R (B in BGRA)
                        argb_arr[:, :, 2] = arr[:, :, 1]  # G
                        argb_arr[:, :, 3] = arr[:, :, 0]  # B (R in BGRA)
                        # 实际上 PyAV from_ndarray 对于 argb 格式期望的是 ARGB 顺序
                        # 但 PIL 是 RGBA，需要转换
                        # 更可靠的方式：用 reformat
                        out_frame = av.VideoFrame.from_ndarray(arr, format="rgba")
                        out_frame = out_frame.reformat(format="argb")
                    else:
                        # yuva420p：从 RGBA 转
                        arr = np.array(overlay_img)  # (H, W, 4) RGBA
                        out_frame = av.VideoFrame.from_ndarray(arr, format="rgba")
                        out_frame = out_frame.reformat(format="yuva420p")

                    for packet in v_out.encode(out_frame):
                        out.mux(packet)

                    encoded_count += 1

                    # 更新进度
                    if encoded_count % 10 == 0 or encoded_count == total_frames:
                        elapsed = time.time() - start_time
                        current_fps = encoded_count / elapsed if elapsed > 0 else 0
                        self.stats["frames_rendered"] = encoded_count
                        self.stats["frames_encoded"] = encoded_count
                        self.stats["overlay_fps"] = round(current_fps, 1)
                        self.stats["encode_fps"] = round(current_fps, 1)
                        self.stats["elapsed_sec"] = round(elapsed, 1)
                        if current_fps > 0:
                            remaining = (total_frames - encoded_count) / current_fps
                            self.stats["eta_sec"] = round(remaining, 0)
                        self.stats["progress_pct"] = round(
                            min(encoded_count, total_frames) / total_frames * 100, 1
                        )

                        self.add_log(
                            f"📊 进度: {encoded_count}/{total_frames} 帧, "
                            f"{current_fps:.1f} fps, ETA {self.stats['eta_sec']:.0f}s",
                            "progress"
                        )

                        if progress_callback and encoded_count % 20 == 0:
                            progress_callback(dict(self.stats))

                frame_idx = batch_end

        # flush 编码器
        for packet in v_out.encode():
            out.mux(packet)

        out.close()
        self.add_log(f"💾 输出文件: {output_path}")
        return output_path

    # ── Overlay 帧渲染 ─────────────────────────────────────

    @staticmethod
    def _render_overlay_frame(fit_data, fit_time, record, widgets, canvas_width, canvas_height):
        """渲染单帧 overlay（在线程池中执行）"""
        from services.frame_renderer import FrameRenderer

        canvas = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))

        for widget in widgets:
            if not widget.visible:
                continue
            FrameRenderer._render_widget(canvas, widget, record, fit_data, fit_time)

        return canvas


# ══════════════════════════════════════════════════════════
#  向后兼容：保留 _render_overlay_in_subprocess 函数签名
# ══════════════════════════════════════════════════════════

def _render_overlay_in_subprocess(
    fit_data_dict: dict,
    fit_time_iso: str,
    widgets_list: list,
    canvas_width: int,
    canvas_height: int,
) -> bytes:
    """在子进程中渲染单帧 overlay，返回 RGBA PNG bytes。

    保留此函数以兼容现有测试。新管线不再使用多进程模式。
    """
    from models.fit_data import FitData as _FitData
    from models.overlay_template import WidgetConfig as _WidgetConfig
    from datetime import datetime
    from services.frame_renderer import FrameRenderer

    fit_data = _FitData.from_dict(fit_data_dict)
    fit_time = datetime.fromisoformat(fit_time_iso) if fit_time_iso else None
    widgets = [_WidgetConfig.from_dict(w) for w in widgets_list]

    overlay = FrameRenderer.render_frame(
        fit_data=fit_data,
        fit_time=fit_time,
        widgets=widgets,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )

    buf = io.BytesIO()
    overlay.save(buf, format="PNG")
    return buf.getvalue()
