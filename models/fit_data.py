"""FIT 数据模型"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class FitRecord:
    """FIT 单条记录（逐秒）"""
    timestamp: Optional[datetime] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[float] = None
    heart_rate: Optional[int] = None
    cadence: Optional[int] = None
    speed: Optional[float] = None      # m/s
    distance: Optional[float] = None    # m
    power: Optional[int] = None         # watts
    temperature: Optional[float] = None # ℃
    gradient: Optional[float] = None    # %，解析阶段预计算/平滑后的坡度

    def to_dict(self) -> dict:
        d = {
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }
        for k in ("latitude", "longitude", "altitude", "heart_rate",
                   "cadence", "speed", "distance", "power", "temperature",
                   "gradient"):
            v = getattr(self, k)
            d[k] = v
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "FitRecord":
        from datetime import datetime
        ts = d.get("timestamp")
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        return cls(
            timestamp=ts,
            latitude=d.get("latitude"),
            longitude=d.get("longitude"),
            altitude=d.get("altitude"),
            heart_rate=d.get("heart_rate"),
            cadence=d.get("cadence"),
            speed=d.get("speed"),
            distance=d.get("distance"),
            power=d.get("power"),
            temperature=d.get("temperature"),
            gradient=d.get("gradient"),
        )

    def get_field(self, name: str):
        """安全获取字段值"""
        return getattr(self, name, None)


@dataclass
class FitSession:
    """FIT 会话摘要"""
    sport: str = ""
    sub_sport: str = ""
    start_time: Optional[datetime] = None
    total_distance: float = 0.0       # m
    total_elapsed_time: float = 0.0   # s
    total_timer_time: float = 0.0     # s
    total_ascent: float = 0.0         # m
    total_descent: float = 0.0        # m
    avg_heart_rate: Optional[int] = None
    max_heart_rate: Optional[int] = None
    avg_speed: Optional[float] = None  # m/s
    max_speed: Optional[float] = None  # m/s
    avg_power: Optional[float] = None  # watts
    max_power: Optional[int] = None    # watts
    avg_cadence: Optional[float] = None
    max_cadence: Optional[int] = None
    records: list = field(default_factory=list)  # list[FitRecord]

    def to_dict(self, include_records: bool = False) -> dict:
        return {
            "sport": self.sport,
            "sub_sport": self.sub_sport,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "total_distance": self.total_distance,
            "total_elapsed_time": self.total_elapsed_time,
            "total_timer_time": self.total_timer_time,
            "total_ascent": self.total_ascent,
            "total_descent": self.total_descent,
            "avg_heart_rate": self.avg_heart_rate,
            "max_heart_rate": self.max_heart_rate,
            "avg_speed": self.avg_speed,
            "max_speed": self.max_speed,
            "avg_power": self.avg_power,
            "max_power": self.max_power,
            "avg_cadence": self.avg_cadence,
            "max_cadence": self.max_cadence,
            "record_count": len(self.records),
            **({"records": [r.to_dict() for r in self.records]} if include_records else {}),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FitSession":
        from datetime import datetime
        st = d.get("start_time")
        if isinstance(st, str):
            st = datetime.fromisoformat(st)
        records_data = d.get("records", [])
        return cls(
            sport=d.get("sport", ""),
            sub_sport=d.get("sub_sport", ""),
            start_time=st,
            total_distance=d.get("total_distance", 0.0),
            total_elapsed_time=d.get("total_elapsed_time", 0.0),
            total_timer_time=d.get("total_timer_time", 0.0),
            total_ascent=d.get("total_ascent", 0.0),
            total_descent=d.get("total_descent", 0.0),
            avg_heart_rate=d.get("avg_heart_rate"),
            max_heart_rate=d.get("max_heart_rate"),
            avg_speed=d.get("avg_speed"),
            max_speed=d.get("max_speed"),
            avg_power=d.get("avg_power"),
            max_power=d.get("max_power"),
            avg_cadence=d.get("avg_cadence"),
            max_cadence=d.get("max_cadence"),
            records=[FitRecord.from_dict(r) for r in records_data],
        )


@dataclass
class FitData:
    """FIT 文件完整解析结果"""
    file_path: str = ""
    sessions: list = field(default_factory=list)  # list[FitSession]
    lap_markers: list = field(default_factory=list)  # list[datetime]
    available_fields: list = field(default_factory=list)  # 可用数据字段名
    haversine_total_distance: float = 0.0  # GPS haversine 全路径积分总距离 (m)，辅助信息
    _glitch_cache: dict = field(default=None, repr=False)  # GPS glitch 缓存
    _track_coords_cache: tuple = field(default=None, repr=False)  # 轨迹坐标缓存 (cache_key, coords)

    def get_glitch_cache(self, max_speed_ms: float = 55.0) -> dict:
        """获取或计算 GPS glitch 缓存"""
        cache_key = f"ms{max_speed_ms}"
        if self._glitch_cache is None:
            self._glitch_cache = {}
        if cache_key not in self._glitch_cache:
            from services.fit_parser import FitSanitize
            self._glitch_cache[cache_key] = FitSanitize.detect_gps_glitches(self, max_speed_ms=max_speed_ms)
        return self._glitch_cache[cache_key]

    def invalidate_glitch_cache(self):
        """使 GPS glitch 缓存失效（数据变更后调用）

        同时清除轨迹坐标缓存，因为 glitch 过滤会影响轨迹。
        """
        self._glitch_cache = None
        self._track_coords_cache = None

    @property
    def primary_session(self) -> Optional[FitSession]:
        return self.sessions[0] if self.sessions else None

    def to_dict(self, include_records: bool = False) -> dict:
        return {
            "file_path": self.file_path,
            "sessions": [s.to_dict(include_records=include_records) for s in self.sessions],
            "available_fields": self.available_fields,
            "lap_markers": [t.isoformat() for t in self.lap_markers],
            "haversine_total_distance": self.haversine_total_distance,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FitData":
        from datetime import datetime
        sessions_data = d.get("sessions", [])
        lap_data = d.get("lap_markers", [])
        return cls(
            file_path=d.get("file_path", ""),
            sessions=[FitSession.from_dict(s) for s in sessions_data],
            lap_markers=[datetime.fromisoformat(t) for t in lap_data if t],
            available_fields=d.get("available_fields", []),
            haversine_total_distance=d.get("haversine_total_distance", 0.0),
        )


# ── 数据清洗配置 ──────────────────────────────────────

@dataclass
class SanitizeConfig:
    """数据清洗配置：决定哪些记录应被丢弃

    清洗是原地变换的第一步——不满足条件的记录直接移除。
    """
    # GPS 清洗
    gps_filter_glitches: bool = True       # 是否过滤 GPS glitch 点
    gps_max_speed_ms: float = 55.0         # GPS 速度阈值 (m/s)，≈200 km/h
    gps_out_of_range: bool = True          # 是否移除坐标范围外的点

    # 心率清洗
    hr_range: tuple = (30, 250)            # 心率物理合理范围 (bpm)
    hr_max_rate: float = 30.0              # 1s 内心率最大变化率 (bpm/s)
    hr_enable_rate_check: bool = True      # 是否启用心率变化率检测

    # 速度清洗
    speed_range: tuple = (0, 55)           # 速度物理合理范围 (m/s)
    speed_max_accel: float = 10.0          # 1s 内速度最大加速度 (m/s²)，≈36 km/h/s
    speed_enable_accel_check: bool = True  # 是否启用速度加速度检测

    # 海拔清洗
    altitude_range: tuple = (-500, 9000)   # 海拔物理合理范围 (m)

    # 踏频/功率/温度等（仅范围检查）
    cadence_range: tuple = (0, 250)
    power_range: tuple = (0, 2500)
    temperature_range: tuple = (-40, 60)

    def to_dict(self) -> dict:
        return {
            "gps_filter_glitches": self.gps_filter_glitches,
            "gps_max_speed_ms": self.gps_max_speed_ms,
            "gps_out_of_range": self.gps_out_of_range,
            "hr_range": list(self.hr_range),
            "hr_max_rate": self.hr_max_rate,
            "hr_enable_rate_check": self.hr_enable_rate_check,
            "speed_range": list(self.speed_range),
            "speed_max_accel": self.speed_max_accel,
            "speed_enable_accel_check": self.speed_enable_accel_check,
            "altitude_range": list(self.altitude_range),
            "cadence_range": list(self.cadence_range),
            "power_range": list(self.power_range),
            "temperature_range": list(self.temperature_range),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SanitizeConfig":
        if not d:
            return cls()
        return cls(
            gps_filter_glitches=d.get("gps_filter_glitches", True),
            gps_max_speed_ms=d.get("gps_max_speed_ms", 55.0),
            gps_out_of_range=d.get("gps_out_of_range", True),
            hr_range=tuple(d.get("hr_range", [30, 250])),
            hr_max_rate=d.get("hr_max_rate", 30.0),
            hr_enable_rate_check=d.get("hr_enable_rate_check", True),
            speed_range=tuple(d.get("speed_range", [0, 55])),
            speed_max_accel=d.get("speed_max_accel", 10.0),
            speed_enable_accel_check=d.get("speed_enable_accel_check", True),
            altitude_range=tuple(d.get("altitude_range", [-500, 9000])),
            cadence_range=tuple(d.get("cadence_range", [0, 250])),
            power_range=tuple(d.get("power_range", [0, 2500])),
            temperature_range=tuple(d.get("temperature_range", [-40, 60])),
        )


@dataclass
class SmoothingConfig:
    """平滑滤波配置：窗函数平滑（可选的第二步原地变换）"""
    enabled: bool = False
    fields: list = field(default_factory=list)  # 要平滑的字段列表
    method: str = "moving_avg"     # "median" | "moving_avg" | "gaussian"
    window_size: int = 5           # 窗口大小（奇数）

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "fields": self.fields,
            "method": self.method,
            "window_size": self.window_size,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SmoothingConfig":
        if not d:
            return cls()
        return cls(
            enabled=d.get("enabled", False),
            fields=d.get("fields", []),
            method=d.get("method", "moving_avg"),
            window_size=d.get("window_size", 5),
        )
