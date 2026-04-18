"""帧渲染服务（Pillow）"""
from typing import Optional
from PIL import Image, ImageDraw, ImageFont
import math

import numpy as np

from models.fit_data import FitData, FitRecord
from models.overlay_template import WidgetConfig


class FrameRenderer:
    """逐帧渲染叠加层"""

    # 字体缓存：key = (family, size)
    _font_cache = {}

    # ── 字体族定义 ──
    _FONT_FAMILIES = {
        "industrial": [
            "fonts/BebasNeue-Regular.ttf",               # 嵌入的工业风窄体
            r"C:\Windows\Fonts\impact.ttf",              # Windows 内置 Ultra Bold 回退
            r"C:\Windows\Fonts\arialbd.ttf",             # Arial Bold
            r"C:\Windows\Fonts\msyhbd.ttc",              # 微软雅黑 Bold（中文回退）
        ],
        "default": [
            r"C:\Windows\Fonts\consola.ttf",
            r"C:\Windows\Fonts\arial.ttf",
            r"C:\Windows\Fonts\msyh.ttc",
        ],
    }

    @staticmethod
    def render_frame(
        fit_data: FitData,
        fit_time,
        widgets: list,
        canvas_width: int,
        canvas_height: int,
        global_style: dict = None,
    ) -> Image.Image:
        """渲染一帧叠加层，返回 RGBA Image

        global_style: 可选的全局渲染设置 dict：
          - bg_color (str): 所有 Widget 的统一背景色（含 alpha），如 "#00000066"
                           覆盖各 widget.style["bg_color"]（若 widget 未单独设置则用此值）
          - label_unit_shadow (bool): 标签/单位是否绘制文字阴影（默认 True）
        """
        from services.fit_parser import FitParserService
        from datetime import datetime

        # 解析全局样式（None 时使用空字典）
        gstyle = global_style or {}

        # 查询当前 FIT 数据
        if isinstance(fit_time, (int, float)):
            # 如果是秒数，需要转换为 datetime
            session = fit_data.primary_session
            if session and session.start_time:
                from datetime import timedelta
                fit_time = session.start_time + timedelta(seconds=fit_time)

        # 统一时区：确保 fit_time 与 FIT 数据使用相同的时区意识
        if fit_time is not None:
            session = fit_data.primary_session
            if session and session.start_time:
                if session.start_time.tzinfo is not None and fit_time.tzinfo is None:
                    fit_time = fit_time.replace(tzinfo=session.start_time.tzinfo)
                elif session.start_time.tzinfo is None and fit_time.tzinfo is not None:
                    fit_time = fit_time.replace(tzinfo=None)

        record = FitParserService.get_record_at(fit_data, fit_time) if fit_time else None

        # 创建透明画布
        canvas = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))

        for widget in widgets:
            if not widget.visible:
                continue
            FrameRenderer._render_widget(canvas, widget, record, fit_data, fit_time,
                                         global_style=gstyle)

        return canvas

    @staticmethod
    def _render_widget(canvas, widget: WidgetConfig, record: Optional[FitRecord],
                       fit_data: FitData, fit_time, global_style: dict = None):
        """渲染单个 Widget

        global_style: 全局样式设置（同 render_frame）
        """
        wtype = widget.widget_type
        gstyle = global_style or {}

        # 创建 Widget 区域
        region = Image.new("RGBA", (widget.width, widget.height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(region)

        # 半透明背景：优先 widget.style.bg_color，其次全局 global_bg_color，最后默认值
        bg_color = widget.style.get("bg_color") or gstyle.get("bg_color")
        if bg_color is None:
            bg_color = "#00000066"
        bg_rgba = FrameRenderer._parse_color(bg_color, default=(0, 0, 0, 102))
        if bg_rgba[3] > 0:
            # 圆角背景
            radius = widget.style.get("border_radius", 8)
            FrameRenderer._draw_rounded_rect(draw, (0, 0, widget.width - 1, widget.height - 1),
                                              radius, bg_rgba)

        # MapTrack 可能需要先渲染瓦片底图
        tile_bg = None
        if wtype == "MapTrack":
            tile_bg = FrameRenderer._render_map_track_bg(widget, fit_data, record)

        # 根据 Widget 类型渲染
        if wtype == "SpeedGauge":
            FrameRenderer._render_gauge(draw, widget, record, "speed",
                                        lambda v: v * 3.6 if v else 0,
                                        global_style=gstyle)  # m/s → km/h
        elif wtype == "HeartRateGauge":
            FrameRenderer._render_heart_rate(draw, widget, record,
                                             global_style=gstyle)
        elif wtype == "CadenceGauge":
            FrameRenderer._render_gauge(draw, widget, record, "cadence", lambda v: v,
                                        global_style=gstyle)
        elif wtype == "PowerGauge":
            FrameRenderer._render_gauge(draw, widget, record, "power", lambda v: v,
                                        global_style=gstyle)
        elif wtype == "ElevationGauge":
            FrameRenderer._render_gauge(draw, widget, record, "altitude", lambda v: v,
                                        global_style=gstyle)
        elif wtype == "DistanceCounter":
            FrameRenderer._render_distance(draw, widget, record, fit_data,
                                           global_style=gstyle)
        elif wtype == "TimerDisplay":
            FrameRenderer._render_timer(draw, widget, record, fit_data, fit_time,
                                        global_style=gstyle)
        elif wtype == "GradientIndicator":
            FrameRenderer._render_gradient(draw, widget, record, fit_data, fit_time,
                                           global_style=gstyle)
        elif wtype == "AltitudeChart":
            FrameRenderer._render_altitude_chart(draw, widget, record, fit_data, fit_time,
                                                 global_style=gstyle)
        elif wtype == "MapTrack":
            FrameRenderer._render_map_track(draw, widget, record, fit_data, fit_time)
        elif wtype == "CustomLabel":
            FrameRenderer._render_label(draw, widget, global_style=gstyle)

        # 如果有瓦片底图，先合成底图再叠加轨迹
        if tile_bg is not None:
            tile_bg.alpha_composite(region, (0, 0))
            region = tile_bg

        # MapTrack 形状裁剪、晕圈、边框后处理
        map_border_offset = 0
        if wtype == "MapTrack":
            region = FrameRenderer._apply_map_track_shape(region, widget)
            map_border_offset = int(widget.style.get("border_width", 0))

        # 应用透明度并合成到画布
        if widget.opacity < 1.0:
            alpha = region.split()[3]
            alpha = alpha.point(lambda p: int(p * widget.opacity))
            region.putalpha(alpha)

        # 边框扩展时向左上偏移，使边框从地图边缘向外生长
        paste_x = widget.x - map_border_offset
        paste_y = widget.y - map_border_offset
        # 防止超出画布左上边界
        src_x = max(0, -paste_x)
        src_y = max(0, -paste_y)
        dst_x = max(0, paste_x)
        dst_y = max(0, paste_y)
        if src_x > 0 or src_y > 0:
            # 裁剪超出画布的部分
            region = region.crop((src_x, src_y, region.width, region.height))
        canvas.alpha_composite(region, (dst_x, dst_y))

    # ── 心率区间颜色 ─────────────────────────────────────────────────────────────
    #
    # 经典 5 区间（基于最大心率百分比）：
    #   Z1: 50%-60%  热身区      蓝色   #4488ff
    #   Z2: 60%-70%  脂肪燃烧    绿色   #44cc44
    #   Z3: 70%-80%  有氧训练    黄色   #ffdd00
    #   Z4: 80%-90%  无氧阈值    橙色   #ff8800
    #   Z5: 90%-100% 最大强度    红色   #ff3333
    #
    # 最大心率 = 220 - 年龄（经典公式）
    # 如果心率低于 50% 最大心率（热身前），使用原始 style.color

    _HR_ZONES = [
        (0.50, 0.60, (68,  136, 255, 255)),   # Z1 蓝
        (0.60, 0.70, (68,  204,  68, 255)),   # Z2 绿
        (0.70, 0.80, (255, 221,   0, 255)),   # Z3 黄
        (0.80, 0.90, (255, 136,   0, 255)),   # Z4 橙
        (0.90, 1.00, (255,  51,  51, 255)),   # Z5 红
    ]

    @staticmethod
    def _hr_zone_color(hr: float, max_hr: float, fallback_color: tuple) -> tuple:
        """根据心率值和最大心率返回区间颜色 RGBA。
        低于 Z1 下限（50% max_hr）返回 fallback_color。
        超过 Z5 上限（100% max_hr）返回 Z5 颜色。
        """
        ratio = hr / max_hr if max_hr > 0 else 0
        if ratio >= 0.90:
            return FrameRenderer._HR_ZONES[-1][2]
        for lo, hi, color in FrameRenderer._HR_ZONES:
            if lo <= ratio < hi:
                return color
        return fallback_color  # < 50%

    @staticmethod
    def _render_heart_rate(draw, widget, record, global_style: dict = None):
        """渲染心率表盘。

        当 style.hr_zone_color=True 时，根据心率区间动态更改颜色：
          Z1 50-60% 最大心率 → 蓝色
          Z2 60-70%          → 绿色
          Z3 70-80%          → 黄色
          Z4 80-90%          → 橙色
          Z5 90-100%         → 红色

        最大心率由 style.hr_max_age（年龄）计算：max_hr = 220 - age
        也可直接用 style.hr_max 指定最大心率值（优先级高于年龄）

        低于 Z1 下限（< 50% 最大心率）时使用 style.color 原色。
        """
        style = widget.style
        hr_zone_color_enabled = style.get("hr_zone_color", False)

        # 拿到原始颜色作为 fallback
        original_color = FrameRenderer._parse_color(style.get("color", "#ff4444"), default=(255, 68, 68, 255))

        if hr_zone_color_enabled and record:
            hr_raw = getattr(record, "heart_rate", None)
            if hr_raw is not None:
                hr_val = float(hr_raw)
                # 最大心率：直接值优先，否则从年龄计算
                hr_max = style.get("hr_max", None)
                if hr_max:
                    max_hr = float(hr_max)
                else:
                    age = style.get("hr_max_age", 30)
                    max_hr = 220 - float(age)
                zone_color = FrameRenderer._hr_zone_color(hr_val, max_hr, original_color)
                # 只传 value_color，标签/单位颜色保持不变（仍基于 style.color）
                FrameRenderer._render_gauge(
                    draw,
                    widget,
                    record,
                    "heart_rate",
                    lambda v: v,
                    value_color=zone_color,
                    global_style=global_style,
                )
                return

        # 未启用区间颜色或无心率数据，走通用渲染
        FrameRenderer._render_gauge(
            draw,
            widget,
            record,
            "heart_rate",
            lambda v: v,
            global_style=global_style,
        )

    # 各字段对应的"最大显示字符串"，用于稳定布局高度（避免单位乱飞）
    # key: field_name，value: 无小数版最大字符串（decimals>0 时自动补点）
    _FIELD_MAX_DISPLAY = {
        "speed":      "999",    # km/h（transform 后 0–999）
        "heart_rate": "999",    # bpm
        "cadence":    "999",    # rpm
        "power":      "9999",   # W
        "altitude":   "9999",   # m（可能负值，但高度一致即可）
        "distance":   "9999",   # km
        "gradient":   "-99",    # %（带符号）
    }

    @staticmethod
    def _anchor_text(field_name: str, decimals: int, style: dict) -> str:
        """返回用于测量布局高度的"锚点字符串"。

        优先用 style.max_val 或 style.max_display 生成，
        否则按字段名取内置最大值字符串。
        返回值与实际 text 格式一致（含小数点），
        确保 textbbox 高度（th）在每帧保持不变，避免单位/标签乱飞。
        """
        # 用户显式指定最大显示值（数字）
        explicit = style.get("max_display", None)
        if explicit is None:
            explicit = style.get("max_val", None)
        if explicit is not None:
            try:
                v = float(explicit)
                base = f"{v:.{decimals}f}" if decimals > 0 else str(int(v))
                return base
            except (TypeError, ValueError):
                pass

        base = FrameRenderer._FIELD_MAX_DISPLAY.get(field_name, "999")
        if decimals > 0:
            return base + "." + "0" * decimals
        return base

    @staticmethod
    def _render_gauge(draw, widget, record, field_name, transform=None, value_color=None,
                      global_style: dict = None):
        """渲染数值型表盘

        支持两种视觉模式：
        - format="number"（默认）：居中大号数值 + 下方小号单位
        - format="arc"：圆弧表盘 + 居中数值

        当 style.font_family="industrial" 时：
        - 数值用 industrial 粗体
        - 标签/单位用 industrial 小号半透明
        - style.layout="stacked" 时：标签在上 → 数值居中 → 单位在下（纵向堆叠）

        value_color: 可选，单独指定数值文字的颜色（RGBA tuple）。
                     若指定，标签和单位仍使用 style.color 的派生色，只有数值使用此颜色。
        global_style: 全局样式设置（label_unit_shadow 等）
        """
        value = None
        if record and field_name != "track":
            raw = getattr(record, field_name, None)
            if raw is not None:
                value = transform(raw) if transform else raw

        style = widget.style
        gstyle = global_style or {}
        color = FrameRenderer._parse_color(style.get("color", "#ffffff"), default=(255, 255, 255, 255))
        # 数值文字颜色：若调用方传入 value_color 则单独使用，否则与整体 color 一致
        text_color = value_color if value_color is not None else color
        font_size = style.get("font_size", 28)
        unit = style.get("unit", "")
        fmt = style.get("format", "number")  # "number" | "arc"
        font_family = style.get("font_family", "default")  # "default" | "industrial"
        layout = style.get("layout", "centered")  # "centered" | "stacked"
        label = style.get("label", "")  # 可选：标签文字（如 "POWER", "CADENCE"）
        unit_offset_x = style.get("unit_offset_x", 0)  # 单位水平偏移（px）
        unit_offset_y = style.get("unit_offset_y", 0)  # 单位垂直偏移（px）
        label_offset_x = style.get("label_offset_x", 0)  # 标签水平偏移（px）
        label_offset_y = style.get("label_offset_y", 0)  # 标签垂直偏移（px）

        # 全局标签/单位阴影设置
        shadow_enabled = gstyle.get("label_unit_shadow", True)
        shadow_alpha = FrameRenderer._shadow_alpha(gstyle)

        # 选择字体族
        value_font = FrameRenderer._get_font(font_size, font_family)
        label_font = FrameRenderer._get_font(max(font_size * 3 // 5, 12), font_family)
        unit_font = FrameRenderer._get_font(max(font_size * 2 // 5, 10), font_family)

        # 格式化数值
        if value is not None:
            decimals = style.get("decimals", 1 if isinstance(value, float) else 0)
            text = f"{value:.{decimals}f}" if decimals > 0 else str(int(value))
        else:
            decimals = style.get("decimals", 0)
            text = "--"

        # 锚点字符串：用于稳定布局高度，避免单位随数值位数变化而跳动
        anchor_text = FrameRenderer._anchor_text(field_name, decimals, style)
        anchor_bbox = draw.textbbox((0, 0), anchor_text, font=value_font)
        anchor_th = anchor_bbox[3] - anchor_bbox[1]

        # ── 绘制圆弧表盘（如果 format=arc）──
        if fmt == "arc" and value is not None:
            min_val = style.get("min_val", 0)
            max_val = style.get("max_val", 100)
            ratio = max(0, min(1, (value - min_val) / (max_val - min_val))) if max_val > min_val else 0

            arc_width = 4
            cx, cy = widget.width // 2, widget.height * 2 // 3
            radius = min(widget.width, widget.height) // 3
            if radius > 10:
                bbox = [cx - radius, cy - radius, cx + radius, cy + radius]
                draw.arc(bbox, start=135, end=405, fill=(255, 255, 255, 40), width=arc_width)
                end_angle = 135 + int(ratio * 270)
                draw.arc(bbox, start=135, end=end_angle, fill=color, width=arc_width)

        # ── stacked 布局（工业风纵向堆叠）──
        if layout == "stacked" and fmt != "arc":
            text_align = style.get("text_align", "center")  # "left" | "center" | "right"

            def _align(avail_w, item_w, offset_x=0):
                if text_align == "left":
                    # leave some margin for left border
                    return 5 + offset_x
                elif text_align == "right":
                    return avail_w - item_w + offset_x
                else:  # center
                    return (avail_w - item_w) // 2 + offset_x

            # 标签（顶部）
            y_cursor = 4
            if label:
                lbbox = draw.textbbox((0, 0), label, font=label_font)
                lw = lbbox[2] - lbbox[0]
                lh = lbbox[3] - lbbox[1]
                lx = _align(widget.width, lw, label_offset_x)
                FrameRenderer._draw_label_text(draw, label,
                    lx, y_cursor + label_offset_y,
                    label_font, color, shadow_enabled, shadow_alpha)
                y_cursor += lh + 2 + label_offset_y

            # 留点空隙
            y_cursor += 8

            # 数值（中间，尽可能大）
            # tw 用实际文字宽度（保持水平对齐正确），th 用锚点高度（稳定单位 Y 位置）
            bbox = draw.textbbox((0, 0), text, font=value_font)
            tw = bbox[2] - bbox[0]
            x = _align(widget.width, tw)
            FrameRenderer._draw_text_with_shadow(draw, text, x, y_cursor,
                value_font, text_color, shadow_enabled, shadow_alpha)
            y_cursor += anchor_th + 1  # 用锚点高度推进，单位 Y 位置不随数字变化

            # 单位（底部）
            if unit:
                ubbox = draw.textbbox((0, 0), unit, font=unit_font)
                uw = ubbox[2] - ubbox[0]
                ux = _align(widget.width, uw, unit_offset_x)
                uy = y_cursor + unit_offset_y
                FrameRenderer._draw_unit_text(draw, unit, ux, uy,
                    unit_font, color, shadow_enabled)
        else:
            # ── centered 布局（原默认布局）──
            bbox = draw.textbbox((0, 0), text, font=value_font)
            tw = bbox[2] - bbox[0]
            x = (widget.width - tw) // 2
            # 用锚点高度定位垂直中心，使数值在固定区域内显示
            y = (widget.height - anchor_th) // 2 - (5 if fmt == "arc" else 0)

            FrameRenderer._draw_text_with_shadow(draw, text, x, y,
                value_font, text_color, shadow_enabled, shadow_alpha)

            # 单位
            if unit:
                ubbox = draw.textbbox((0, 0), unit, font=unit_font)
                uw = ubbox[2] - ubbox[0]
                ux = (widget.width - uw) // 2 + unit_offset_x
                uy = y + anchor_th + 2 + unit_offset_y  # 用锚点高度定位单位
                FrameRenderer._draw_unit_text(draw, unit, ux, uy,
                    unit_font, color, shadow_enabled, shadow_alpha)


    @staticmethod
    def _render_distance(draw, widget, record, fit_data, global_style: dict = None):
        """渲染距离表盘

        distance_mode:
          "current"       (默认) - 只显示当前距离，如 "13.5"
          "current_total"        - 显示当前/总距离，如 "13.5 / 153.8"
        """
        style = widget.style
        gstyle = global_style or {}
        shadow_enabled = gstyle.get("label_unit_shadow", True)
        shadow_alpha = FrameRenderer._shadow_alpha(gstyle)
        distance_mode = style.get("distance_mode", "current")  # "current" | "current_total"

        # 当前距离
        value = None
        if record:
            raw = getattr(record, "distance", None)
            if raw is not None:
                value = raw / 1000  # m → km

        # 总距离
        total_km = None
        if distance_mode == "current_total" and fit_data:
            session = fit_data.primary_session
            # 优先使用 session.total_distance（FIT 文件内置，最可靠）
            if session and session.total_distance > 0:
                total_km = session.total_distance / 1000
            # 回退到 haversine_total_distance（GPS 坐标积分，受 GPS 精度影响）
            elif fit_data.haversine_total_distance > 0:
                total_km = fit_data.haversine_total_distance / 1000
            # 最后回退：取 records 最后一条的 distance
            elif value is not None and session and session.records:
                last = session.records[-1]
                if last and last.distance is not None:
                    total_km = last.distance / 1000

        if distance_mode == "current_total" and total_km is not None:
            # ── 模式：当前 / 总距离 ──
            color = FrameRenderer._parse_color(style.get("color", "#ffffff"), default=(255, 255, 255, 255))
            font_size = style.get("font_size", 28)
            font_family = style.get("font_family", "default")
            layout = style.get("layout", "centered")
            label = style.get("label", "")
            text_align = style.get("text_align", "center")
            unit_offset_x = style.get("unit_offset_x", 0)
            unit_offset_y = style.get("unit_offset_y", 0)
            label_offset_x = style.get("label_offset_x", 0)
            label_offset_y = style.get("label_offset_y", 0)

            decimals = style.get("decimals", 1 if value is not None and isinstance(value, float) else 0)
            unit = style.get("unit", "km")

            value_font = FrameRenderer._get_font(font_size, font_family)
            total_font = FrameRenderer._get_font(max(font_size * 2 // 3, 12), font_family)
            label_font = FrameRenderer._get_font(max(font_size * 3 // 5, 12), font_family)
            unit_font = FrameRenderer._get_font(max(font_size * 2 // 5, 10), font_family)

            # 格式化当前距离
            cur_text = f"{value:.{decimals}f}" if value is not None else "--"
            total_text = f"{total_km:.{decimals}f}"

            # 锚点字符串：总距离是固定值，以总距离字符串作为大值字体的高度锚点
            # 确保当前距离从 "--" 变成数字时布局不跳动
            anchor_cur = FrameRenderer._anchor_text("distance", decimals, style)
            anchor_bbox_cur = draw.textbbox((0, 0), anchor_cur, font=value_font)
            anchor_th_cur = anchor_bbox_cur[3] - anchor_bbox_cur[1]

            def _align(avail_w, item_w, offset_x=0):
                if text_align == "left":
                    return 5 + offset_x
                elif text_align == "right":
                    return avail_w - item_w + offset_x
                else:
                    return (avail_w - item_w) // 2 + offset_x

            if layout == "stacked":
                # ── stacked 布局 ──
                y_cursor = 4
                # 标签
                if label:
                    lbbox = draw.textbbox((0, 0), label, font=label_font)
                    lw = lbbox[2] - lbbox[0]
                    lx = _align(widget.width, lw, label_offset_x)
                    FrameRenderer._draw_label_text(draw, label,
                        lx, y_cursor + label_offset_y,
                        label_font, color, shadow_enabled, shadow_alpha)
                    lh = lbbox[3] - lbbox[1]
                    y_cursor += lh + 2 + label_offset_y
                y_cursor += 8

                # 当前距离（大号）：tw 用实际宽度，th 用锚点高度
                bbox = draw.textbbox((0, 0), cur_text, font=value_font)
                tw = bbox[2] - bbox[0]
                x = _align(widget.width, tw)
                FrameRenderer._draw_text_with_shadow(draw, cur_text, x, y_cursor,
                    value_font, color, shadow_enabled, shadow_alpha)
                y_cursor += anchor_th_cur + 1  # 用锚点高度推进

                # / 总距离（小号半透明）
                total_full = f"/ {total_text} {unit}"
                tbbox = draw.textbbox((0, 0), total_full, font=total_font)
                ttw = tbbox[2] - tbbox[0]
                tx = _align(widget.width, ttw, unit_offset_x)
                ty = y_cursor + unit_offset_y
                FrameRenderer._draw_unit_text(draw, total_full, tx, ty,
                    total_font, color, shadow_enabled, shadow_alpha)
            else:
                # ── centered 布局：当前距离 大号 + /总距离 小号 ──
                bbox = draw.textbbox((0, 0), cur_text, font=value_font)
                tw = bbox[2] - bbox[0]
                # th 用锚点高度，单位行 Y 位置不随位数变化
                th = anchor_th_cur

                total_full = f"/ {total_text}"
                tbbox = draw.textbbox((0, 0), total_full, font=total_font)
                ttw = tbbox[2] - tbbox[0]
                tth = tbbox[3] - tbbox[1]

                # 整行居中
                gap_px = 6
                total_w = tw + gap_px + ttw
                start_x = (widget.width - total_w) // 2
                y = (widget.height - max(th, tth)) // 2

                # 当前距离
                FrameRenderer._draw_text_with_shadow(draw, cur_text, start_x, y,
                    value_font, color, shadow_enabled, shadow_alpha)

                # / 总距离
                total_x = start_x + tw + gap_px
                total_y = y + th - tth  # 底部对齐
                FrameRenderer._draw_unit_text(draw, total_full, total_x, total_y,
                    total_font, color, shadow_enabled, shadow_alpha)

                # 单位
                if unit:
                    full_unit_text = f"{unit}"
                    ubbox = draw.textbbox((0, 0), full_unit_text, font=unit_font)
                    uw = ubbox[2] - ubbox[0]
                    ux = (widget.width - uw) // 2 + unit_offset_x
                    uy = y + max(th, tth) + 2 + unit_offset_y
                    FrameRenderer._draw_unit_text(draw, full_unit_text, ux, uy,
                        unit_font, color, shadow_enabled, shadow_alpha)
        else:
            # ── 默认模式：只显示当前距离（走原来的 _render_gauge）──
            FrameRenderer._render_gauge(draw, widget, record, "distance",
                                        lambda v: v / 1000,
                                        global_style=gstyle)  # m → km

    @staticmethod
    def _render_timer(draw, widget, record, fit_data, fit_time,
                      global_style: dict = None):
        """渲染运动时间

        time_mode:
          "elapsed" (默认) - 从运动开始到现在的时长（HH:MM:SS）
          "clock"           - 当前 24 小时制时间（HH:MM:SS）

        clock 模式下的时区：
          style.timezone: "local"（默认，使用 FIT 时间本身的时区）
                         或 IANA 时区名如 "Asia/Shanghai"、"UTC"、"America/New_York" 等

        支持 style.layout="stacked" 和 style.text_align="left"|"center"|"right"
        stacked 布局：标签(上) → 时间(中) → 单位(下)
        """
        session = fit_data.primary_session
        if not session or not session.start_time or not fit_time:
            return

        style = widget.style
        gstyle = global_style or {}
        shadow_enabled = gstyle.get("label_unit_shadow", True)
        shadow_alpha = FrameRenderer._shadow_alpha(gstyle)
        time_mode = style.get("time_mode", "elapsed")  # "elapsed" | "clock"
        font_family = style.get("font_family", "default")
        font_size = style.get("font_size", 24)
        font = FrameRenderer._get_font(font_size, font_family)
        color = FrameRenderer._parse_color(style.get("color", "#ffffff"), default=(255, 255, 255, 255))
        layout = style.get("layout", "centered")
        text_align = style.get("text_align", "center")
        label_text = style.get("label", "")
        unit_offset_x = style.get("unit_offset_x", 0)
        unit_offset_y = style.get("unit_offset_y", 0)
        label_offset_x = style.get("label_offset_x", 0)
        label_offset_y = style.get("label_offset_y", 0)

        elapsed = (fit_time - session.start_time).total_seconds()
        unit = ""

        if time_mode == "clock":
            # 24 小时制当前时间，支持时区
            tz_name = style.get("timezone", "local")
            if tz_name and tz_name != "local":
                try:
                    from zoneinfo import ZoneInfo
                    tz = ZoneInfo(tz_name)
                    text = fit_time.astimezone(tz).strftime("%H:%M:%S")
                except Exception:
                    text = fit_time.strftime("%H:%M:%S")
            else:
                text = fit_time.strftime("%H:%M:%S")
        else:
            # 运动时长
            if elapsed < 0:
                text = "--:--:--"
            else:
                hours = int(elapsed // 3600)
                minutes = int((elapsed % 3600) // 60)
                seconds = int(elapsed % 60)
                text = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                # 超过 99 小时显示天数
                if hours >= 100:
                    days = hours // 24
                    h_rem = hours % 24
                    text = f"{days}d {h_rem:02d}:{minutes:02d}:{seconds:02d}"

        def _align(avail_w, item_w, offset_x=0):
            if text_align == "left":
                return 5 + offset_x
            elif text_align == "right":
                return avail_w - item_w + offset_x
            else:
                return (avail_w - item_w) // 2 + offset_x

        # 锚点字符串：时间格式固定为 HH:MM:SS，用于稳定布局高度
        # 超长（天数）格式仅在极端情况出现，不作为主锚点
        anchor_time_str = "00:00:00"
        anchor_bbox = draw.textbbox((0, 0), anchor_time_str, font=font)
        anchor_th = anchor_bbox[3] - anchor_bbox[1]

        if layout == "stacked":
            # ── stacked 布局：标签(上) → 时间(中) → 单位(下) ──
            y_cursor = 4

            # 标签
            if label_text:
                label_font = FrameRenderer._get_font(max(font_size * 3 // 5, 12), font_family)
                lbbox = draw.textbbox((0, 0), label_text, font=label_font)
                lw = lbbox[2] - lbbox[0]
                lx = _align(widget.width, lw, label_offset_x)
                FrameRenderer._draw_label_text(draw, label_text,
                    lx, y_cursor + label_offset_y,
                    label_font, color, shadow_enabled, shadow_alpha)
                lh = lbbox[3] - lbbox[1]
                y_cursor += lh + 2 + label_offset_y

            y_cursor += 8
            # 时间（大号）：tw 用实际宽度，th 用锚点高度
            bbox = draw.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
            x = _align(widget.width, tw)
            FrameRenderer._draw_text_with_shadow(draw, text, x, y_cursor,
                font, color, shadow_enabled, shadow_alpha)
            y_cursor += anchor_th + 1  # 用锚点高度推进，单位 Y 稳定

            # 单位（小号半透明）
            if unit:
                unit_font = FrameRenderer._get_font(max(font_size * 2 // 5, 10), font_family)
                ubbox = draw.textbbox((0, 0), unit, font=unit_font)
                uw = ubbox[2] - ubbox[0]
                ux = _align(widget.width, uw, unit_offset_x)
                uy = y_cursor + unit_offset_y
                FrameRenderer._draw_unit_text(draw, unit, ux, uy,
                    unit_font, color, shadow_enabled, shadow_alpha)
        else:
            # ── centered 布局（默认）：居中显示时间 ──
            bbox = draw.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
            x = _align(widget.width, tw)
            y = (widget.height - anchor_th) // 2  # 用锚点高度垂直居中
            FrameRenderer._draw_text_with_shadow(draw, text, x, y,
                font, color, shadow_enabled, shadow_alpha)

    @staticmethod
    def _render_gradient(draw, widget, record, fit_data, fit_time,
                         global_style: dict = None):
        """渲染坡度指示

        坡度直接使用 parse 阶段预计算/平滑后的 record.gradient。

        支持 stacked 布局的 text_align / unit_offset / label_offset，
        与 _render_gauge 对齐逻辑一致。
        """
        gstyle = global_style or {}
        shadow_enabled = gstyle.get("label_unit_shadow", True)
        shadow_alpha = FrameRenderer._shadow_alpha(gstyle)
        gradient = record.gradient if record and record.gradient is not None else 0

        style = widget.style
        color = FrameRenderer._parse_color(style.get("color", "#ffaa00"), default=(255, 170, 0, 255))
        font_family = style.get("font_family", "default")
        font_size = style.get("font_size", 24)
        font = FrameRenderer._get_font(font_size, font_family)
        decimals = style.get("decimals", 1)
        unit = style.get("unit", "%")
        # 数值文本（不含单位）
        text = f"{gradient:+.{decimals}f}"

        # 锚点字符串：带符号、最大宽度，用于稳定布局高度
        # 坡度通常 ±99.9%，以 "-99.9..." 作为锚点（带负号宽度更大）
        anchor_str = "-99" + ("." + "9" * decimals if decimals > 0 else "")
        anchor_bbox = draw.textbbox((0, 0), anchor_str, font=font)
        anchor_th = anchor_bbox[3] - anchor_bbox[1]

        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]

        # 支持 stacked 布局
        layout = style.get("layout", "centered")
        label = style.get("label", "")
        if layout == "stacked" and label:
            label_font = FrameRenderer._get_font(max(font_size * 3 // 5, 12), font_family)
            unit_font = FrameRenderer._get_font(max(font_size * 2 // 5, 10), font_family)
            unit_offset_x = style.get("unit_offset_x", 0)
            unit_offset_y = style.get("unit_offset_y", 0)
            label_offset_x = style.get("label_offset_x", 0)
            label_offset_y = style.get("label_offset_y", 0)
            text_align = style.get("text_align", "center")

            def _align(avail_w, item_w, offset_x=0):
                if text_align == "left":
                    return 0 + offset_x
                elif text_align == "right":
                    return avail_w - item_w + offset_x
                else:  # center
                    return (avail_w - item_w) // 2 + offset_x

            y_cursor = 4
            # 标签
            lbbox = draw.textbbox((0, 0), label, font=label_font)
            lw = lbbox[2] - lbbox[0]
            lh = lbbox[3] - lbbox[1]
            lx = _align(widget.width, lw, label_offset_x)
            FrameRenderer._draw_label_text(draw, label,
                lx, y_cursor + label_offset_y,
                label_font, color, shadow_enabled, shadow_alpha)
            y_cursor += lh + 2 + label_offset_y

            y_cursor += 8

            # 数值（阴影 + 前景）
            vx = _align(widget.width, tw)
            FrameRenderer._draw_text_with_shadow(draw, text, vx, y_cursor,
                font, color, shadow_enabled, shadow_alpha)
            y_cursor += anchor_th + 1  # 用锚点高度推进，单位 Y 稳定

            # 单位
            if unit:
                ubbox = draw.textbbox((0, 0), unit, font=unit_font)
                uw = ubbox[2] - ubbox[0]
                ux = _align(widget.width, uw, unit_offset_x)
                uy = y_cursor + unit_offset_y
                FrameRenderer._draw_unit_text(draw, unit, ux, uy,
                    unit_font, color, shadow_enabled, shadow_alpha)
        else:
            # centered 布局：数值+单位拼接
            full_text = f"{text}{unit}"
            fbbox = draw.textbbox((0, 0), full_text, font=font)
            ftw = fbbox[2] - fbbox[0]
            fth = fbbox[3] - fbbox[1]
            fx = (widget.width - ftw) // 2
            fy = (widget.height - fth) // 2
            FrameRenderer._draw_unit_text(draw, full_text, fx, fy,
                font, color, shadow_enabled, shadow_alpha)

    @staticmethod
    def _render_altitude_chart(draw, widget, record, fit_data, fit_time,
                               global_style: dict = None):
        """渲染海拔剖面图

        chart_mode:
          "full"   (默认) - 显示全程海拔，当前位置用竖线标记
          "follow"        - 跟随模式：以当前位置为中心，显示前后 follow_window/2 秒窗口
                            当前位置用白色实心圆圈标记

        follow_window: follow 模式时间窗口（秒），默认 120
        """
        from datetime import timedelta

        session = fit_data.primary_session
        if not session or not session.records:
            return

        records = session.records
        # 找有效海拔数据
        all_alt_data = [(r.timestamp, r.altitude) for r in records
                        if r.altitude is not None and r.timestamp is not None]
        if len(all_alt_data) < 2:
            return

        style = widget.style
        chart_mode = style.get("chart_mode", "full")
        follow_window = float(style.get("follow_window", 120))  # 秒

        line_color = FrameRenderer._parse_color(style.get("line_color", "#aa88ff"), default=(170, 136, 255, 255))
        fill_color = FrameRenderer._parse_color(style.get("fill_color", "#aa88ff30"), default=(170, 136, 255, 48))

        # ── 确定显示窗口 ──
        global_start = all_alt_data[0][0]
        global_end   = all_alt_data[-1][0]

        if chart_mode == "follow" and fit_time is not None:
            half_w = follow_window / 2.0
            win_start = fit_time - timedelta(seconds=half_w)
            win_end   = fit_time + timedelta(seconds=half_w)
            # 夹在全程范围内（不溢出）
            win_start = max(win_start, global_start)
            win_end   = min(win_end,   global_end)
            # 筛选窗口内的数据点，并在两端插值补边界
            alt_data = [d for d in all_alt_data if win_start <= d[0] <= win_end]
            # 如果窗口内数据不足，直接用全程
            if len(alt_data) < 2:
                alt_data = all_alt_data
                chart_mode = "full"   # 降级为全程模式
        else:
            chart_mode = "full"
            alt_data = all_alt_data

        # ── 降采样：数据点远超像素宽度时 ──
        max_points = widget.width * 2  # Nyquist 2x 采样
        if len(alt_data) > max_points:
            step = len(alt_data) / max_points
            sampled = []
            i = 0.0
            while int(i) < len(alt_data):
                sampled.append(alt_data[int(i)])
                i += step
            if sampled[-1] != alt_data[-1]:
                sampled.append(alt_data[-1])
            alt_data = sampled

        min_alt = min(a for _, a in alt_data)
        max_alt = max(a for _, a in alt_data)
        alt_range = max_alt - min_alt if max_alt > min_alt else 1

        start_time = alt_data[0][0]
        end_time   = alt_data[-1][0]
        total_dur  = (end_time - start_time).total_seconds()
        if total_dur <= 0:
            return

        # ── 内边距 ──
        pad = 2
        draw_w = widget.width  - pad * 2
        draw_h = widget.height - pad * 2

        # 坐标映射函数
        def _to_xy(ts, alt):
            x = pad + int((ts - start_time).total_seconds() / total_dur * draw_w)
            y = widget.height - pad - int((alt - min_alt) / alt_range * draw_h)
            return (x, y)

        # 绘制海拔曲线
        points = [_to_xy(ts, alt) for ts, alt in alt_data]

        if len(points) > 1:
            draw.line(points, fill=line_color, width=2)
            # 填充区域
            fill_points = ([points[0]] + points +
                           [(points[-1][0], widget.height), (points[0][0], widget.height)])
            draw.polygon(fill_points, fill=fill_color)

        # ── 当前位置标记 ──
        if fit_time is not None and start_time <= fit_time <= end_time:
            if chart_mode == "follow":
                # follow 模式：当前位置固定在中央，白色实心圆圈
                cx = pad + draw_w // 2
            else:
                cx = pad + int((fit_time - start_time).total_seconds() / total_dur * draw_w)

            # 找当前时刻对应海拔（线性插值）
            cur_alt = None
            for i in range(len(alt_data) - 1):
                t0, a0 = alt_data[i]
                t1, a1 = alt_data[i + 1]
                if t0 <= fit_time <= t1:
                    frac = (fit_time - t0).total_seconds() / max((t1 - t0).total_seconds(), 1e-6)
                    cur_alt = a0 + (a1 - a0) * frac
                    break
            if cur_alt is None and alt_data:
                # 边界：取最后一个点
                cur_alt = alt_data[-1][1]

            if cur_alt is not None:
                cy = widget.height - pad - int((cur_alt - min_alt) / alt_range * draw_h)
            else:
                cy = widget.height // 2

            r_circle = max(4, min(widget.height // 6, 8))  # 圆圈半径，自适应高度
            draw.ellipse(
                [cx - r_circle, cy - r_circle, cx + r_circle, cy + r_circle],
                fill=(255, 255, 255, 230),
                outline=(200, 200, 200, 180),
                width=1,
            )
        elif fit_time is not None and chart_mode == "full":
            # 全程模式，时间超出范围时还是画竖线
            if fit_time > end_time:
                cx = widget.width - pad
            else:
                cx = pad
            draw.line([(cx, 0), (cx, widget.height)], fill=(255, 255, 255, 150), width=1)

    @staticmethod
    def _apply_map_track_shape(region: Image.Image, widget) -> Image.Image:
        """对 MapTrack 区域进行形状裁剪、内侧晕圈、边框后处理。

        style 参数：
          map_shape       : "rect"（默认）| "circle"
          border_radius   : 方形圆角像素（仅 map_shape="rect" 时有效，默认 8）
          border_width    : 边框宽度像素（0 = 无边框，默认 0）
          border_color    : 边框颜色（默认 "#ffffff"）
          border_glow     : 内侧透明晕圈宽度像素（0 = 无，默认 0）
                            晕圈从地图边缘向内渐变到透明，形成柔和边缘感

        返回一张新的 RGBA Image（尺寸 = widget + 边框扩展）。
        """
        style = widget.style
        w, h = region.width, region.height
        map_shape = style.get("map_shape", "rect")
        border_w = int(style.get("border_width", 0))
        border_color_raw = style.get("border_color", "#ffffff")
        border_color = FrameRenderer._parse_color(border_color_raw, default=(255, 255, 255, 255))
        glow = int(style.get("border_glow", 0))
        radius = int(style.get("border_radius", 8))

        # ── 1. 生成形状蒙版（只裁剪地图内容，不含边框扩展）──
        shape_mask = Image.new("L", (w, h), 0)
        mask_draw = ImageDraw.Draw(shape_mask)
        if map_shape == "circle":
            mask_draw.ellipse([0, 0, w - 1, h - 1], fill=255)
        else:
            r = min(radius, w // 2, h // 2)
            mask_draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)

        # ── 2. 应用晕圈：在蒙版边缘内侧渐变衰减 ──
        if glow > 0:
            # 使用高斯模糊生成边缘衰减蒙版
            from PIL import ImageFilter
            # 收缩版蒙版（内部完全不透明区域）
            inner_mask = shape_mask.filter(ImageFilter.GaussianBlur(radius=glow))
            # 组合：最终 alpha = min(shape_mask, inner_mask) → 保留形状 + 边缘渐变
            shape_mask = Image.fromarray(
                np.minimum(np.array(shape_mask), np.array(inner_mask)).astype(np.uint8)
            )

        # ── 3. 用蒙版裁剪地图区域 ──
        region_arr = np.array(region)
        mask_arr = np.array(shape_mask)
        # 将地图原有 alpha 与形状蒙版相乘
        region_arr[:, :, 3] = (region_arr[:, :, 3].astype(np.float32)
                               * mask_arr.astype(np.float32) / 255.0).astype(np.uint8)
        region = Image.fromarray(region_arr)

        # ── 4. 创建输出画布（含边框扩展）──
        total_w = w + border_w * 2
        total_h = h + border_w * 2
        if border_w <= 0:
            # 无边框：直接返回裁剪后的地图
            return region

        out = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))

        # ── 5. 绘制边框 ──
        out_draw = ImageDraw.Draw(out)
        bx0, by0 = 0, 0
        bx1, by1 = total_w - 1, total_h - 1
        if map_shape == "circle":
            # 填充完整椭圆作为边框背景
            out_draw.ellipse([bx0, by0, bx1, by1], fill=border_color)
            # 中间挖空（通过蒙版）
            inner_clear = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
            inner_draw = ImageDraw.Draw(inner_clear)
            inner_draw.ellipse([border_w, border_w, total_w - border_w - 1, total_h - border_w - 1],
                               fill=(0, 0, 0, 255))
            # 用 inner_clear 的 alpha 作为蒙版，将 out 对应区域清零
            out_arr = np.array(out)
            clear_arr = np.array(inner_clear)
            # 内圆区域：out alpha = 0
            inner_alpha = clear_arr[:, :, 3].astype(np.float32) / 255.0
            out_arr[:, :, 3] = (out_arr[:, :, 3].astype(np.float32) * (1.0 - inner_alpha)).astype(np.uint8)
            out = Image.fromarray(out_arr)
        else:
            r_outer = min(radius + border_w, total_w // 2, total_h // 2)
            r_inner = min(radius, w // 2, h // 2)
            out_draw.rounded_rectangle([bx0, by0, bx1, by1], radius=r_outer, fill=border_color)
            # 挖空中心（内圆角矩形）
            inner_clear = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
            inner_draw = ImageDraw.Draw(inner_clear)
            inner_draw.rounded_rectangle(
                [border_w, border_w, total_w - border_w - 1, total_h - border_w - 1],
                radius=r_inner, fill=(0, 0, 0, 255)
            )
            out_arr = np.array(out)
            clear_arr = np.array(inner_clear)
            inner_alpha = clear_arr[:, :, 3].astype(np.float32) / 255.0
            out_arr[:, :, 3] = (out_arr[:, :, 3].astype(np.float32) * (1.0 - inner_alpha)).astype(np.uint8)
            out = Image.fromarray(out_arr)

        # ── 6. 将裁剪后的地图合成到边框画布中央 ──
        out.alpha_composite(region, (border_w, border_w))

        return out

    @staticmethod
    def _render_map_track_bg(widget, fit_data, record=None):
        """渲染轨迹地图的瓦片底图背景，返回 RGBA Image 或 None

        map_mode:
          "overview"（默认）- 轨迹全览，中心为轨迹 bounding box 中心
          "follow"          - 地图跟随，中心为当前位置，高 zoom 不缩放只 pan

        follow_scale:
          跟随模式下的放大倍数（1.0 = 原始，2.0 = 2 倍放大）。
          原理：以更大尺寸渲染底图再缩小，使瓦片文字/标注变大，
          而不改变 zoom 级别（不会加载更多瓦片，不会增加细节）。
        """
        from services.fit_parser import FitParserService

        style = widget.style
        tile_style = style.get("tile_source", "")  # osm | carto_dark | ... | "" (无底图)
        if not tile_style:
            return None

        coords = FitParserService.get_track_coords(fit_data)
        if len(coords) < 2:
            return None

        try:
            from services.tile_service import render_tile_map, compute_zoom_for_size, latlon_to_pixel, pixel_to_latlon, get_tile_url

            lats = [c[0] for c in coords]
            lons = [c[1] for c in coords]
            min_lat, max_lat = min(lats), max(lats)
            min_lon, max_lon = min(lons), max(lons)

            map_mode = style.get("map_mode", "overview")  # "overview" | "follow"
            follow_scale = float(style.get("follow_scale", 1.0))  # 放大倍数
            follow_scale = max(1.0, min(follow_scale, 4.0))

            if map_mode == "follow" and record and record.latitude and record.longitude:
                # ── 跟随模式：中心 = 当前位置 ──
                center_lat = record.latitude
                center_lon = record.longitude

                # 跟随模式下使用固定的高 zoom（如 15 或 16）
                zoom = style.get("follow_zoom", 15)
                if isinstance(zoom, str):
                    zoom = int(zoom)
                zoom = max(10, min(18, zoom))
            else:
                # ── 全览模式：中心 = 轨迹 bounding box 中心 ──
                center_lat = (min_lat + max_lat) / 2
                center_lon = (min_lon + max_lon) / 2

                zoom = style.get("zoom", None)
                if zoom is None or zoom <= 0:
                    zoom = compute_zoom_for_size(min_lat, max_lat, min_lon, max_lon,
                                                  widget.width, widget.height, padding=0.1)

            # ── 放大渲染：用更小逻辑视口渲染底图，再放大回 widget 尺寸 ──
            # 这相当于“放大镜”效果：显示更小的地理范围，但像素铺满整个 widget。
            render_w, render_h, _, _ = FrameRenderer._tile_follow_render_metrics(
                widget.width, widget.height, follow_scale
            )

            tile_url = get_tile_url(tile_style)
            tile_map = render_tile_map(center_lat, center_lon, zoom,
                                       render_w, render_h,
                                       tile_url_template=tile_url)

            # 如果有放大，缩小到实际 Widget 尺寸
            if follow_scale > 1.0 and tile_map.size != (widget.width, widget.height):
                tile_map = tile_map.resize((widget.width, widget.height), Image.LANCZOS)

            return tile_map

        except Exception as e:
            print(f"[MapTrack] 瓦片底图渲染失败: {e}")
            return None

    @staticmethod
    def _render_map_track(draw, widget, record, fit_data, fit_time):
        """渲染轨迹地图（矢量轨迹线 + 位置标记，叠加在底图上）

        优化：
        - 使用 numpy 向量化批量投影所有轨迹点（替代逐点调用闭包）
        - 使用 bisect 二分查找 walked_points 截断索引
        - 渐变色轨迹：numpy 插值生成逐段颜色，PIL 逐段绘制
        """
        from services.fit_parser import FitParserService

        coords = FitParserService.get_track_coords(fit_data)
        if len(coords) < 2:
            return

        style = widget.style
        track_color = FrameRenderer._parse_color(style.get("track_color", "#00d4aa"), default=(0, 212, 170, 255))
        marker_color = FrameRenderer._parse_color(style.get("marker_color", "#ff4444"), default=(255, 68, 68, 255))
        track_width = max(1, int(style.get("track_width", 2)))
        walked_width = max(1, int(style.get("walked_width", track_width + 1)))

        # ── 提取 numpy 数组 ──
        coords_arr = np.array(coords, dtype=np.float64)  # shape (N, 2)
        lats = coords_arr[:, 0]
        lons = coords_arr[:, 1]

        # ── 计算 bounding box ──
        min_lat, max_lat = float(lats.min()), float(lats.max())
        min_lon, max_lon = float(lons.min()), float(lons.max())
        center_lat = (min_lat + max_lat) / 2
        center_lon = (min_lon + max_lon) / 2

        lat_range = max_lat - min_lat if max_lat > min_lat else 0.001
        lon_range = max_lon - min_lon if max_lon > min_lon else 0.001

        # 添加 padding
        padding = 0.05
        lat_range_padded = lat_range * (1 + padding * 2)
        lon_range_padded = lon_range * (1 + padding * 2)
        min_lat_padded = min_lat - lat_range * padding
        min_lon_padded = min_lon - lon_range * padding

        # ── 选择投影方式并批量计算像素坐标 ──
        tile_style = style.get("tile_source", "")
        map_mode = style.get("map_mode", "overview")
        follow_scale = float(style.get("follow_scale", 1.0))
        follow_scale = max(1.0, min(follow_scale, 4.0))

        if tile_style:
            # 瓦片底图模式：使用 Web Mercator 坐标系投影
            try:
                from services.tile_service import latlon_to_pixel, compute_zoom_for_size

                if map_mode == "follow" and record and record.latitude and record.longitude:
                    proj_center_lat = record.latitude
                    proj_center_lon = record.longitude
                    zoom = style.get("follow_zoom", 15)
                    if isinstance(zoom, str):
                        zoom = int(zoom)
                    zoom = max(10, min(18, zoom))
                else:
                    proj_center_lat = center_lat
                    proj_center_lon = center_lon
                    zoom = style.get("zoom", None)
                    if zoom is None or zoom <= 0:
                        zoom = compute_zoom_for_size(min_lat, max_lat, min_lon, max_lon,
                                                      widget.width, widget.height, padding=0.1)

                # ── numpy 向量化 Web Mercator 投影 ──
                # 与底图保持同一套变换：
                # 1. 在较小的逻辑视口(render_w/render_h)中定位像素
                # 2. 再按 (widget / render) 放大回最终屏幕坐标
                center_px_x, center_px_y = latlon_to_pixel(proj_center_lat, proj_center_lon, zoom)
                render_w, render_h, scale_x, scale_y = FrameRenderer._tile_follow_render_metrics(
                    widget.width, widget.height, follow_scale
                )
                half_w = render_w / 2.0
                half_h = render_h / 2.0

                n = 2.0 ** zoom
                px_x = (lons + 180.0) / 360.0 * n * 256
                lat_rad = np.radians(lats)
                px_y = (1.0 - np.log(np.tan(lat_rad) + 1.0 / np.cos(lat_rad)) / np.pi) / 2.0 * n * 256
                # 先映射到逻辑视口，再按与底图一致的缩放倍率放大到实际 widget 尺寸。
                all_x, all_y = FrameRenderer._project_tile_pixels_to_widget(
                    px_x, px_y,
                    center_px_x, center_px_y,
                    render_w, render_h,
                    scale_x, scale_y,
                )

                # 投影单个坐标（用于当前位置标记等少量点）
                def project_single(lat, lon):
                    sx, sy = latlon_to_pixel(lat, lon, zoom)
                    px_arr_x, px_arr_y = FrameRenderer._project_tile_pixels_to_widget(
                        np.array([sx], dtype=np.float64),
                        np.array([sy], dtype=np.float64),
                        center_px_x, center_px_y,
                        render_w, render_h,
                        scale_x, scale_y,
                    )
                    return int(px_arr_x[0]), int(px_arr_y[0])

            except Exception:
                tile_style = ""  # 回退到矢量模式

        if not tile_style:
            # ── 无底图：矢量轨迹线模式（经度修正） ──
            margin = 5
            w = widget.width - margin * 2
            h = widget.height - margin * 2
            cos_lat = math.cos(math.radians(center_lat))
            effective_lon_range = lon_range_padded * cos_lat

            # ── numpy 向量化等距投影 ──
            all_x = (margin + ((lons - min_lon_padded) * cos_lat) / effective_lon_range * w).astype(np.int32)
            all_y = (margin + (max_lat + lat_range * padding - lats) / lat_range_padded * h).astype(np.int32)

            def project_single(lat, lon):
                x = margin + int(((lon - min_lon_padded) * cos_lat) / effective_lon_range * w)
                y = margin + int((max_lat + lat_range * padding - lat) / lat_range_padded * h)
                return x, y

        # ── 批量生成 points 列表 ──
        all_points = list(zip(all_x.tolist(), all_y.tolist()))

        # ── 绘制全部轨迹线（暗色） ──
        if len(all_points) > 1:
            dim_track = (track_color[0] // 3, track_color[1] // 3, track_color[2] // 3, 200)
            draw.line(all_points, fill=dim_track, width=track_width)

        # ── 绘制已走路径（亮色+渐变）：numpy bisect + 切片 ──
        if fit_time and record and record.latitude and record.longitude:
            session = fit_data.primary_session
            if session and session.records:
                walked_points, walked_colors = FrameRenderer._get_walked_points_vectorized(
                    session.records, fit_time, all_x, all_y)

                # 渐变绘制：用 PIL 逐段绘制不同颜色
                if walked_points and len(walked_points) > 1:
                    n_walked = len(walked_points)
                    # 渐变 alpha：从暗(100)到亮(255)
                    alphas = np.linspace(100, 255, n_walked, dtype=np.int32)
                    base_r, base_g, base_b = track_color[0], track_color[1], track_color[2]

                    # 逐段绘制（分段批量，避免逐像素调用）
                    # 每 k 帧一段，平衡视觉效果和 draw 调用次数
                    segment_size = max(1, n_walked // 32)
                    for seg_start in range(0, n_walked - 1, segment_size):
                        seg_end = min(seg_start + segment_size + 1, n_walked)
                        seg_points = walked_points[seg_start:seg_end]
                        # 取段中间 alpha
                        mid_alpha = int(alphas[min(seg_start + segment_size // 2, n_walked - 1)])
                        seg_color = (base_r, base_g, base_b, mid_alpha)
                        if len(seg_points) > 1:
                            draw.line(seg_points, fill=seg_color, width=walked_width)

        # ── 当前位置标记 ──
        if record and record.latitude and record.longitude:
            cx, cy = project_single(record.latitude, record.longitude)
            marker_size = style.get("marker_size", 6)
            draw.ellipse([cx - marker_size, cy - marker_size, cx + marker_size, cy + marker_size],
                         fill=marker_color)

    @staticmethod
    def _tile_follow_render_metrics(widget_width: int, widget_height: int, follow_scale: float):
        """计算跟随底图/轨迹共用的逻辑视口尺寸与回放大倍率。

        follow_scale > 1 表示显示更小的地理范围，再放大回 widget 尺寸。
        这样底图、轨迹、当前位置标记必须共用同一套 metrics，才能严格对齐。
        """
        follow_scale = max(1.0, min(float(follow_scale), 4.0))
        render_w = max(1, int(round(widget_width / follow_scale)))
        render_h = max(1, int(round(widget_height / follow_scale)))
        scale_x = widget_width / render_w
        scale_y = widget_height / render_h
        return render_w, render_h, scale_x, scale_y

    @staticmethod
    def _project_tile_pixels_to_widget(
        px_x,
        px_y,
        center_px_x: float,
        center_px_y: float,
        render_w: int,
        render_h: int,
        scale_x: float,
        scale_y: float,
    ):
        """将 Web Mercator 像素坐标投影到 widget 坐标。

        先在逻辑视口(render_w/render_h)中定位，再按 scale_x/scale_y
        放大回最终 widget 尺寸。支持 numpy 数组输入。
        """
        half_w = render_w / 2.0
        half_h = render_h / 2.0
        logic_x = px_x - center_px_x + half_w
        logic_y = px_y - center_px_y + half_h
        out_x = np.rint(logic_x * scale_x).astype(np.int32)
        out_y = np.rint(logic_y * scale_y).astype(np.int32)
        return out_x, out_y

    @staticmethod
    def _get_walked_points_vectorized(records, fit_time, all_x, all_y):
        """使用 bisect + numpy 切片获取已走路径点（替代逐点调用闭包）

        Args:
            records: FitRecord 列表（按 timestamp 升序）
            fit_time: 当前 FIT 时间
            all_x: np.ndarray, 全部轨迹点的 x 像素坐标
            all_y: np.ndarray, 全部轨迹点的 y 像素坐标

        Returns:
            walked_points: [(x, y), ...] 已走路径的投影点列表
            walked_alphas: np.ndarray 对应的 alpha 值（可选渐变）
        """
        import bisect

        n = len(records)
        if n < 1000:
            # 小数据集：直接切片
            cut_idx = n
            for i, r in enumerate(records):
                if r.timestamp and r.timestamp > fit_time:
                    cut_idx = i
                    break
            # 从 all_x/all_y 中取有坐标的记录
            walked_points = []
            for i in range(cut_idx):
                r = records[i]
                if r.latitude is not None and r.longitude is not None and i < len(all_x):
                    walked_points.append((int(all_x[i]), int(all_y[i])))
            return walked_points, None

        # 大数据集：bisect 查找
        timestamps = [r.timestamp for r in records if r.timestamp is not None]
        if not timestamps:
            return [], None

        cut_idx = bisect.bisect_right(timestamps, fit_time)

        # 构建 mask：有坐标且在 cut_idx 范围内
        walked_points = []
        for i in range(min(cut_idx, len(all_x))):
            r = records[i]
            if r.latitude is not None and r.longitude is not None:
                walked_points.append((int(all_x[i]), int(all_y[i])))

        return walked_points, None

    @staticmethod
    def _render_label(draw, widget, global_style: dict = None):
        """渲染自定义标签"""
        gstyle = global_style or {}
        shadow_enabled = gstyle.get("label_unit_shadow", True)
        shadow_alpha = FrameRenderer._shadow_alpha(gstyle)
        text = widget.style.get("text", "Label")
        color = FrameRenderer._parse_color(widget.style.get("color", "#ffffff"), default=(255, 255, 255, 255))
        font_size = widget.style.get("font_size", 16)
        font_family = widget.style.get("font_family", "default")
        font = FrameRenderer._get_font(font_size, font_family)
        FrameRenderer._draw_text_with_shadow(draw, text, 5, 5,
            font, color, shadow_enabled,
            max(0, min(255, int(round(shadow_alpha * 0.78)))))

    # ── 工具方法 ──────────────────────────────────

    @staticmethod
    def _get_font(size: int, family: str = "default"):
        """获取字体（带缓存）

        Args:
            size: 字号（px）
            family: 字体族名 "industrial" | "default"
        """
        size = max(8, min(size, 200))
        key = (family, size)
        if key in FrameRenderer._font_cache:
            return FrameRenderer._font_cache[key]

        font = None
        paths = FrameRenderer._FONT_FAMILIES.get(family,
               FrameRenderer._FONT_FAMILIES["default"])

        # 项目相对路径 → 绝对路径
        import os
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        for fp in paths:
            abs_path = fp if os.path.isabs(fp) else os.path.join(base_dir, fp)
            try:
                font = ImageFont.truetype(abs_path, size)
                break
            except (OSError, IOError):
                continue

        if font is None:
            font = ImageFont.load_default()

        FrameRenderer._font_cache[key] = font
        return font

    @staticmethod
    def _draw_label_text(draw, text: str, x: int, y: int,
                         font, color: tuple, shadow: bool = True, shadow_alpha: int = 180):
        """绘制标签文字（统一风格：半透明，可选阴影）

        Args:
            draw: ImageDraw 对象
            text: 标签文字
            x, y: 绘制坐标
            font: 字体
            color: 主色 RGBA tuple（如 (255, 255, 255, 160)）
            shadow: 是否绘制文字阴影
        """
        label_color = (color[0], color[1], color[2], min(color[3], 180))
        label_shadow_alpha = max(0, min(255, int(round(shadow_alpha * 0.78))))
        FrameRenderer._draw_text_with_shadow(draw, text, x, y,
            font, label_color, shadow, label_shadow_alpha)

    @staticmethod
    def _draw_unit_text(draw, text: str, x: int, y: int,
                        font, color: tuple, shadow: bool = True, shadow_alpha: int = 180):
        """绘制单位文字（统一风格：更小半透明，可选阴影）

        Args:
            draw: ImageDraw 对象
            text: 单位文字
            x, y: 绘制坐标
            font: 字体
            color: 主色 RGBA tuple（如 (255, 255, 255, 140)）
            shadow: 是否绘制文字阴影
        """
        unit_color = (color[0], color[1], color[2], min(color[3], 160))
        unit_shadow_alpha = max(0, min(255, int(round(shadow_alpha * 0.67))))
        FrameRenderer._draw_text_with_shadow(draw, text, x, y,
            font, unit_color, shadow, unit_shadow_alpha)

    @staticmethod
    def _shadow_alpha(global_style: dict = None, default: int = 180) -> int:
        """读取并规范化全局文字阴影透明度。"""
        gstyle = global_style or {}
        raw = gstyle.get("text_shadow_alpha", default)
        try:
            alpha = int(raw)
        except (TypeError, ValueError):
            alpha = default
        return max(0, min(255, alpha))

    @staticmethod
    def _draw_text_with_shadow(draw, text: str, x: int, y: int,
                               font, color: tuple, shadow: bool = True,
                               shadow_alpha: int = 180):
        """统一绘制带阴影的文字。"""
        if shadow and shadow_alpha > 0:
            draw.text((x + 1, y + 1), text, font=font, fill=(0, 0, 0, shadow_alpha))
        draw.text((x, y), text, font=font, fill=color)

    @staticmethod
    def _parse_color(color_str: str, default=(255, 255, 255, 255)) -> tuple:
        """解析颜色字符串为 RGBA"""
        if not color_str:
            return default
        color_str = color_str.strip()
        try:
            if color_str.startswith("#"):
                hex_color = color_str[1:]
                if len(hex_color) == 8:
                    r, g, b, a = (int(hex_color[i:i+2], 16) for i in (0, 2, 4, 6))
                    return (r, g, b, a)
                elif len(hex_color) == 6:
                    r, g, b = (int(hex_color[i:i+2], 16) for i in (0, 2, 4))
                    return (r, g, b, 255)
                elif len(hex_color) == 3:
                    r, g, b = (int(c * 2, 16) for c in hex_color)
                    return (r, g, b, 255)
            elif color_str.startswith("rgba"):
                parts = color_str.strip("rgba()").split(",")
                r, g, b = int(parts[0]), int(parts[1]), int(parts[2])
                a = int(float(parts[3]) * 255) if len(parts) > 3 else 255
                return (r, g, b, a)
            elif color_str.startswith("rgb"):
                parts = color_str.strip("rgb()").split(",")
                r, g, b = int(parts[0]), int(parts[1]), int(parts[2])
                return (r, g, b, 255)
        except (ValueError, IndexError):
            pass
        return default

    @staticmethod
    def _draw_rounded_rect(draw, bbox, radius, fill):
        """绘制圆角矩形"""
        x0, y0, x1, y1 = bbox
        r = min(radius, (x1 - x0) // 2, (y1 - y0) // 2)
        # 四个角
        draw.pieslice([x0, y0, x0 + 2*r, y0 + 2*r], 180, 270, fill=fill)
        draw.pieslice([x1 - 2*r, y0, x1, y0 + 2*r], 270, 360, fill=fill)
        draw.pieslice([x0, y1 - 2*r, x0 + 2*r, y1], 90, 180, fill=fill)
        draw.pieslice([x1 - 2*r, y1 - 2*r, x1, y1], 0, 90, fill=fill)
        # 中间区域
        draw.rectangle([x0 + r, y0, x1 - r, y1], fill=fill)
        draw.rectangle([x0, y0 + r, x0 + r, y1 - r], fill=fill)
        draw.rectangle([x1 - r, y0 + r, x1, y1 - r], fill=fill)
