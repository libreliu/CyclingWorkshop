"""叠加层模板与 Widget 模型"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class WidgetConfig:
    """单个叠加 Widget 的配置"""
    widget_type: str = ""           # Widget 类型名
    x: int = 0                      # 左上角 x（px）
    y: int = 0                      # 左上角 y（px）
    width: int = 100                # 宽度（px）
    height: int = 100               # 高度（px）
    opacity: float = 1.0            # 0.0 ~ 1.0
    data_field: str = ""            # 绑定的 FIT 数据字段
    visible: bool = True
    style: dict = field(default_factory=dict)
    # MapTrack style: { map_style, zoom, show_breadcrumb, marker_color, ... }
    # Gauge style: { color, font_size, show_label, min_val, max_val, arc_color, format, ... }
    # Chart style: { line_color, fill_color, window_seconds, show_grid, ... }

    def to_dict(self) -> dict:
        return {
            "widget_type": self.widget_type,
            "x": self.x, "y": self.y,
            "width": self.width, "height": self.height,
            "opacity": self.opacity,
            "data_field": self.data_field,
            "visible": self.visible,
            "style": self.style,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "WidgetConfig":
        return cls(
            widget_type=d.get("widget_type", ""),
            x=d.get("x", 0), y=d.get("y", 0),
            width=d.get("width", 100), height=d.get("height", 100),
            opacity=d.get("opacity", 1.0),
            data_field=d.get("data_field", ""),
            visible=d.get("visible", True),
            style=d.get("style", {}),
        )


# ── 内置模板定义 ──────────────────────────────────────

def _cycling_classic(w: int, h: int) -> list[WidgetConfig]:
    """骑行经典模板"""
    map_w = min(350, w // 4)
    # 地图默认 4:3 宽高比（典型骑行轨迹横向较长），实际会由前端根据轨迹宽高比调整
    map_h = int(map_w * 3 // 4)
    gauge_w, gauge_h = 150, 80
    margin = 15
    return [
        WidgetConfig(
            widget_type="MapTrack", x=margin, y=h - map_h - margin,
            width=map_w, height=map_h,
            data_field="track",
            style={"map_style": "dark", "zoom": 14, "auto_center": True,
                   "track_color": "#00d4aa", "marker_color": "#ff4444", "marker_size": 8,
                   "auto_aspect": True},
        ),
        WidgetConfig(
            widget_type="SpeedGauge", x=w - gauge_w - margin, y=h - gauge_h * 3 - margin * 2 - 30,
            width=gauge_w, height=gauge_h,
            data_field="speed",
            style={"color": "#00d4aa", "font_size": 32, "unit": "km/h", "format": "arc",
                   "min_val": 0, "max_val": 80},
        ),
        WidgetConfig(
            widget_type="HeartRateGauge", x=w - gauge_w - margin, y=h - gauge_h * 2 - margin - 15,
            width=gauge_w, height=gauge_h,
            data_field="heart_rate",
            style={"color": "#ff4444", "font_size": 32, "unit": "bpm", "format": "arc",
                   "min_val": 40, "max_val": 200},
        ),
        WidgetConfig(
            widget_type="CadenceGauge", x=w - gauge_w - margin, y=h - gauge_h - margin,
            width=gauge_w, height=gauge_h,
            data_field="cadence",
            style={"color": "#4488ff", "font_size": 32, "unit": "rpm", "format": "arc",
                   "min_val": 0, "max_val": 150},
        ),
        WidgetConfig(
            widget_type="AltitudeChart", x=margin, y=margin,
            width=w - margin * 2, height=50,
            data_field="altitude",
            style={"line_color": "#aa88ff", "fill_color": "#aa88ff30", "show_grid": False},
        ),
    ]


def _cycling_minimal(w: int, h: int) -> list[WidgetConfig]:
    """骑行极简模板"""
    map_w = 250
    map_h = int(map_w * 3 // 4)
    return [
        WidgetConfig(
            widget_type="MapTrack", x=15, y=h - map_h - 15,
            width=map_w, height=map_h,
            data_field="track",
            style={"map_style": "dark", "zoom": 14, "auto_center": True,
                   "track_color": "#00d4aa", "marker_color": "#ff4444",
                   "auto_aspect": True},
        ),
        WidgetConfig(
            widget_type="SpeedGauge", x=w - 165, y=h - 95,
            width=150, height=80,
            data_field="speed",
            style={"color": "#00d4aa", "font_size": 36, "unit": "km/h", "format": "number"},
        ),
    ]


def _running_basic(w: int, h: int) -> list[WidgetConfig]:
    """跑步基础模板"""
    gauge_w, gauge_h = 150, 60
    total_w = gauge_w * 3 + 15 * 2
    start_x = (w - total_w) // 2
    y = h - gauge_h - 20
    return [
        WidgetConfig(
            widget_type="SpeedGauge", x=start_x, y=y,
            width=gauge_w, height=gauge_h,
            data_field="speed",
            style={"color": "#00d4aa", "font_size": 28, "unit": "min/km", "format": "number"},
        ),
        WidgetConfig(
            widget_type="HeartRateGauge", x=start_x + gauge_w + 15, y=y,
            width=gauge_w, height=gauge_h,
            data_field="heart_rate",
            style={"color": "#ff4444", "font_size": 28, "unit": "bpm", "format": "number"},
        ),
        WidgetConfig(
            widget_type="DistanceCounter", x=start_x + (gauge_w + 15) * 2, y=y,
            width=gauge_w, height=gauge_h,
            data_field="distance",
            style={"color": "#ffffff", "font_size": 28, "unit": "km", "format": "number"},
        ),
    ]


def _trail_climb(w: int, h: int) -> list[WidgetConfig]:
    """越野爬坡模板"""
    chart_h = 100
    return [
        WidgetConfig(
            widget_type="AltitudeChart", x=w - 320, y=15,
            width=305, height=chart_h,
            data_field="altitude",
            style={"line_color": "#aa88ff", "fill_color": "#aa88ff30", "show_grid": True,
                   "window_seconds": 600},
        ),
        WidgetConfig(
            widget_type="GradientIndicator", x=w - 320, y=chart_h + 25,
            width=100, height=40,
            data_field="gradient",
            style={"color": "#ffaa00", "font_size": 24, "unit": "%"},
        ),
        WidgetConfig(
            widget_type="SpeedGauge", x=w - 200, y=chart_h + 25,
            width=150, height=40,
            data_field="speed",
            style={"color": "#00d4aa", "font_size": 24, "unit": "km/h", "format": "number"},
        ),
        WidgetConfig(
            widget_type="ElevationGauge", x=15, y=h - 55,
            width=130, height=40,
            data_field="altitude",
            style={"color": "#aa88ff", "font_size": 24, "unit": "m"},
        ),
    ]


# ── 模板注册表 ────────────────────────────────────────

class OverlayTemplate:
    """叠加层模板"""
    def __init__(self, name: str, description: str, widget_factory):
        self.name = name
        self.description = description
        self._factory = widget_factory

    def create_widgets(self, canvas_width: int, canvas_height: int) -> list[WidgetConfig]:
        return self._factory(canvas_width, canvas_height)

    def to_dict(self, canvas_width: int = 1920, canvas_height: int = 1080) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "widgets": [w.to_dict() for w in self.create_widgets(canvas_width, canvas_height)],
        }


TEMPLATES = {
    "cycling_classic": OverlayTemplate(
        "骑行经典", "左下轨迹地图 + 右下速度/心率/踏频表盘 + 顶部海拔条", _cycling_classic),
    "cycling_minimal": OverlayTemplate(
        "骑行极简", "左下小地图 + 右下速度数字", _cycling_minimal),
    "running_basic": OverlayTemplate(
        "跑步基础", "中心底部速度 + 心率 + 距离", _running_basic),
    "trail_climb": OverlayTemplate(
        "越野爬坡", "右上角海拔剖面 + 坡度 + 速度", _trail_climb),
}
