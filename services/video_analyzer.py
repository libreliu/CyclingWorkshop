"""视频分析服务

帧提取：PyAV（Python 侧旋转处理，方向可靠）
视频元数据：subprocess ffprobe
"""
import io
import json
import os
import subprocess
from datetime import datetime
from typing import Optional

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
    def extract_frame(file_path: str, timestamp_sec: float, timeout: int = 10,
                      rotation: int = 0) -> Optional[bytes]:
        """提取视频指定时间点的帧为 JPEG

        Args:
            file_path: 视频文件路径
            timestamp_sec: 提取时间点（秒）
            timeout: 超时时间
            rotation: 视频旋转角度（0/90/180/270/-90/-180/-270），
                      PyAV 自动应用旋转 metadata，此参数作为备用
        """
        return VideoAnalyzerService._extract_frame_pyav(
            file_path, timestamp_sec, rotation
        )

    @staticmethod
    def _apply_rotation(img, rotation: int):
        """对 PIL Image 应用旋转（90° 增量）"""
        if rotation in (180, -180):
            return img.transpose(2)  # ROTATE_180
        elif rotation in (90, -270):
            return img.transpose(7)  # ROTATE_90  (逆时针90° → 顺时针补正)
        elif rotation in (270, -90):
            return img.transpose(6)  # ROTATE_270 (顺时针90° → 逆时针补正)
        return img

    @staticmethod
    def _extract_frame_pyav(file_path: str, timestamp_sec: float,
                            rotation: int = 0) -> Optional[bytes]:
        """使用 PyAV 提取视频帧（自动应用旋转 metadata）"""
        if not os.path.isfile(file_path):
            return None

        try:
            import av

            container = av.open(file_path)
            stream = container.streams.video[0]

            # PyAV (libav) 默认 auto-rotate，会自动应用 rotation metadata
            stream.thread_type = "AUTO"

            # seek 到目标时间
            # stream.time_base 是 fractions.Fraction，如 1/30000
            # seek 需要流时间基下的时间戳
            target_ts = int(timestamp_sec / float(stream.time_base))
            container.seek(target_ts, stream=stream)

            # 获取下一帧（seek 后可能需要跳过几帧才能对齐）
            frame = None
            for f in container.decode(stream):
                frame = f
                break

            if frame is None:
                container.close()
                return None

            # 转为 PIL Image（PyAV 已自动旋转）
            img = frame.to_image()

            # 转为 JPEG bytes
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90)
            frame_bytes = buf.getvalue()

            container.close()
            return frame_bytes if len(frame_bytes) > 100 else None

        except Exception as e:
            print(f"[VideoAnalyzer] PyAV 帧提取失败: {e}")
            # 回退到 subprocess 方式
            return VideoAnalyzerService._extract_frame_subprocess(
                file_path, timestamp_sec, 15, rotation
            )

    @staticmethod
    def _build_rotation_filter(rotation: int) -> str:
        """根据旋转角度构建 ffmpeg transpose 滤镜字符串"""
        if rotation in (180, -180):
            return "hflip,vflip"
        elif rotation in (90, -270):
            return "transpose=1"
        elif rotation in (270, -90):
            return "transpose=2"
        return ""

    @staticmethod
    def _extract_frame_subprocess(file_path: str, timestamp_sec: float, timeout: int = 10,
                                  rotation: int = 0) -> Optional[bytes]:
        """提取视频帧（python-ffmpeg 构建命令 + subprocess 执行）— 作为 PyAV 的回退"""
        if not os.path.isfile(file_path):
            return None

        try:
            from ffmpeg import FFmpeg as FFmpegBuilder

            # 构建旋转滤镜
            rotation_filter = VideoAnalyzerService._build_rotation_filter(rotation)

            if rotation_filter:
                vf = f"{rotation_filter}"
                builder = (
                    FFmpegBuilder(executable=config.FFMPEG_PATH)
                    .input(file_path, ss=str(timestamp_sec))
                    .output("pipe:1", vframes="1", **{"q:v": "2"},
                            f="image2pipe", vcodec="mjpeg", vf=vf)
                )
            else:
                builder = (
                    FFmpegBuilder(executable=config.FFMPEG_PATH)
                    .input(file_path, ss=str(timestamp_sec))
                    .output("pipe:1", vframes="1", **{"q:v": "2"},
                            f="image2pipe", vcodec="mjpeg")
                )
            cmd_args = builder.arguments

            result = subprocess.run(cmd_args, capture_output=True, timeout=timeout)
            if result.returncode != 0 or len(result.stdout) < 100:
                stderr_text = result.stderr.decode('utf-8', errors='replace')[-200:] if result.stderr else ""
                print(f"[VideoAnalyzer] 帧提取失败 (rc={result.returncode}): {stderr_text}")
                return None
            return result.stdout
        except subprocess.TimeoutExpired:
            print(f"[VideoAnalyzer] 帧提取超时 ({timeout}s): {file_path} @ {timestamp_sec}s")
            return None
        except Exception as e:
            print(f"[VideoAnalyzer] 帧提取异常: {e}")
            return None
