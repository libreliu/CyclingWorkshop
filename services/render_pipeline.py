"""渲染管线服务（多进程流水线 + 共享内存 + Tick 模式）

架构：
  多进程模式（默认）：
    ┌──────────┐    shared_buf    ┌──────────────┐    shared_buf    ┌──────────┐
    │ Decode   │ ──────────────► │ Overlay      │ ──────────────► │ Encode   │
    │ Service  │   BG RGBA       │ Service      │   Composite     │ Service  │
    │ (PyAV)   │                 │ (Pillow)     │   RGB(A)        │ (PyAV)   │
    └──────────┘                 └──────────────┘                  └──────────┘
    三个子进程通过 SharedFrameBuffer 流水线协作，真正并行（绕过 GIL）

    overlay-only 模式下省略 Decode 进程，Overlay 直接生成 RGBA 帧。

  Tick 模式（主线程调试模式）：
    当多进程不可用（Windows spawn 问题、共享内存不足等）时自动降级
    三个 Service 类提供 init()/tick()/finish()/cleanup() 方法，
    在主线程中按序执行，无需启动子进程，可直接断点调试

模式：
  - 合成模式（overlay_only=False）：overlay + 背景视频合成，输出 MP4
  - 仅 Overlay（overlay_only=True）：只输出 overlay 层（保留 alpha），
    输出 MOV（qtrle 无损）或 WebM（VP9），用户可自行在 NLE 中编辑
"""
import io
import os
import multiprocessing as mp
import threading
import time
from typing import Optional

from models.fit_data import FitData, FitRecord, FitSession
from models.overlay_template import WidgetConfig
from models.video_config import TimeSyncConfig

from PIL import Image

import av  # PyAV
import numpy as np
from fractions import Fraction


def _try_multiprocessing_available() -> bool:
    """检测多进程是否可用（Windows spawn + shared_memory）"""
    try:
        from multiprocessing import shared_memory
        test_shm = shared_memory.SharedMemory(create=True, size=1024)
        test_shm.close()
        test_shm.unlink()
        return True
    except Exception:
        return False


class RenderPipeline:
    """渲染管线（多进程流水线 / 串行回退 + 实时日志）"""

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
        self._use_multiprocess = _try_multiprocessing_available()

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
        overlay_codec: str = "qtrle",
        num_workers: int = 4,
        batch_size: int = 8,
        progress_callback=None,
        hwaccel_decode: bool = False,
    ) -> dict:
        self._cancelled = False
        self.logs = []
        start_time = time.time()

        try:
            # ── 0. 获取视频信息 & 预计算 ──────────────────────
            from services.fit_parser import FitParserService
            from services.video_analyzer import VideoAnalyzerService

            video_info = VideoAnalyzerService.analyze(video_path) if not overlay_only else None
            if not overlay_only and not video_info:
                return {"status": "error", "error": f"视频分析失败: {video_path}", "stats": self.stats}

            if fps is None or fps <= 0:
                fps = video_info.fps or 29.97 if video_info else 29.97

            if end_sec is None or end_sec <= 0:
                end_sec = video_info.duration if video_info else 0

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
            self.add_log(f"   引擎: PyAV {av.__version__} + 多进程流水线" if self._use_multiprocess
                         else f"   引擎: PyAV {av.__version__} + 串行回退")

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

            assert(self._use_multiprocess)

            actual_output = self._render_pipeline(
                video_path, fit_data, fit_time_lookup, record_lookup, widgets,
                canvas_width, canvas_height, fps, total_frames,
                start_sec, end_sec, output_path, codec, preset, crf,
                audio_mode, overlay_only, overlay_codec,
                num_workers, start_time, progress_callback,
                rotation=video_info.rotation if video_info else 0,
                hwaccel_decode=hwaccel_decode,
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
    #  多进程流水线（统一 composite / overlay-only）
    # ══════════════════════════════════════════════════════════

    def _render_pipeline(
        self, video_path, fit_data, fit_time_lookup, record_lookup, widgets,
        canvas_width, canvas_height, fps, total_frames,
        start_sec, end_sec, output_path, codec, preset, crf,
        audio_mode, overlay_only, overlay_codec,
        num_workers, start_time, progress_callback,
        rotation=0, hwaccel_decode=False,
    ):
        """多进程流水线：Decode → Overlay → Encode

        overlay_only=True 时省略 Decode 进程，Overlay 直接生成 RGBA overlay 帧，
        输出缓冲区通道数为 4（保留 alpha），编码器使用 qtrle/VP9。
        """
        from services.render_services import (
            SharedFrameBuffer, SlotState,
            DecodeService, OverlayService, EncodeService,
            LogForwarder,
        )

        N_SLOTS = 4  # 每个缓冲区的槽数

        # ── 确定输出参数 ──
        out_channels = 4 if overlay_only else 3  # overlay-only: RGBA; composite: RGB

        # ── 创建共享内存缓冲区 ──
        bg_buf = None
        if not overlay_only:
            bg_buf = SharedFrameBuffer(None, canvas_width, canvas_height,
                                        channels=4, n_slots=N_SLOTS)
        out_buf = SharedFrameBuffer(None, canvas_width, canvas_height,
                                     channels=out_channels, n_slots=N_SLOTS)

        if bg_buf:
            self.add_log(f"🔧 共享内存: bg={bg_buf.name}, out={out_buf.name}")
        else:
            self.add_log(f"🔧 共享内存: out={out_buf.name} (overlay-only, 无 bg_buf)")

        # ── 创建进程间通信对象 ──
        frame_meta_queue = mp.Queue(maxsize=N_SLOTS * 2)
        encode_meta_queue = mp.Queue(maxsize=N_SLOTS * 2)
        log_queue = mp.Queue(maxsize=1000)
        cancel_event = mp.Event()

        # ── 创建共享进度计数器（EncodeService 写，主进程读）──
        encode_progress = mp.Value('i', 0)  # 已编码帧数
        overlay_progress = mp.Value('i', 0)  # 已合成帧数

        # ── 序列化 fit_data / widgets / record_lookup ──
        fit_data_dict = fit_data.to_dict(include_records=True)
        widgets_dicts = [w.to_dict() for w in widgets]
        record_lookup_dicts = [
            r.to_dict() if r is not None else None for r in record_lookup
        ]
        # fit_time_lookup 序列化：datetime → ISO str
        fit_time_lookup_serialized = []
        for ft in fit_time_lookup:
            if ft is not None:
                fit_time_lookup_serialized.append(ft.isoformat() if hasattr(ft, 'isoformat') else str(ft))
            else:
                fit_time_lookup_serialized.append(None)

        # ── 启动日志转发 ──
        log_forwarder = LogForwarder(log_queue, self.add_log)
        log_forwarder.start()

        # ── 构造 Service 实例 ──
        # 注意：子进程中需要通过 name 连接共享内存，所以传 name 字符串而非对象
        self.add_log("🚀 启动流水线进程...")

        # 1) Decode 服务（仅 composite 模式）
        decode_proc = None
        decode_feeder_thread = None
        if not overlay_only:
            # 用向后兼容函数式入口（子进程需要通过 name 连接 bg_buf）
            from services.render_services import decode_service_main
            decode_proc = mp.Process(
                target=decode_service_main,
                name="DecodeService",
                args=(video_path, start_sec, end_sec, fps,
                      canvas_width, canvas_height,
                      bg_buf.name, N_SLOTS,
                      frame_meta_queue, log_queue, cancel_event,
                      rotation, hwaccel_decode),
            )
            decode_proc.start()
        else:
            # overlay-only: 向 frame_meta_queue 发送所有帧的 (slot=0, frame_idx)
            decode_feeder_thread = threading.Thread(
                target=self._overlay_only_feeder,
                args=(frame_meta_queue, total_frames, N_SLOTS),
                daemon=True,
            )
            decode_feeder_thread.start()

        # 2) Overlay 服务
        from services.render_services import overlay_service_main
        overlay_proc = mp.Process(
            target=overlay_service_main,
            name="OverlayService",
            args=(fit_data_dict, fit_time_lookup_serialized, record_lookup_dicts,
                  widgets_dicts, canvas_width, canvas_height,
                  overlay_only,
                  bg_buf.name if bg_buf else "", N_SLOTS,
                  out_buf.name, out_channels, N_SLOTS,
                  frame_meta_queue, encode_meta_queue,
                  log_queue, cancel_event, num_workers,
                  overlay_progress),
        )
        overlay_proc.start()

        # 3) Encode 服务
        from services.render_services import encode_service_main
        encode_proc = mp.Process(
            target=encode_service_main,
            name="EncodeService",
            args=(output_path, canvas_width, canvas_height, fps,
                  out_channels,
                  codec, preset, crf,
                  overlay_only, overlay_codec,
                  out_buf.name, N_SLOTS,
                  encode_meta_queue, log_queue, cancel_event,
                  total_frames, encode_progress),
        )
        encode_proc.start()

        procs = [p for p in (decode_proc, overlay_proc, encode_proc) if p is not None]

        # ── 主进程监控 ──
        try:
            while True:
                if self._cancelled:
                    cancel_event.set()
                    break

                # 检查子进程状态，记录异常退出
                for p in procs:
                    if not p.is_alive() and p.exitcode is not None and p.exitcode != 0:
                        self.add_log(f"⚠️ {p.name} 异常退出（exitcode={p.exitcode}）", "error")

                # 等待所有子进程结束
                if all(not p.is_alive() for p in procs):
                    break

                # 更新进度（从子进程读取实际帧数）
                elapsed = time.time() - start_time
                encoded = encode_progress.value
                overlaid = overlay_progress.value
                self.stats["frames_rendered"] = overlaid
                self.stats["frames_encoded"] = encoded
                self.stats["elapsed_sec"] = round(elapsed, 1)
                # 用编码帧数计算进度（最准确，因为编码是最后一步）
                progress_frames = encoded if encoded > 0 else overlaid
                if progress_frames > 0 and elapsed > 0:
                    current_fps = progress_frames / elapsed
                    self.stats["overlay_fps"] = round(overlaid / elapsed, 1) if elapsed > 0 else 0
                    self.stats["encode_fps"] = round(encoded / elapsed, 1) if elapsed > 0 else 0
                    remaining = (total_frames - progress_frames) / current_fps
                    self.stats["eta_sec"] = round(remaining, 0)
                    self.stats["progress_pct"] = round(min(progress_frames, total_frames) / total_frames * 100, 1)

                if progress_callback:
                    progress_callback(dict(self.stats))

                time.sleep(1.0)

        except KeyboardInterrupt:
            cancel_event.set()
        finally:
            # 等待子进程结束
            timeout = 10
            for p in procs:
                p.join(timeout=timeout)
            # 强制终止
            for p in procs:
                if p.is_alive():
                    p.terminate()

            # 清理共享内存
            log_forwarder.stop()
            if bg_buf:
                bg_buf.close()
                bg_buf.unlink()
            out_buf.close()
            out_buf.unlink()

            # 处理音频（仅合成模式）
            actual_output = output_path
            if not self._cancelled and not overlay_only and audio_mode == "copy":
                actual_output = self._mux_audio(
                    output_path, video_path, start_sec, end_sec)

        return actual_output

    @staticmethod
    def _overlay_only_feeder(frame_meta_queue, total_frames, n_slots):
        """overlay-only 模式下，替代 Decode 进程向 frame_meta_queue 喂帧元数据。

        每帧使用 slot_idx=0（bg_buf 不存在），让 Overlay 服务逐帧处理。
        """
        for i in range(total_frames):
            frame_meta_queue.put((0, i))  # (bg_slot=0, frame_idx)
        frame_meta_queue.put(None)  # 终止信号

    def _mux_audio(self, video_path, audio_source_path, start_sec, end_sec):
        """将音频从源视频混流到输出视频（PyAV re-encode）"""
        import gc
        # 读取原视频输出，重新加入音频
        temp_path = video_path + ".noaudio.mp4"
        if os.path.exists(temp_path):
            os.remove(temp_path)
        os.rename(video_path, temp_path)

        inp_video = None
        inp_audio_src = None
        out = None
        try:
            inp_video = av.open(temp_path)
            inp_audio_src = av.open(audio_source_path)

            out = av.open(video_path, "w")

            # 视频流：直接复制（remux，不重编码）
            in_v = inp_video.streams.video[0]
            try:
                v_out = out.add_stream_from_template(in_v)
            except (AttributeError, TypeError):
                # PyAV < 17.0.0 fallback
                v_out = out.add_stream(codec_name=in_v.codec_context.name,
                                       rate=in_v.average_rate)
                v_out.width = in_v.codec_context.width
                v_out.height = in_v.codec_context.height
                v_out.pix_fmt = in_v.codec_context.pix_fmt

            # 音频流：re-encode
            in_a = inp_audio_src.streams.audio[0] if inp_audio_src.streams.audio else None
            a_out = None
            if in_a:
                a_out = out.add_stream(codec_name="aac", rate=in_a.sample_rate)
                a_out.layout = "stereo"

                if start_sec > 0:
                    try:
                        audio_ts = int(start_sec / float(in_a.time_base))
                        inp_audio_src.seek(audio_ts, stream=in_a)
                    except:
                        pass

            # 复制视频包（remux，不重编码）
            for packet in inp_video.demux(in_v):
                if packet.dts is None:
                    continue
                try:
                    packet.stream = v_out  # 必须重新关联到输出流
                    out.mux(packet)
                except Exception as mux_err:
                    self.add_log(f"⚠️ 视频包 mux 失败: {mux_err}", "warning")
                    break

            # 编码音频
            if in_a and a_out:
                try:
                    for a_frame in inp_audio_src.decode(in_a):
                        a_frame.pts = None
                        for packet in a_out.encode(a_frame):
                            out.mux(packet)
                    for packet in a_out.encode():
                        out.mux(packet)
                except Exception as audio_err:
                    self.add_log(f"⚠️ 音频编码失败: {audio_err}", "warning")

            # 显式关闭，确保文件句柄释放
            out.close()
            out = None
            inp_video.close()
            inp_video = None
            inp_audio_src.close()
            inp_audio_src = None

            # 强制 GC 释放可能残留的文件句柄引用
            gc.collect()

            os.remove(temp_path)
            self.add_log(f"🔊 音频混流完成")
            return video_path

        except Exception as e:
            self.add_log(f"⚠️ 音频混流失败: {e}，输出无音频", "warning")
            # 确保关闭所有打开的容器
            for container in (out, inp_video, inp_audio_src):
                if container is not None:
                    try:
                        container.close()
                    except:
                        pass
            gc.collect()
            # 恢复无音频版本
            if os.path.exists(temp_path) and not os.path.exists(video_path):
                try:
                    os.rename(temp_path, video_path)
                except OSError:
                    pass
            elif os.path.exists(temp_path):
                # video_path 已存在（部分写入），删除临时文件
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            return video_path

    # ══════════════════════════════════════════════════════════
    #  主线程 Tick 模式（用于调试）
    # ══════════════════════════════════════════════════════════

    def render_video_tick_mode(
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
        overlay_codec: str = "qtrle",
        max_ticks: Optional[int] = None,
        hwaccel_decode: bool = False,
    ) -> dict:
        """主线程 Tick 模式渲染：逐步调用每个 service 的 tick()

        用于调试各环节问题。三个 service 在主线程中按序 Tick，
        不启动子进程，可直接断点调试。

        Args:
            max_ticks: 最多执行的 tick 轮数。None 则执行到完成。
        """
        import queue
        from services.render_services import (
            SharedFrameBuffer, SlotState,
            DecodeService, OverlayService, EncodeService,
        )

        self._cancelled = False
        self.logs = []
        start_time = time.time()

        # 获取视频信息
        from services.fit_parser import FitParserService
        from services.video_analyzer import VideoAnalyzerService

        video_info = VideoAnalyzerService.analyze(video_path) if not overlay_only else None
        if not overlay_only and not video_info:
            return {"status": "error", "error": f"视频分析失败: {video_path}", "stats": self.stats}

        if fps is None or fps <= 0:
            fps = video_info.fps or 29.97 if video_info else 29.97
        if end_sec is None or end_sec <= 0:
            end_sec = video_info.duration if video_info else 0

        total_duration = end_sec - start_sec
        total_frames = int(total_duration * fps)

        if total_frames <= 0:
            return {"status": "error", "error": f"渲染范围无效", "stats": self.stats}

        self.stats["total_frames"] = total_frames
        self.stats["phase"] = "rendering"

        # 预计算
        record_lookup = [None] * total_frames
        fit_time_lookup = [None] * total_frames
        for i in range(total_frames):
            video_elapsed = i / fps + start_sec
            fit_time = time_sync.fit_time_at_video_seconds(video_elapsed)
            fit_time_lookup[i] = fit_time
            if fit_time is not None:
                record_lookup[i] = FitParserService.get_record_at(fit_data, fit_time)

        # 序列化
        fit_data_dict = fit_data.to_dict(include_records=True)
        widgets_dicts = [w.to_dict() for w in widgets]
        record_lookup_dicts = [r.to_dict() if r is not None else None for r in record_lookup]
        fit_time_lookup_serialized = []
        for ft in fit_time_lookup:
            if ft is not None:
                fit_time_lookup_serialized.append(ft.isoformat() if hasattr(ft, 'isoformat') else str(ft))
            else:
                fit_time_lookup_serialized.append(None)

        out_channels = 4 if overlay_only else 3

        # 创建本地 queue（非 mp.Queue）
        frame_meta_queue = queue.Queue(maxsize=8)
        encode_meta_queue = queue.Queue(maxsize=8)

        def log_fn(level, msg):
            self.add_log(msg, level)

        def cancel_check():
            return self._cancelled

        # 创建共享内存缓冲区
        bg_buf = None
        if not overlay_only:
            bg_buf = SharedFrameBuffer(None, canvas_width, canvas_height,
                                        channels=4, n_slots=4)
        out_buf = SharedFrameBuffer(None, canvas_width, canvas_height,
                                     channels=out_channels, n_slots=4)

        # 创建 Service 实例
        decode_svc = None
        if not overlay_only:
            decode_svc = DecodeService(
                video_path=video_path,
                start_sec=start_sec,
                end_sec=end_sec,
                fps=fps,
                canvas_width=canvas_width,
                canvas_height=canvas_height,
                bg_buf=bg_buf,
                frame_meta_queue=frame_meta_queue,
                log_fn=log_fn,
                cancel_check=cancel_check,
                rotation=video_info.rotation if video_info else 0,
                hwaccel_decode=hwaccel_decode,
            )
            decode_svc.init()
        else:
            # overlay-only feeder
            for i in range(total_frames):
                frame_meta_queue.put((0, i))
            frame_meta_queue.put(None)

        overlay_svc = OverlayService(
            fit_data_dict=fit_data_dict,
            fit_time_lookup=fit_time_lookup_serialized,
            record_lookup_dicts=record_lookup_dicts,
            widgets_dicts=widgets_dicts,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
            overlay_only=overlay_only,
            bg_buf=bg_buf,
            out_buf=out_buf,
            frame_meta_queue=frame_meta_queue,
            encode_meta_queue=encode_meta_queue,
            log_fn=log_fn,
            cancel_check=cancel_check,
        )
        overlay_svc.init()

        encode_svc = EncodeService(
            output_path=output_path,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
            fps=fps,
            out_channels=out_channels,
            overlay_only=overlay_only,
            overlay_codec=overlay_codec,
            out_buf=out_buf,
            encode_meta_queue=encode_meta_queue,
            log_fn=log_fn,
            cancel_check=cancel_check,
            total_frames=total_frames,
            codec=codec,
            preset=preset,
            crf=crf,
        )
        encode_svc.init()

        self.add_log("🔧 Tick 模式: 三个 Service 在主线程中按序执行")

        # 主循环
        tick_count = 0
        try:
            while True:
                if self._cancelled:
                    break
                if max_ticks is not None and tick_count >= max_ticks:
                    self.add_log(f"⏹ 达到最大 tick 数 ({max_ticks})，停止", "warning")
                    break

                # Decode tick
                decode_alive = False
                if decode_svc is not None:
                    decode_alive = decode_svc.tick()
                    if not decode_alive:
                        decode_svc.finish()
                        decode_svc = None

                # Overlay tick
                overlay_alive = overlay_svc.tick()
                if not overlay_alive:
                    overlay_svc.finish()

                # Encode tick
                encode_alive = encode_svc.tick()
                if not encode_alive:
                    encode_svc.finish()

                tick_count += 1

                # 更新进度
                encoded = encode_svc._encode_count
                self.stats["frames_rendered"] = encoded
                self.stats["frames_encoded"] = encoded
                elapsed = time.time() - start_time
                self.stats["elapsed_sec"] = round(elapsed, 1)
                if encoded > 0 and elapsed > 0:
                    self.stats["overlay_fps"] = round(encoded / elapsed, 1)
                    self.stats["encode_fps"] = round(encoded / elapsed, 1)
                    self.stats["progress_pct"] = round(encoded / total_frames * 100, 1)

                # 所有 service 都完成
                if decode_svc is None and not overlay_alive and not encode_alive:
                    break

        except KeyboardInterrupt:
            self._cancelled = True
        finally:
            # flush 编码器（仅当 finish 尚未被调用时）
            if not encode_svc._done:
                encode_svc.finish()
            # cleanup
            if decode_svc is not None:
                decode_svc.cleanup()
            overlay_svc.cleanup()
            encode_svc.cleanup()

            # 清理共享内存
            if bg_buf is not None:
                bg_buf.close()
                bg_buf.unlink()
            out_buf.close()
            out_buf.unlink()

        actual_output = encode_svc.actual_output_path

        # 音频混流
        if not self._cancelled and not overlay_only and audio_mode == "copy":
            from services.video_analyzer import VideoAnalyzerService
            vi = VideoAnalyzerService.analyze(video_path)
            if vi:
                actual_output = self._mux_audio(actual_output, video_path, start_sec, end_sec)

        total_time = time.time() - start_time
        self.stats["phase"] = "done" if not self._cancelled else "cancelled"
        self.add_log(f"✅ Tick 模式完成: {tick_count} ticks, {total_time:.1f}s")

        return {
            "status": "completed" if not self._cancelled else "cancelled",
            "error": None,
            "stats": self.stats,
            "output_path": actual_output,
        }

    # ── 工具方法 ─────────────────────────────────────────────

    @staticmethod
    def _resolve_encode_params(output_path, overlay_only, overlay_codec,
                                codec, preset, crf):
        """根据模式解析编码参数，返回 (output_path, codec, pix_fmt, codec_opts)

        可能根据 overlay_codec / codec 自动调整 output_path 扩展名。

        支持的合成模式编码器：
          软件：libx264, libx265, libvpx-vp9, libaom-av1
          硬件：h264_nvenc, hevc_nvenc, h264_amf, hevc_amf
        """
        if overlay_only:
            if overlay_codec == "libvpx-vp9":
                out_codec = "libvpx-vp9"
                pix_fmt = "yuva420p"
                if not output_path.lower().endswith(".webm"):
                    base, _ = os.path.splitext(output_path)
                    output_path = base + ".webm"
                codec_opts = {"crf": str(crf), "b:v": "0"}
            else:
                out_codec = "qtrle"
                pix_fmt = "argb"
                if not output_path.lower().endswith(".mov"):
                    base, _ = os.path.splitext(output_path)
                    output_path = base + ".mov"
                codec_opts = {}
        else:
            out_codec = codec
            # 根据编码器选择参数
            if codec in ("h264_nvenc", "hevc_nvenc"):
                pix_fmt = "yuv420p"
                codec_opts = {"preset": preset, "cq": str(crf)}
            elif codec in ("h264_amf", "hevc_amf"):
                pix_fmt = "yuv420p"
                codec_opts = {"quality": preset, "rc": "cqp", "qp_i": str(crf),
                              "qp_p": str(crf), "qp_b": str(crf)}
            elif codec == "libvpx-vp9":
                pix_fmt = "yuv420p"
                if not output_path.lower().endswith(".webm"):
                    base, _ = os.path.splitext(output_path)
                    output_path = base + ".webm"
                codec_opts = {"crf": str(crf), "b:v": "0", "cpu-used": preset}
            elif codec == "libaom-av1":
                pix_fmt = "yuv420p"
                codec_opts = {"crf": str(crf), "cpu-used": preset}
            elif codec == "libx265":
                pix_fmt = "yuv420p"
                codec_opts = {"preset": preset, "crf": str(crf)}
            else:
                # libx264 默认
                pix_fmt = "yuv420p"
                codec_opts = {"preset": preset, "crf": str(crf)}

        return output_path, out_codec, pix_fmt, codec_opts

    @staticmethod
    def _encode_overlay_frame(v_out, out_container, overlay_img, pix_fmt):
        """编码单帧 overlay 图像（RGBA → 目标 pix_fmt）"""
        arr = np.array(overlay_img, dtype=np.uint8)
        out_frame = av.VideoFrame.from_ndarray(arr, format="rgba")
        if pix_fmt != "rgba":
            out_frame = out_frame.reformat(format=pix_fmt)
        for packet in v_out.encode(out_frame):
            out_container.mux(packet)

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

    def _update_progress(self, encoded_count, total_frames, start_time, progress_callback):
        """更新进度统计"""
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
        if progress_callback:
            progress_callback(dict(self.stats))
