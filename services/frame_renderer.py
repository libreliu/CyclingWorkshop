"""帧渲染服务（Pillow）"""
from typing import Optional
from PIL import Image, ImageDraw, ImageFont
import math
import numpy as np

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
                center_px_x, center_px_y = latlon_to_pixel(proj_center_lat, proj_center_lon, zoom)
                half_w = widget.width / 2
                half_h = widget.height / 2

                n = 2.0 ** zoom
                px_x = (lons + 180.0) / 360.0 * n * 256
                lat_rad = np.radians(lats)
                px_y = (1.0 - np.log(np.tan(lat_rad) + 1.0 / np.cos(lat_rad)) / np.pi) / 2.0 * n * 256
                all_x = (px_x - center_px_x + half_w).astype(np.int32)
                all_y = (px_y - center_px_y + half_h).astype(np.int32)

                # 投影单个坐标（用于当前位置标记等少量点）
                def project_single(lat, lon):
                    sx, sy = latlon_to_pixel(lat, lon, zoom)
                    return int(sx - center_px_x + half_w), int(sy - center_px_y + half_h)

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
            draw.line(all_points, fill=dim_track, width=2)

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
                            draw.line(seg_points, fill=seg_color, width=3)

        # ── 当前位置标记 ──
        if record and record.latitude and record.longitude:
            cx, cy = project_single(record.latitude, record.longitude)
            marker_size = style.get("marker_size", 6)
            draw.ellipse([cx - marker_size, cy - marker_size, cx + marker_size, cy + marker_size],
                         fill=marker_color)

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
