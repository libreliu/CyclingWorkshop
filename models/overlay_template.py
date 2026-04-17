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

def _cycling_industrial(w: int, h: int) -> list[WidgetConfig]:
    """骑行工业风模板（新布局）

    布局：
      左上角 — 跟随模式圆形地图（高度 20%，zoom=16，follow_scale=2）
      地图下方 — 纵向堆叠：海拔 / 速度 / 心率
      左下角 — 坡度指示器 + AltitudeChart（follow 跟随剖面）
      字体：industrial 64 号
    """
    margin = 20
    gap = 8
    font_size = 96
    gauge_h = int(font_size * 1.8)

    # ── 左上角跟随地图 ──
    map_size = int(h * 0.30)         # 高度 30%，正方形
    widgets = []

    map_widget = WidgetConfig(
        widget_type="MapTrack",
        x=margin, y=margin,
        width=map_size, height=map_size,
        data_field="track",
        style={
            "tile_source": "osm",
            "map_mode": "follow",
            "follow_zoom": 16,
            "follow_scale": 2.0,
            "track_color": "#00d4aa",
            "marker_color": "#ff4444",
            "marker_size": 8,
            "map_shape": "circle",
            "border_width": 4,
            "border_color": "#b4b8a8",
            "border_glow": 10,
            "track_width": 2,
            "walked_width": 3,
        },
    )
    widgets.append(map_widget)

    # ── 地图下方纵向仪表：海拔 / 速度 / 心率 ──
    x_extra_margin = int(map_size * 0.05)
    col_w = map_size            # 与地图同宽
    y = margin + map_size + gap

    def _industrial_gauge(wtype, field, label, unit, color, y_pos, max_val=100):
        return WidgetConfig(
            widget_type=wtype, x=margin + x_extra_margin, y=y_pos,
            width=col_w - x_extra_margin, height=gauge_h,
            data_field=field,
            style={
                "color": color, "font_size": font_size, "unit": unit,
                "format": "number", "font_family": "industrial",
                "layout": "stacked", "label": label,
                "max_val": max_val, "bg_color": "#00000088",
                "border_radius": 4,
                "unit_offset_x": 190,
                "unit_offset_y": -20,
                "text_align": "left",
            },
        )

    widgets.append(_industrial_gauge("ElevationGauge", "altitude", "ALTITUDE", "m", "#aa88ff", y, 3000))
    y += gauge_h + gap
    widgets.append(_industrial_gauge("SpeedGauge", "speed", "SPEED", "km/h", "#00d4aa", y, 80))
    y += gauge_h + gap
    widgets.append(_industrial_gauge("HeartRateGauge", "heart_rate", "HEART RATE", "bpm", "#ff4444", y, 200))
    y += gauge_h + gap
    widgets.append(_industrial_gauge("GradientIndicator", "gradient", "GRADE", "%", "#ffaa00", y, 200))
    y += gauge_h + gap

    return widgets


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
    "cycling_industrial": OverlayTemplate(
        "骑行工业风", "左侧纵向堆叠仪表 + Bebas Neue 粗体棱角风格 + 圆形跟随地图", _cycling_industrial),
}
