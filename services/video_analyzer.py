"""视频分析服务

帧提取：PyAV 解码（不自动旋转）+ Pillow 手动 transpose
视频元数据：subprocess ffprobe
"""
import io
import json
import os
import subprocess
from datetime import datetime
from typing import Optional

from PIL import Image

import config
from models.video_config import VideoInfo


class VideoAnalyzerService:
    """视频元数据分析和帧提取"""

    @staticmethod
    def analyze(file_path: str) -> Optional[VideoInfo]:
        """通过 ffprobe 分析视频元数据"""
        if not os.path.isfile(file_path):
            return None

        try:
            cmd = [
                config.FFPROBE_PATH,
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                file_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
            if result.returncode != 0:
                print(f"[VideoAnalyzer] ffprobe 失败: {result.stderr}")
                return None

            probe = json.loads(result.stdout)

            # 找视频流
            video_stream = None
            for stream in probe.get("streams", []):
                if stream.get("codec_type") == "video":
                    video_stream = stream
                    break

            if not video_stream:
                return None

            fmt = probe.get("format", {})

            # 计算 fps
            fps = 0.0
            r_frame_rate = video_stream.get("r_frame_rate", "0/1")
            if "/" in r_frame_rate:
                num, den = r_frame_rate.split("/")
                den = int(den) if int(den) != 0 else 1
                fps = int(num) / den
            avg_frame_rate = video_stream.get("avg_frame_rate", "0/1")
            if fps == 0 and "/" in avg_frame_rate:
                num, den = avg_frame_rate.split("/")
                den = int(den) if int(den) != 0 else 1
                fps = int(num) / den

            # 文件修改时间
            file_mtime = None
            try:
                mtime = os.path.getmtime(file_path)
                file_mtime = datetime.fromtimestamp(mtime)
            except Exception:
                pass

            # 读取旋转角度
            rotation = 0
            for sd in video_stream.get("side_data_list", []):
                if "rotation" in sd:
                    rotation = int(sd["rotation"])
                    break

            info = VideoInfo(
                file_path=file_path,
                duration=float(fmt.get("duration", 0)),
                width=int(video_stream.get("width", 0)),
                height=int(video_stream.get("height", 0)),
                fps=round(fps, 3),
                codec=video_stream.get("codec_name", ""),
                bitrate=int(fmt.get("bit_rate", 0)),
                frame_count=int(video_stream.get("nb_frames", 0)),
                file_mtime=file_mtime,
                rotation=rotation,
            )
            return info

        except Exception as e:
            print(f"[VideoAnalyzer] 分析失败: {e}")
            return None

    @staticmethod
    def _apply_rotation(img: "Image.Image", rotation: int) -> "Image.Image":
        """根据 rotation metadata 手动旋转 PIL Image

        PyAV/libavcodec 不会自动旋转帧（auto-rotate 是 fftools 层功能），
        需要在 Python 侧手动应用。

        Args:
            img: PIL Image
            rotation: 旋转角度（0, 90, 180, 270, -90, -180）

        Returns:
            旋转后的 PIL Image
        """
        if rotation == 0:
            return img
        elif rotation == 90:
            return img.transpose(Image.Transpose.ROTATE_270)
        elif rotation in (-90, 270):
            return img.transpose(Image.Transpose.ROTATE_90)
        elif rotation in (180, -180):
            return img.transpose(Image.Transpose.ROTATE_180)
        else:
            return img  # 未知角度不处理

    @staticmethod
    def extract_frame(file_path: str, timestamp_sec: float,
                       rotation: int = 0) -> Optional[bytes]:
        """提取视频指定时间点的帧为 JPEG

        PyAV/libavcodec 不会自动旋转帧，需要手动根据 rotation metadata 旋转。

        Args:
            file_path: 视频文件路径
            timestamp_sec: 提取时间点（秒）
            rotation: 视频旋转角度（来自 ffprobe side_data_list）
        """
        if not os.path.isfile(file_path):
            return None

        try:
            import av

            container = av.open(file_path)
            stream = container.streams.video[0]

            # PyAV 不自动旋转，帧方向与编码一致
            stream.thread_type = "AUTO"

            # seek 到目标时间
            # stream.time_base 是 fractions.Fraction，如 1/30000
            # seek 需要流时间基下的时间戳
            target_ts = int(timestamp_sec / float(stream.time_base))
            # backward=True 确保 seek 到目标之前最近的关键帧
            container.seek(target_ts, stream=stream, backward=True)

            # seek 后从关键帧开始解码，需要持续解码直到 pts >= target_ts
            # 否则只取第一帧会总是返回关键帧（GOP 间隔通常 1-2 秒）
            frame = None
            for f in container.decode(stream):
                if f.pts >= target_ts:
                    frame = f
                    break

            if frame is None:
                container.close()
                return None

            # 转为 PIL Image 并手动旋转
            img = frame.to_image()
            img = VideoAnalyzerService._apply_rotation(img, rotation)

            # 转为 JPEG bytes
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90)
            frame_bytes = buf.getvalue()

            container.close()
            return frame_bytes if len(frame_bytes) > 100 else None

        except Exception as e:
            print(f"[VideoAnalyzer] PyAV 帧提取失败: {e}")
            return None
