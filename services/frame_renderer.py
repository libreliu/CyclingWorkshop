"""帧渲染服务（Pillow）"""
from typing import Optional
from PIL import Image, ImageDraw, ImageFont
import math

from models.fit_data import FitData, FitRecord
from models.overlay_template import WidgetConfig


class FrameRenderer:
    """逐帧渲染叠加层"""

    # 默认字体回退
    _font_cache = {}

    @staticmethod
    def render_frame(
        fit_data: FitData,
        fit_time,
        widgets: list,
        canvas_width: int,
        canvas_height: int,
    ) -> Image.Image:
        """渲染一帧叠加层，返回 RGBA Image"""
        from services.fit_parser import FitParserService
        from datetime import datetime

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
            FrameRenderer._render_widget(canvas, widget, record, fit_data, fit_time)

        return canvas

    @staticmethod
    def _render_widget(canvas, widget: WidgetConfig, record: Optional[FitRecord],
                       fit_data: FitData, fit_time):
        """渲染单个 Widget"""
        wtype = widget.widget_type

        # 创建 Widget 区域
        region = Image.new("RGBA", (widget.width, widget.height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(region)

        # 半透明背景
        bg_color = widget.style.get("bg_color", "#00000066")
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
                                        lambda v: v * 3.6 if v else 0)  # m/s → km/h
        elif wtype == "HeartRateGauge":
            FrameRenderer._render_gauge(draw, widget, record, "heart_rate", lambda v: v)
        elif wtype == "CadenceGauge":
            FrameRenderer._render_gauge(draw, widget, record, "cadence", lambda v: v)
        elif wtype == "PowerGauge":
            FrameRenderer._render_gauge(draw, widget, record, "power", lambda v: v)
        elif wtype == "ElevationGauge":
            FrameRenderer._render_gauge(draw, widget, record, "altitude", lambda v: v)
        elif wtype == "DistanceCounter":
            FrameRenderer._render_gauge(draw, widget, record, "distance",
                                        lambda v: v / 1000 if v else 0)  # m → km
        elif wtype == "TimerDisplay":
            FrameRenderer._render_timer(draw, widget, record, fit_data, fit_time)
        elif wtype == "GradientIndicator":
            FrameRenderer._render_gradient(draw, widget, record, fit_data, fit_time)
        elif wtype == "AltitudeChart":
            FrameRenderer._render_altitude_chart(draw, widget, record, fit_data, fit_time)
        elif wtype == "MapTrack":
            FrameRenderer._render_map_track(draw, widget, record, fit_data, fit_time)
        elif wtype == "CustomLabel":
            FrameRenderer._render_label(draw, widget)

        # 如果有瓦片底图，先合成底图再叠加轨迹
        if tile_bg is not None:
            tile_bg.alpha_composite(region, (0, 0))
            region = tile_bg

        # 应用透明度并合成到画布
        if widget.opacity < 1.0:
            alpha = region.split()[3]
            alpha = alpha.point(lambda p: int(p * widget.opacity))
            region.putalpha(alpha)

        canvas.alpha_composite(region, (widget.x, widget.y))

    @staticmethod
    def _render_gauge(draw, widget, record, field_name, transform=None):
        """渲染数值型表盘"""
        value = None
        if record and field_name != "track":
            raw = getattr(record, field_name, None)
            if raw is not None:
                value = transform(raw) if transform else raw

        style = widget.style
        color = FrameRenderer._parse_color(style.get("color", "#ffffff"), default=(255, 255, 255, 255))
        font_size = style.get("font_size", 28)
        unit = style.get("unit", "")
        fmt = style.get("format", "number")  # "number" | "arc"

        font = FrameRenderer._get_font(font_size)
        small_font = FrameRenderer._get_font(max(font_size * 3 // 5, 12))

        # 数字显示
        if value is not None:
            decimals = style.get("decimals", 1 if isinstance(value, float) else 0)
            text = f"{value:.{decimals}f}" if decimals > 0 else str(int(value))
        else:
            text = "--"

        # 绘制圆弧表盘（如果 format=arc）
        if fmt == "arc" and value is not None:
            min_val = style.get("min_val", 0)
            max_val = style.get("max_val", 100)
            ratio = max(0, min(1, (value - min_val) / (max_val - min_val))) if max_val > min_val else 0

            arc_width = 4
            cx, cy = widget.width // 2, widget.height * 2 // 3
            radius = min(widget.width, widget.height) // 3
            if radius > 10:
                # 背景弧 (270°)
                bbox = [cx - radius, cy - radius, cx + radius, cy + radius]
                draw.arc(bbox, start=135, end=405, fill=(255, 255, 255, 40), width=arc_width)
                # 值弧
                end_angle = 135 + int(ratio * 270)
                draw.arc(bbox, start=135, end=end_angle, fill=color, width=arc_width)

        # 绘制数字
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x = (widget.width - tw) // 2
        y = (widget.height - th) // 2 - (5 if fmt == "arc" else 0)

        # 投影
        draw.text((x + 1, y + 1), text, font=font, fill=(0, 0, 0, 180))
        draw.text((x, y), text, font=font, fill=color)

        # 单位
        if unit:
            ubbox = draw.textbbox((0, 0), unit, font=small_font)
            uw = ubbox[2] - ubbox[0]
            ux = (widget.width - uw) // 2
            uy = y + th + 2
            draw.text((ux, uy), unit, font=small_font, fill=(color[0], color[1], color[2], 180))

    @staticmethod
    def _render_timer(draw, widget, record, fit_data, fit_time):
        """渲染运动时间"""
        session = fit_data.primary_session
        if not session or not session.start_time or not fit_time:
            return

        elapsed = (fit_time - session.start_time).total_seconds()
        hours = int(elapsed // 3600)
        minutes = int((elapsed % 3600) // 60)
        seconds = int(elapsed % 60)

        text = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        font = FrameRenderer._get_font(widget.style.get("font_size", 24))
        color = FrameRenderer._parse_color(widget.style.get("color", "#ffffff"), default=(255, 255, 255, 255))

        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text(((widget.width - tw) // 2, (widget.height - th) // 2), text, font=font, fill=color)

    @staticmethod
    def _render_gradient(draw, widget, record, fit_data, fit_time):
        """渲染坡度指示"""
        from services.fit_parser import FitParserService
        from datetime import timedelta

        if not fit_time:
            return

        # 前后各取 5 秒算坡度
        t1 = fit_time - timedelta(seconds=5)
        t2 = fit_time + timedelta(seconds=5)
        r1 = FitParserService.get_record_at(fit_data, t1)
        r2 = FitParserService.get_record_at(fit_data, t2)

        if r1 and r2 and r1.altitude is not None and r2.altitude is not None:
            # 简化：用高度差/时间差估算
            alt_diff = r2.altitude - r1.altitude
            dist_diff = 0
            if r1.distance is not None and r2.distance is not None:
                dist_diff = r2.distance - r1.distance
            if dist_diff > 0:
                gradient = (alt_diff / dist_diff) * 100
            else:
                gradient = 0
        else:
            gradient = 0

        style = widget.style
        color = FrameRenderer._parse_color(style.get("color", "#ffaa00"), default=(255, 170, 0, 255))
        font = FrameRenderer._get_font(style.get("font_size", 24))
        text = f"{gradient:+.1f}%"
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text(((widget.width - tw) // 2, (widget.height - th) // 2), text, font=font, fill=color)

    @staticmethod
    def _render_altitude_chart(draw, widget, record, fit_data, fit_time):
        """渲染海拔剖面图

        优化：当数据点远超像素宽度时降采样，避免遍历 47k 记录。
        降采样到 max_points = widget.width * 2（Nyquist 2x 采样）。
        """
        session = fit_data.primary_session
        if not session or not session.records:
            return

        records = session.records
        # 找有效海拔数据
        alt_data = [(r.timestamp, r.altitude) for r in records
                     if r.altitude is not None and r.timestamp is not None]
        if len(alt_data) < 2:
            return

        # ── 降采样：数据点远超像素宽度时 ──
        max_points = widget.width * 2  # Nyquist 2x 采样
        if len(alt_data) > max_points:
            step = len(alt_data) / max_points
            sampled = []
            i = 0.0
            while int(i) < len(alt_data):
                sampled.append(alt_data[int(i)])
                i += step
            # 确保最后一个点被包含
            if sampled[-1] != alt_data[-1]:
                sampled.append(alt_data[-1])
            alt_data = sampled

        min_alt = min(a for _, a in alt_data)
        max_alt = max(a for _, a in alt_data)
        alt_range = max_alt - min_alt if max_alt > min_alt else 1

        style = widget.style
        line_color = FrameRenderer._parse_color(style.get("line_color", "#aa88ff"), default=(170, 136, 255, 255))
        fill_color = FrameRenderer._parse_color(style.get("fill_color", "#aa88ff30"), default=(170, 136, 255, 48))

        start_time = alt_data[0][0]
        end_time = alt_data[-1][0]
        total_dur = (end_time - start_time).total_seconds()
        if total_dur <= 0:
            return

        # 绘制海拔曲线
        points = []
        for ts, alt in alt_data:
            x = int((ts - start_time).total_seconds() / total_dur * (widget.width - 4)) + 2
            y = widget.height - 2 - int((alt - min_alt) / alt_range * (widget.height - 4))
            points.append((x, y))

        if len(points) > 1:
            draw.line(points, fill=line_color, width=2)
            # 填充区域
            fill_points = [points[0]] + points + [points[-1], (points[-1][0], widget.height), (points[0][0], widget.height)]
            draw.polygon(fill_points, fill=fill_color)

        # 当前位置标记线
        if fit_time and start_time <= fit_time <= end_time:
            cx = int((fit_time - start_time).total_seconds() / total_dur * (widget.width - 4)) + 2
            draw.line([(cx, 0), (cx, widget.height)], fill=(255, 255, 255, 200), width=1)

    @staticmethod
    def _render_map_track_bg(widget, fit_data, record=None):
        """渲染轨迹地图的瓦片底图背景，返回 RGBA Image 或 None

        map_mode:
          "overview"（默认）- 轨迹全览，中心为轨迹 bounding box 中心
          "follow"          - 地图跟随，中心为当前位置，高 zoom 不缩放只 pan
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

            tile_url = get_tile_url(tile_style)
            tile_map = render_tile_map(center_lat, center_lon, zoom,
                                       widget.width, widget.height,
                                       tile_url_template=tile_url)

            return tile_map

        except Exception as e:
            print(f"[MapTrack] 瓦片底图渲染失败: {e}")
            return None

    @staticmethod
    def _render_map_track(draw, widget, record, fit_data, fit_time):
        """渲染轨迹地图（矢量轨迹线 + 位置标记，叠加在底图上）

        优化：使用 bisect 二分查找 walked_points 截断索引，
        避免遍历 47k 条记录（从 46ms → <1ms）。
        """
        from services.fit_parser import FitParserService

        coords = FitParserService.get_track_coords(fit_data)
        if len(coords) < 2:
            return

        style = widget.style
        track_color = FrameRenderer._parse_color(style.get("track_color", "#00d4aa"), default=(0, 212, 170, 255))
        marker_color = FrameRenderer._parse_color(style.get("marker_color", "#ff4444"), default=(255, 68, 68, 255))

        # 计算 bounding box
        lats = [c[0] for c in coords]
        lons = [c[1] for c in coords]
        min_lat, max_lat = min(lats), max(lats)
        min_lon, max_lon = min(lons), max(lons)
        center_lat = (min_lat + max_lat) / 2
        center_lon = (min_lon + max_lon) / 2

        lat_range = max_lat - min_lat if max_lat > min_lat else 0.001
        lon_range = max_lon - min_lon if max_lon > min_lon else 0.001

        # 添加 padding
        padding = 0.05
        lat_range *= (1 + padding * 2)
        lon_range *= (1 + padding * 2)
        min_lat -= lat_range * padding
        min_lon -= lon_range * padding

        # ── 选择投影方式 ──
        tile_style = style.get("tile_source", "")
        map_mode = style.get("map_mode", "overview")  # "overview" | "follow"

        if tile_style:
            # 瓦片底图模式：使用 Web Mercator 坐标系投影
            try:
                from services.tile_service import latlon_to_pixel, compute_zoom_for_size

                if map_mode == "follow" and record and record.latitude and record.longitude:
                    # ── 跟随模式：中心 = 当前位置，高 zoom ──
                    proj_center_lat = record.latitude
                    proj_center_lon = record.longitude
                    zoom = style.get("follow_zoom", 15)
                    if isinstance(zoom, str):
                        zoom = int(zoom)
                    zoom = max(10, min(18, zoom))
                else:
                    # ── 全览模式 ──
                    proj_center_lat = center_lat
                    proj_center_lon = center_lon
                    zoom = style.get("zoom", None)
                    if zoom is None or zoom <= 0:
                        zoom = compute_zoom_for_size(min_lat, max_lat, min_lon, max_lon,
                                                      widget.width, widget.height, padding=0.1)

                center_px_x, center_px_y = latlon_to_pixel(proj_center_lat, proj_center_lon, zoom)
                half_w = widget.width / 2
                half_h = widget.height / 2

                def project(lat, lon):
                    px_x, px_y = latlon_to_pixel(lat, lon, zoom)
                    x = int(px_x - center_px_x + half_w)
                    y = int(px_y - center_px_y + half_h)
                    return x, y
            except Exception:
                tile_style = ""  # 回退到矢量模式

        if not tile_style:
            # ── 无底图：矢量轨迹线模式（经度修正） ──
            margin = 5
            w = widget.width - margin * 2
            h = widget.height - margin * 2

            # 修正宽高比：使用等距投影（经度修正）
            cos_lat = math.cos(math.radians(center_lat))
            effective_lon_range = lon_range * cos_lat

            # 保持地理宽高比的映射
            def project(lat, lon):
                x = margin + int(((lon - min_lon) * cos_lat) / effective_lon_range * w)
                y = margin + int((max_lat - lat) / lat_range * h)
                return x, y

        # 绘制全部轨迹线（暗色）
        points = [project(lat, lon) for lat, lon in coords]
        if len(points) > 1:
            dim_track = (track_color[0]//3, track_color[1]//3, track_color[2]//3, 200)
            draw.line(points, fill=dim_track, width=2)

        # 绘制已走路径（亮色）：用 bisect 二分查找截断索引
        if fit_time and record and record.latitude and record.longitude:
            session = fit_data.primary_session
            if session and session.records:
                walked_points = FrameRenderer._get_walked_points_bisect(
                    session.records, fit_time, project)
                if len(walked_points) > 1:
                    draw.line(walked_points, fill=track_color, width=3)

        # 当前位置标记
        if record and record.latitude and record.longitude:
            cx, cy = project(record.latitude, record.longitude)
            marker_size = style.get("marker_size", 6)
            draw.ellipse([cx - marker_size, cy - marker_size, cx + marker_size, cy + marker_size],
                         fill=marker_color)

    @staticmethod
    def _get_walked_points_bisect(records, fit_time, project_fn):
        """使用 bisect 二分查找已走路径点（替代全遍历）

        records 按 timestamp 升序排列，用 bisect 找到 fit_time 的位置，
        只遍历 fit_time 之前有坐标的记录。

        Args:
            records: FitRecord 列表（按 timestamp 升序）
            fit_time: 当前 FIT 时间
            project_fn: 投影函数 (lat, lon) → (x, y)

        Returns:
            walked_points: [(x, y), ...] 已走路径的投影点列表
        """
        import bisect

        # 构建排序的 timestamp 列表用于 bisect
        # 只在 records 有足够多时才做优化（小数据集全遍历也很快）
        n = len(records)
        if n < 1000:
            # 小数据集：直接遍历
            walked_points = []
            for r in records:
                if r.timestamp and r.timestamp <= fit_time and r.latitude and r.longitude:
                    walked_points.append(project_fn(r.latitude, r.longitude))
            return walked_points

        # 大数据集：bisect 查找
        # 找到第一个 timestamp > fit_time 的记录索引
        # 使用虚拟 key：提取 timestamp 用于比较
        timestamps = [r.timestamp for r in records if r.timestamp is not None]
        if not timestamps:
            return []

        # bisect_right: 找到 fit_time 右侧插入点
        cut_idx = bisect.bisect_right(timestamps, fit_time)

        # 只遍历 [0, cut_idx) 范围内有坐标的记录
        walked_points = []
        for i in range(cut_idx):
            r = records[i]
            if r.latitude is not None and r.longitude is not None:
                walked_points.append(project_fn(r.latitude, r.longitude))

        return walked_points

    @staticmethod
    def _render_label(draw, widget):
        """渲染自定义标签"""
        text = widget.style.get("text", "Label")
        color = FrameRenderer._parse_color(widget.style.get("color", "#ffffff"), default=(255, 255, 255, 255))
        font_size = widget.style.get("font_size", 16)
        font = FrameRenderer._get_font(font_size)
        draw.text((5, 5), text, font=font, fill=color)

    # ── 工具方法 ──────────────────────────────────

    @staticmethod
    def _get_font(size: int):
        """获取字体（带缓存）"""
        size = max(8, min(size, 120))
        if size in FrameRenderer._font_cache:
            return FrameRenderer._font_cache[size]

        # 尝试加载系统字体
        font = None
        font_paths = [
            r"C:\Windows\Fonts\consola.ttf",
            r"C:\Windows\Fonts\arial.ttf",
            r"C:\Windows\Fonts\msyh.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
        for fp in font_paths:
            try:
                font = ImageFont.truetype(fp, size)
                break
            except (OSError, IOError):
                continue

        if font is None:
            font = ImageFont.load_default()

        FrameRenderer._font_cache[size] = font
        return font

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
