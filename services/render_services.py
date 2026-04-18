"""渲染流水线服务（多进程并行 + 共享内存）

架构：
  三个独立 Service 通过共享内存流水线协作：

  ┌──────────┐    shared_buf    ┌──────────────┐    shared_buf    ┌──────────┐
  │ Decode   │ ──────────────► │ Overlay      │ ──────────────► │ Encode   │
  │ Service  │   BG RGBA       │ Service      │   Composite     │ Service  │
  │ (PyAV)   │                 │ (Pillow)     │   RGB(A)        │ (PyAV)   │
  └──────────┘                 └──────────────┘                  └──────────┘

  每个服务是一个类，提供 Tick() 方法：
  - DecodeService.Tick()：解码一帧背景视频 → 写入 bg_buf
  - OverlayService.Tick()：读 bg_buf + 渲染 widget → 合成 → 写入 out_buf
  - EncodeService.Tick()：读 out_buf → PyAV 编码 → mux 输出

  运行方式：
  1. mp.Process(target=svc.run_loop)：子进程循环 Tick
  2. 主线程手动 Tick：逐步调试，定位问题环节

  通信：
  - SharedFrameBuffer：基于 multiprocessing 共享内存的环形缓冲区
    每帧一个 slot，slot 有 state 标记（EMPTY/FILLED/DONE）
    生产者写完置 FILLED，消费者读完置 EMPTY
  - 帧元数据（slot_idx, frame_idx）通过 Queue 传递

  降级：
  - 如果共享内存创建失败或子进程异常，自动降级为串行模式
"""

import multiprocessing as mp
from multiprocessing import shared_memory as shm_module
import numpy as np
import time
import os
import struct
import threading
from typing import Optional
from enum import IntEnum

import av
from PIL import Image
from fractions import Fraction


# ══════════════════════════════════════════════════════════
#  SharedFrameBuffer：基于共享内存的帧环形缓冲区
# ══════════════════════════════════════════════════════════

class SlotState(IntEnum):
    EMPTY = 0    # 可写入
    FILLED = 1   # 已写入，可读取
    DONE = 2     # 终止信号


class SharedFrameBuffer:
    """多进程共享内存帧缓冲区

    结构：
      states[N_SLOTS]  : int32 每个槽的状态
      frames[N_SLOTS]  : 每个槽 H*W*C 字节的 RGBA 数据

    使用方式：
      生产者：wait_slot(idx, EMPTY) → 写入 → set_state(idx, FILLED)
      消费者：wait_slot(idx, FILLED) → 读取 → set_state(idx, EMPTY)
      终止：  set_state(idx, DONE)
    """

    def __init__(self, name: Optional[str], width: int, height: int,
                 channels: int = 4, n_slots: int = 4):
        """创建或连接共享内存缓冲区

        Args:
            name: 共享内存名称。None 则创建新的（生产者），
                  指定名称则连接已有的（消费者）。
            width, height: 帧尺寸
            channels: 通道数（4=RGBA, 3=RGB）
            n_slots: 缓冲槽数
        """
        self.width = width
        self.height = height
        self.channels = channels
        self.n_slots = n_slots
        self.frame_size = width * height * channels
        self.slot_bytes = self.frame_size * np.dtype(np.uint8).itemsize
        self._is_creator = name is None

        # 总大小：states 数组 + frames 数据
        self.states_size = n_slots * np.dtype(np.int32).itemsize
        self.frames_size = n_slots * self.slot_bytes
        self.total_size = self.states_size + self.frames_size

        if name is None:
            # 生产者：创建共享内存
            self._shm = shm_module.SharedMemory(create=True, size=self.total_size)
        else:
            # 消费者：连接已有共享内存
            self._shm = shm_module.SharedMemory(name=name)
        self._shm_name = self._shm.name

        # 只保留一份底层 buffer 引用，并使用 offset 创建 ndarray，
        # 避免长期持有多个 memoryview slice，降低 close() 时的 exported
        # pointers 概率。
        self._buffer = self._shm.buf
        self._states = np.ndarray(
            (n_slots,), dtype=np.int32,
            buffer=self._buffer, offset=0
        )

        if name is None:
            # 初始化所有状态为 EMPTY
            self._states[:] = SlotState.EMPTY

    @property
    def name(self) -> str:
        return self._shm.name

    def get_frame_view(self, slot_idx: int) -> np.ndarray:
        """获取指定槽的帧 numpy 视图（可直接读写，零拷贝）

        注意：调用 close() 前必须确保所有 frame view 引用已释放，
        否则会触发 BufferError。建议每次使用后立即释放引用。
        """
        offset = slot_idx * self.slot_bytes
        return np.ndarray(
            (self.height, self.width, self.channels),
            dtype=np.uint8,
            buffer=self._buffer,
            offset=self.states_size + offset
        )

    def get_state(self, slot_idx: int) -> int:
        return self._states[slot_idx]

    def set_state(self, slot_idx: int, state: int):
        self._states[slot_idx] = state

    def wait_slot(self, slot_idx: int, expected_state: int,
                  timeout: float = 60.0, poll_interval: float = 0.001) -> bool:
        """自旋等待指定槽达到预期状态

        Returns:
            True: 达到预期状态
            False: 超时
        """
        deadline = time.monotonic() + timeout
        while True:
            if self._states[slot_idx] == expected_state:
                return True
            if self._states[slot_idx] == SlotState.DONE:
                return False  # 收到终止信号
            if time.monotonic() > deadline:
                return False  # 超时
            time.sleep(poll_interval)

    def signal_done(self):
        """向所有槽写入终止信号"""
        for i in range(self.n_slots):
            self._states[i] = SlotState.DONE

    def close(self):
        """关闭共享内存（不删除）。必须先释放所有 numpy view 引用。"""
        # 先释放本对象持有的 ndarray / memoryview 引用，再关闭底层 SharedMemory。
        self._states = None
        self._buffer = None
        try:
            self._shm.close()
        except FileNotFoundError:
            pass

    def unlink(self):
        """删除共享内存（仅创建者调用）"""
        if not self._is_creator:
            return
        try:
            self._shm.unlink()
        except (FileNotFoundError, AttributeError):
            pass


# ══════════════════════════════════════════════════════════
#  StandaloneService：服务基类（子进程运行入口）
# ══════════════════════════════════════════════════════════

class StandaloneService:
    """服务基类：提供子进程运行入口 run_loop()

    子类必须实现：
      - init(): 初始化资源
      - tick() -> bool: 处理一帧，返回 False 表示完成
      - finish(): 完成处理，发送终止信号
      - cleanup(): 清理资源

    可选覆盖：
      - _log(level, msg): 日志输出
      - _is_cancelled() -> bool: 检查取消状态
    """

    def __init__(self, log_fn=None, cancel_check=None):
        self._log_fn = log_fn
        self._cancel_check = cancel_check

    def _log(self, level: str, msg: str):
        if self._log_fn is None:
            return
        if hasattr(self._log_fn, 'put'):
            self._log_fn.put((level, msg))
        else:
            self._log_fn(level, msg)

    def _is_cancelled(self) -> bool:
        if self._cancel_check is None:
            return False
        if hasattr(self._cancel_check, 'is_set'):
            return self._cancel_check.is_set()
        return self._cancel_check()

    def run_loop(self):
        """子进程入口：循环 tick 直到完成"""
        try:
            self.init()
            while self.tick():
                pass
            self.finish()
        except Exception as e:
            self._log("error", f"[{self.__class__.__name__}] 异常: {e}")
            import traceback
            self._log("error", traceback.format_exc())
            self._handle_error()
        finally:
            self.cleanup()

    def _handle_error(self):
        """子类可覆盖的错误处理逻辑"""
        pass


# ══════════════════════════════════════════════════════════
#  DecodeService：PyAV 解码背景视频帧
# ══════════════════════════════════════════════════════════

class DecodeService(StandaloneService):
    """解码服务：逐帧解码背景视频，写入 bg_buf

    使用方式：
      方式 1（子进程）：
        svc = DecodeService(...)
        proc = mp.Process(target=svc.run_loop, name="DecodeService")
        proc.start()

      方式 2（主线程调试）：
        svc = DecodeService(...)
        svc.init()
        while svc.Tick():
            ...
        svc.cleanup()
    """

    def __init__(
        self,
        video_path: str,
        start_sec: float,
        end_sec: float,
        fps: float,
        canvas_width: int,
        canvas_height: int,
        bg_buf: Optional[SharedFrameBuffer],
        frame_meta_queue,
        log_fn=None,
        cancel_check=None,
        rotation: int = 0,
        hwaccel_decode: bool = False,
    ):
        super().__init__(log_fn=log_fn, cancel_check=cancel_check)
        self.video_path = video_path
        self.start_sec = start_sec
        self.end_sec = end_sec
        self.fps = fps
        self.canvas_width = canvas_width
        self.canvas_height = canvas_height
        self.bg_buf = bg_buf
        self.frame_meta_queue = frame_meta_queue
        self.rotation = rotation
        self.hwaccel_decode = hwaccel_decode

        # 内部状态（init 时初始化）
        self._container = None
        self._v_stream = None
        self._frame_iter = None
        self._frame_idx = 0
        self._slot_idx = 0
        self._total_frames = 0
        self._decode_count = 0
        self._t0 = 0.0
        self._done = False

    def _log(self, level: str, msg: str):
        if self._log_fn is None:
            return
        if hasattr(self._log_fn, 'put'):
            self._log_fn.put((level, msg))
        else:
            self._log_fn(level, msg)

    def _is_cancelled(self) -> bool:
        if self._cancel_check is None:
            return False
        if hasattr(self._cancel_check, 'is_set'):
            return self._cancel_check.is_set()
        return self._cancel_check()

    def init(self):
        """初始化 PyAV 容器和解码器"""
        self._container = av.open(self.video_path)
        self._v_stream = self._container.streams.video[0]
        self._v_stream.thread_type = "AUTO"

        # 尝试硬件加速解码
        self._hw_device = None
        if self.hwaccel_decode:
            try:
                # 尝试 CUDA (NVIDIA)
                self._hw_device = av.open("CUDA", mode="r")
                self._v_stream.codec_context.hw_device = self._hw_device
                self._log("info", "[Decode] 硬件加速解码已启用 (CUDA)")
            except Exception:
                try:
                    # 尝试 DXVA2 (Windows 通用)
                    self._v_stream.codec_context.hw_pix_fmt = "d3d11"
                    self._log("info", "[Decode] 硬件加速解码已启用 (D3D11)")
                except Exception as e2:
                    self._log("warning", f"[Decode] 硬件加速解码不可用，回退到 CPU 解码: {e2}")

        if self.start_sec > 0:
            target_ts = int(self.start_sec / float(self._v_stream.time_base))
            self._container.seek(target_ts, stream=self._v_stream)

        self._total_frames = int((self.end_sec - self.start_sec) * self.fps)
        self._frame_iter = self._container.decode(self._v_stream)
        self._t0 = time.perf_counter()
        self._log("info", f"[Decode] 初始化完成, 视频: {os.path.basename(self.video_path)}")

    def tick(self) -> bool:
        """解码一帧并写入 bg_buf。

        Returns:
            True: 成功写入一帧
            False: 解码结束或取消
        """
        if self._done:
            return False

        if self._is_cancelled():
            self._done = True
            return False

        if self._frame_idx >= self._total_frames:
            self._done = True
            return False

        # 获取下一帧
        try:
            v_frame = next(self._frame_iter)
        except StopIteration:
            self._done = True
            return False

        bg_buf = self.bg_buf
        if bg_buf is not None:
            # 等待可用槽
            if not bg_buf.wait_slot(self._slot_idx, SlotState.EMPTY, timeout=30):
                self._log("warning", f"[Decode] 等待槽 {self._slot_idx} 超时（Overlay 进程可能已退出）")
                self._done = True
                return False

            # 解码 + 转为 RGBA numpy
            bg_img = v_frame.to_image()

            # 手动旋转（PyAV/libavcodec 不自动旋转帧）
            if self.rotation == 90:
                bg_img = bg_img.transpose(Image.Transpose.ROTATE_270)
            elif self.rotation in (-90, 270):
                bg_img = bg_img.transpose(Image.Transpose.ROTATE_90)
            elif self.rotation in (180, -180):
                bg_img = bg_img.transpose(Image.Transpose.ROTATE_180)

            if bg_img.size != (self.canvas_width, self.canvas_height):
                bg_img = bg_img.resize((self.canvas_width, self.canvas_height), Image.LANCZOS)
            bg_rgba = bg_img.convert("RGBA")

            # 写入共享内存
            view = bg_buf.get_frame_view(self._slot_idx)
            np.copyto(view, np.array(bg_rgba, dtype=np.uint8))
            del view  # 释放 numpy view 引用

            bg_buf.set_state(self._slot_idx, SlotState.FILLED)

        # 通知 overlay 服务
        self.frame_meta_queue.put((self._slot_idx, self._frame_idx))

        self._decode_count += 1
        self._slot_idx = (self._slot_idx + 1) % (bg_buf.n_slots if bg_buf else 4)
        self._frame_idx += 1

        if self._decode_count % 50 == 0:
            elapsed = time.perf_counter() - self._t0
            self._log("progress",
                f"[Decode] {self._decode_count}/{self._total_frames} 帧, "
                f"{self._decode_count/elapsed:.1f} fps")

        return True

    def finish(self):
        """发送终止信号并关闭资源"""
        self.frame_meta_queue.put(None)  # 终止信号

        elapsed = time.perf_counter() - self._t0 if self._t0 else 0
        if self._decode_count > 0 and elapsed > 0:
            self._log("info", f"[Decode] 完成: {self._decode_count} 帧, {elapsed:.1f}s, "
                               f"{self._decode_count/elapsed:.1f} fps")
        self._done = True

    def _handle_error(self):
        """DecodeService 特定的错误处理"""
        try:
            self.frame_meta_queue.put(None)
            if self.bg_buf is not None:
                self.bg_buf.signal_done()
        except:
            pass

    def cleanup(self):
        """关闭 PyAV 容器和共享内存"""
        if self._container is not None:
            try:
                self._container.close()
            except:
                pass
            self._container = None
        if self._hw_device is not None:
            try:
                self._hw_device.close()
            except:
                pass
            self._hw_device = None
        if self.bg_buf is not None:
            try:
                self.bg_buf.close()
            except:
                pass


# ══════════════════════════════════════════════════════════
#  OverlayService：渲染 Widget + 合成背景
# ══════════════════════════════════════════════════════════

class OverlayService(StandaloneService):
    """Overlay 服务：读 bg_buf + 渲染 widget → 合成 → 写入 out_buf

    使用方式：
      方式 1（子进程）：
        svc = OverlayService(...)
        proc = mp.Process(target=svc.run_loop, name="OverlayService")
        proc.start()

      方式 2（主线程调试）：
        svc = OverlayService(...)
        svc.init()
        while svc.tick():
            ...
        svc.cleanup()
    """

    def __init__(
        self,
        fit_data_dict: dict,
        fit_time_lookup: list,
        record_lookup_dicts: list,
        widgets_dicts: list,
        canvas_width: int,
        canvas_height: int,
        overlay_only: bool,
        bg_buf: Optional[SharedFrameBuffer],
        out_buf: SharedFrameBuffer,
        frame_meta_queue,
        encode_meta_queue,
        log_fn=None,
        cancel_check=None,
        num_workers: int = 1,
        overlay_progress=None,
    ):
        super().__init__(log_fn=log_fn, cancel_check=cancel_check)
        self.fit_data_dict = fit_data_dict
        self.fit_time_lookup = fit_time_lookup
        self.record_lookup_dicts = record_lookup_dicts
        self.widgets_dicts = widgets_dicts
        self.canvas_width = canvas_width
        self.canvas_height = canvas_height
        self.overlay_only = overlay_only
        self.bg_buf = bg_buf
        self.out_buf = out_buf
        self.frame_meta_queue = frame_meta_queue
        self.encode_meta_queue = encode_meta_queue
        self.num_workers = num_workers
        self.overlay_progress = overlay_progress

        # 全局样式（从渲染设置传入）
        self.global_style = fit_data_dict.get("global_style", {})

        # 内部状态
        self._fit_data = None
        self._widgets = None
        self._record_lookup = None
        self._fit_time_lookup_dt = None
        self._out_slot_idx = 0
        self._overlay_count = 0
        self._decode_done = False
        self._t0 = 0.0
        self._done = False

    def init(self):
        """初始化：反序列化 fit_data/widgets"""
        from models.fit_data import FitData, FitRecord
        from models.overlay_template import WidgetConfig
        from services.frame_renderer import FrameRenderer
        from datetime import datetime

        self._fit_data = FitData.from_dict(self.fit_data_dict)
        self._widgets = [WidgetConfig.from_dict(w) for w in self.widgets_dicts]

        # 反序列化 record_lookup
        self._record_lookup = []
        for rd in self.record_lookup_dicts:
            self._record_lookup.append(FitRecord.from_dict(rd) if rd else None)

        # 反序列化 fit_time_lookup
        self._fit_time_lookup_dt = []
        for ft in self.fit_time_lookup:
            if ft is not None:
                self._fit_time_lookup_dt.append(
                    datetime.fromisoformat(ft) if isinstance(ft, str) else ft
                )
            else:
                self._fit_time_lookup_dt.append(None)

        self._t0 = time.perf_counter()
        self._log("info", f"[Overlay] 初始化完成, overlay_only={self.overlay_only}")

    def tick(self) -> bool:
        """处理一帧：读 bg → 渲染 overlay → 合成 → 写 out_buf

        Returns:
            True: 成功处理一帧
            False: 所有帧已处理完或取消
        """
        if self._done:
            return False

        if self._is_cancelled():
            self._done = True
            return False

        # 获取帧元数据
        try:
            msg = self.frame_meta_queue.get(timeout=2.0)
        except:
            if self._decode_done:
                self._done = True
                return False
            return True  # 继续等待

        if msg is None:
            self._decode_done = True
            self._done = True
            return False

        bg_slot_idx, frame_idx = msg

        # ── 等待背景帧就绪（仅 composite 模式）──
        bg_buf = self.bg_buf
        if bg_buf is not None:
            if not bg_buf.wait_slot(bg_slot_idx, SlotState.FILLED, timeout=30):
                self._log("warning", f"[Overlay] 等待 bg 槽 {bg_slot_idx} 超时（Decode 进程可能已退出）")
                self._done = True
                return False

        # ── 渲染 overlay ──
        from services.frame_renderer import FrameRenderer

        fit_time = (self._fit_time_lookup_dt[frame_idx]
                    if frame_idx < len(self._fit_time_lookup_dt) else None)
        overlay_img = FrameRenderer.render_frame(
            fit_data=self._fit_data,
            fit_time=fit_time,
            widgets=self._widgets,
            canvas_width=self.canvas_width,
            canvas_height=self.canvas_height,
            global_style=self.global_style,
        )

        # ── 合成 ──
        if self.overlay_only:
            composite = overlay_img
        else:
            bg_view = bg_buf.get_frame_view(bg_slot_idx)
            # 先复制出独立数组，避免 PIL Image 间接持有 SharedMemory buffer。
            bg_img = Image.fromarray(np.array(bg_view, copy=True), "RGBA")
            del bg_view  # 释放 numpy view
            bg_img.alpha_composite(overlay_img)
            composite = bg_img.convert("RGB")

        # 释放背景帧槽（仅 composite 模式）
        if bg_buf is not None:
            bg_buf.set_state(bg_slot_idx, SlotState.EMPTY)

        # ── 写入输出缓冲区 ──
        out_buf = self.out_buf
        if not out_buf.wait_slot(self._out_slot_idx, SlotState.EMPTY, timeout=30):
            self._log("warning", f"[Overlay] 等待 out 槽 {self._out_slot_idx} 超时（Encode 进程可能已退出）")
            self._done = True
            return False

        out_view = out_buf.get_frame_view(self._out_slot_idx)
        np.copyto(out_view, np.array(composite, dtype=np.uint8))
        del out_view  # 释放 numpy view

        out_buf.set_state(self._out_slot_idx, SlotState.FILLED)

        # 通知编码服务
        self.encode_meta_queue.put((self._out_slot_idx, frame_idx))

        self._overlay_count += 1
        # 更新共享进度计数器
        if self.overlay_progress is not None:
            self.overlay_progress.value = self._overlay_count
        self._out_slot_idx = (self._out_slot_idx + 1) % out_buf.n_slots

        if self._overlay_count % 50 == 0:
            elapsed = time.perf_counter() - self._t0
            self._log("progress",
                f"[Overlay] {self._overlay_count} 帧, "
                f"{self._overlay_count/elapsed:.1f} fps")

        return True

    def finish(self):
        """发送终止信号"""
        self.encode_meta_queue.put(None)
        elapsed = time.perf_counter() - self._t0 if self._t0 else 0
        if self._overlay_count > 0 and elapsed > 0:
            self._log("info", f"[Overlay] 完成: {self._overlay_count} 帧, {elapsed:.1f}s, "
                               f"{self._overlay_count/elapsed:.1f} fps")
        self._done = True

    def cleanup(self):
        """关闭共享内存"""
        if self.bg_buf is not None:
            try:
                self.bg_buf.close()
            except:
                pass
        if self.out_buf is not None:
            try:
                self.out_buf.close()
            except:
                pass

    def _handle_error(self):
        """OverlayService 特定的错误处理"""
        try:
            self.encode_meta_queue.put(None)
            if self.out_buf is not None:
                self.out_buf.signal_done()
        except:
            pass


# ══════════════════════════════════════════════════════════
#  EncodeService：PyAV 编码输出
# ══════════════════════════════════════════════════════════

class EncodeService(StandaloneService):
    """编码服务：从 out_buf 读取合成帧 → PyAV 编码 → mux 输出

    使用方式：
      方式 1（子进程）：
        svc = EncodeService(...)
        proc = mp.Process(target=svc.run_loop, name="EncodeService")
        proc.start()

      方式 2（主线程调试）：
        svc = EncodeService(...)
        svc.init()
        while svc.tick():
            ...
        svc.cleanup()
    """

    def __init__(
        self,
        output_path: str,
        canvas_width: int,
        canvas_height: int,
        fps: float,
        out_channels: int,
        overlay_only: bool,
        overlay_codec: str,
        out_buf: SharedFrameBuffer,
        encode_meta_queue,
        log_fn=None,
        cancel_check=None,
        total_frames: int = 0,
        codec: str = "libx264",
        preset: str = "fast",
        crf: int = 23,
        encode_progress=None,
    ):
        super().__init__(log_fn=log_fn, cancel_check=cancel_check)
        self.output_path = output_path
        self.canvas_width = canvas_width
        self.canvas_height = canvas_height
        self.fps = fps
        self.out_channels = out_channels
        self.overlay_only = overlay_only
        self.overlay_codec = overlay_codec
        self.out_buf = out_buf
        self.encode_meta_queue = encode_meta_queue
        self.total_frames = total_frames
        self.codec = codec
        self.preset = preset
        self.crf = crf
        self.encode_progress = encode_progress

        # 内部状态
        self._container = None
        self._v_out = None
        self._out_codec = None
        self._pix_fmt = None
        self._actual_output_path = None
        self._encode_count = 0
        self._overlay_done = False
        self._t0 = 0.0
        self._done = False

    def init(self):
        """初始化：确定编码参数并创建输出容器"""
        # 确定编码参数
        output_path = self.output_path
        if self.overlay_only:
            if self.overlay_codec == "libvpx-vp9":
                self._out_codec = "libvpx-vp9"
                self._pix_fmt = "yuva420p"
                if not output_path.lower().endswith(".webm"):
                    base, _ = os.path.splitext(output_path)
                    output_path = base + ".webm"
                codec_opts = {"crf": str(self.crf), "b:v": "0"}
            else:
                self._out_codec = "qtrle"
                self._pix_fmt = "argb"
                if not output_path.lower().endswith(".mov"):
                    base, _ = os.path.splitext(output_path)
                    output_path = base + ".mov"
                codec_opts = {}
        else:
            self._out_codec = self.codec
            # 根据编码器选择参数
            if self.codec in ("h264_nvenc", "hevc_nvenc"):
                self._pix_fmt = "yuv420p"
                codec_opts = {"preset": self.preset, "cq": str(self.crf)}
            elif self.codec in ("h264_amf", "hevc_amf"):
                self._pix_fmt = "yuv420p"
                codec_opts = {"quality": self.preset, "rc": "cqp",
                              "qp_i": str(self.crf), "qp_p": str(self.crf),
                              "qp_b": str(self.crf)}
            elif self.codec == "libvpx-vp9":
                self._pix_fmt = "yuv420p"
                if not output_path.lower().endswith(".webm"):
                    base, _ = os.path.splitext(output_path)
                    output_path = base + ".webm"
                codec_opts = {"crf": str(self.crf), "b:v": "0",
                              "cpu-used": self.preset}
            elif self.codec == "libaom-av1":
                self._pix_fmt = "yuv420p"
                codec_opts = {"crf": str(self.crf), "cpu-used": self.preset}
            elif self.codec == "libx265":
                self._pix_fmt = "yuv420p"
                codec_opts = {"preset": self.preset, "crf": str(self.crf)}
            else:
                # libx264 默认
                self._pix_fmt = "yuv420p"
                codec_opts = {"preset": self.preset, "crf": str(self.crf)}

        self._actual_output_path = output_path

        # 创建输出容器
        self._container = av.open(output_path, "w")
        self._v_out = self._container.add_stream(
            self._out_codec, Fraction(self.fps).limit_denominator(100000))
        self._v_out.width = self.canvas_width
        self._v_out.height = self.canvas_height
        self._v_out.pix_fmt = self._pix_fmt
        if codec_opts:
            self._v_out.options = codec_opts

        self._t0 = time.perf_counter()
        self._log("info", f"[Encode] 初始化完成, 输出: {os.path.basename(output_path)}")

    def tick(self) -> bool:
        """编码一帧：从 out_buf 读取 → PyAV 编码 → mux

        Returns:
            True: 成功编码一帧
            False: 所有帧已编码完或取消
        """
        if self._done:
            return False

        if self._is_cancelled():
            self._done = True
            return False

        # 获取帧元数据
        try:
            msg = self.encode_meta_queue.get(timeout=2.0)
        except:
            if self._overlay_done:
                self._done = True
                return False
            return True  # 继续等待

        if msg is None:
            self._overlay_done = True
            self._done = True
            return False

        buf_slot_idx, frame_idx = msg

        # 等待帧就绪
        if not self.out_buf.wait_slot(buf_slot_idx, SlotState.FILLED, timeout=30):
            self._log("warning", f"[Encode] 等待 out 槽 {buf_slot_idx} 超时（Overlay 进程可能已退出）")
            self._done = True
            return False

        # 从共享内存读取并编码
        view = self.out_buf.get_frame_view(buf_slot_idx)

        if self.out_channels == 4 and self.overlay_only:
            # RGBA → argb/yuva420p
            arr = np.array(view, dtype=np.uint8)
            out_frame = av.VideoFrame.from_ndarray(arr, format="rgba")
            if self._pix_fmt == "argb":
                out_frame = out_frame.reformat(format="argb")
            elif self._pix_fmt == "yuva420p":
                out_frame = out_frame.reformat(format="yuva420p")
        else:
            # RGB → yuv420p
            arr = np.array(view, dtype=np.uint8)
            out_frame = av.VideoFrame.from_ndarray(arr, format="rgb24")
            if self._pix_fmt != "rgb24":
                out_frame = out_frame.reformat(format=self._pix_fmt)

        del view  # 释放 numpy view
        del arr

        for packet in self._v_out.encode(out_frame):
            self._container.mux(packet)
        del out_frame

        # 释放输出帧槽
        self.out_buf.set_state(buf_slot_idx, SlotState.EMPTY)

        self._encode_count += 1
        # 更新共享进度计数器
        if self.encode_progress is not None:
            self.encode_progress.value = self._encode_count

        if self._encode_count % 50 == 0:
            elapsed = time.perf_counter() - self._t0
            self._log("progress",
                f"[Encode] {self._encode_count}/{self.total_frames} 帧, "
                f"{self._encode_count/elapsed:.1f} fps")

        return True

    def finish(self):
        """flush 编码器"""
        if self._v_out is not None:
            for packet in self._v_out.encode():
                self._container.mux(packet)

        elapsed = time.perf_counter() - self._t0 if self._t0 else 0
        if self._encode_count > 0 and elapsed > 0:
            self._log("info", f"[Encode] 完成: {self._encode_count} 帧, {elapsed:.1f}s, "
                               f"{self._encode_count/elapsed:.1f} fps, 输出: {self._actual_output_path}")
        self._done = True

    def cleanup(self):
        """关闭输出容器和共享内存"""
        if self._container is not None:
            try:
                self._container.close()
            except:
                pass
            self._container = None
        if self.out_buf is not None:
            try:
                self.out_buf.close()
            except:
                pass

    @property
    def actual_output_path(self) -> str:
        """编码完成后获取实际输出路径（可能因扩展名修正而不同）"""
        return self._actual_output_path or self.output_path

    def _handle_error(self):
        """EncodeService 特定的错误处理"""
        try:
            if self.out_buf is not None:
                self.out_buf.signal_done()
        except:
            pass


# ══════════════════════════════════════════════════════════
#  日志转发器：从子进程日志队列转发到主进程 RenderPipeline
# ══════════════════════════════════════════════════════════

class LogForwarder:
    """从子进程的 log_queue 读取日志并转发到 RenderPipeline.add_log()"""

    def __init__(self, log_queue: mp.Queue, add_log_fn, poll_interval: float = 0.1):
        self.log_queue = log_queue
        self.add_log_fn = add_log_fn
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _run(self):
        while not self._stop.is_set():
            try:
                level, msg = self.log_queue.get(timeout=self.poll_interval)
                self.add_log_fn(msg, level)
            except:
                pass


# ══════════════════════════════════════════════════════════
#  向后兼容：函数式入口（内部委托给类）
# ══════════════════════════════════════════════════════════

def decode_service_main(
    video_path, start_sec, end_sec, fps,
    canvas_width, canvas_height,
    bg_buf_name, bg_buf_n_slots,
    frame_meta_queue, log_queue, cancel_event,
    rotation=0, hwaccel_decode=False,
):
    """向后兼容：子进程入口函数，内部创建 DecodeService 实例"""
    bg_buf = None
    if bg_buf_name:
        bg_buf = SharedFrameBuffer(bg_buf_name, canvas_width, canvas_height,
                                    channels=4, n_slots=bg_buf_n_slots)
    svc = DecodeService(
        video_path=video_path,
        start_sec=start_sec,
        end_sec=end_sec,
        fps=fps,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        bg_buf=bg_buf,
        frame_meta_queue=frame_meta_queue,
        log_fn=log_queue,
        cancel_check=cancel_event,
        rotation=rotation,
        hwaccel_decode=hwaccel_decode,
    )
    # 子进程中 bg_buf 由 service cleanup 关闭
    svc.run_loop()
    if bg_buf is not None:
        try:
            bg_buf.unlink()
        except:
            pass


def overlay_service_main(
    fit_data_dict, fit_time_lookup, record_lookup_dicts,
    widgets_dicts, canvas_width, canvas_height,
    overlay_only,
    bg_buf_name, bg_buf_n_slots,
    out_buf_name, out_channels, out_buf_n_slots,
    frame_meta_queue, encode_meta_queue,
    log_queue, cancel_event, num_workers,
    overlay_progress=None,
):
    """向后兼容：子进程入口函数，内部创建 OverlayService 实例"""
    bg_buf = None
    if bg_buf_name:
        bg_buf = SharedFrameBuffer(bg_buf_name, canvas_width, canvas_height,
                                    channels=4, n_slots=bg_buf_n_slots)
    out_buf = SharedFrameBuffer(out_buf_name, canvas_width, canvas_height,
                                 channels=out_channels, n_slots=out_buf_n_slots)
    svc = OverlayService(
        fit_data_dict=fit_data_dict,
        fit_time_lookup=fit_time_lookup,
        record_lookup_dicts=record_lookup_dicts,
        widgets_dicts=widgets_dicts,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        overlay_only=overlay_only,
        bg_buf=bg_buf,
        out_buf=out_buf,
        frame_meta_queue=frame_meta_queue,
        encode_meta_queue=encode_meta_queue,
        log_fn=log_queue,
        cancel_check=cancel_event,
        num_workers=num_workers,
        overlay_progress=overlay_progress,
    )
    svc.run_loop()


def encode_service_main(
    output_path, canvas_width, canvas_height, fps,
    out_channels, codec, preset, crf,
    overlay_only, overlay_codec,
    out_buf_name, out_buf_n_slots,
    encode_meta_queue, log_queue, cancel_event,
    total_frames, encode_progress=None,
):
    """向后兼容：子进程入口函数，内部创建 EncodeService 实例"""
    out_buf = SharedFrameBuffer(out_buf_name, canvas_width, canvas_height,
                                 channels=out_channels, n_slots=out_buf_n_slots)
    svc = EncodeService(
        output_path=output_path,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        fps=fps,
        out_channels=out_channels,
        overlay_only=overlay_only,
        overlay_codec=overlay_codec,
        out_buf=out_buf,
        encode_meta_queue=encode_meta_queue,
        log_fn=log_queue,
        cancel_check=cancel_event,
        total_frames=total_frames,
        codec=codec,
        preset=preset,
        crf=crf,
        encode_progress=encode_progress,
    )
    svc.run_loop()
