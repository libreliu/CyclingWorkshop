"""视频配置模型"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class VideoInfo:
    """视频文件元数据"""
    file_path: str = ""
    duration: float = 0.0          # 秒
    width: int = 0
    height: int = 0
    fps: float = 0.0
    codec: str = ""
    bitrate: int = 0
    frame_count: int = 0
    file_mtime: Optional[datetime] = None  # 文件修改时间
    rotation: int = 0  # 视频旋转角度（0, 90, 180, 270, -90, -180）

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "duration": self.duration,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "codec": self.codec,
            "bitrate": self.bitrate,
            "frame_count": self.frame_count,
            "file_mtime": self.file_mtime.isoformat() if self.file_mtime else None,
            "rotation": self.rotation,
        }


@dataclass
class TimeSyncConfig:
    """时间同步配置
    
    时间映射逻辑：
      FIT 绝对时间 = video_start_time + video_elapsed * time_scale + offset_seconds
    
    - video_start_time: 视频录制起始的绝对时刻（手动模式由用户指定，自动模式从文件推断）
    - offset_seconds: 微调偏移（秒），正值=FIT 时间延后
    - time_scale: 时间缩放（1.0=正常，30.0=30x延时摄影）
    - fit_start_time: FIT 数据的起始绝对时间（由 FIT 文件解析得到，用于自动推断偏移和关键帧对齐）
    """
    video_start_time: Optional[datetime] = None
    fit_start_time: Optional[datetime] = None
    offset_seconds: float = 0.0    # 微调偏移（秒）
    time_scale: float = 1.0        # 时间缩放（1.0=正常，30.0=30x延时）

    def fit_time_at_video_frame(self, frame_index: int, fps: float) -> Optional[datetime]:
        """计算视频第 frame_index 帧对应的 FIT 绝对时间"""
        video_elapsed = frame_index / fps
        return self.fit_time_at_video_seconds(video_elapsed)

    def fit_time_at_video_seconds(self, video_seconds: float) -> Optional[datetime]:
        """计算视频第 video_seconds 秒对应的 FIT 绝对时间
        
        公式：video_start_time + video_elapsed * time_scale + offset_seconds
        返回的是绝对时间，可直接与 FIT 记录的 timestamp 比较
        """
        if self.video_start_time is None:
            return None
        from datetime import timedelta
        return self.video_start_time + timedelta(
            seconds=video_seconds * self.time_scale + self.offset_seconds
        )

    def to_dict(self) -> dict:
        return {
            "video_start_time": self.video_start_time.isoformat() if self.video_start_time else None,
            "fit_start_time": self.fit_start_time.isoformat() if self.fit_start_time else None,
            "offset_seconds": self.offset_seconds,
            "time_scale": self.time_scale,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TimeSyncConfig":
        return cls(
            video_start_time=datetime.fromisoformat(d["video_start_time"]) if d.get("video_start_time") else None,
            fit_start_time=datetime.fromisoformat(d["fit_start_time"]) if d.get("fit_start_time") else None,
            offset_seconds=d.get("offset_seconds", 0.0),
            time_scale=d.get("time_scale", 1.0),
        )


@dataclass
class VideoConfig:
    """项目视频配置"""
    video_info: Optional[VideoInfo] = None
    time_sync: TimeSyncConfig = None
    output_path: str = ""

    def __post_init__(self):
        if self.time_sync is None:
            self.time_sync = TimeSyncConfig()

    def to_dict(self) -> dict:
        return {
            "video_info": self.video_info.to_dict() if self.video_info else None,
            "time_sync": self.time_sync.to_dict(),
            "output_path": self.output_path,
        }
