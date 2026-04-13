"""FIT 数据 API"""
from flask import Blueprint, request, jsonify, send_file
import io
import os

from models.fit_data import SanitizeConfig, SmoothingConfig
from services.fit_parser import (
    FitParserService, FitSanitize, FitFilter, detect_outliers_compat,
)

fit_bp = Blueprint("fit", __name__)

# 内存缓存：path → FitData
_fit_cache = {}


def _get_or_parse(path: str):
    """获取或解析 FIT 数据"""
    if path in _fit_cache:
        return _fit_cache[path]
    if not os.path.isfile(path):
        return None
    data = FitParserService.parse(path)
    if data:
        _fit_cache[path] = data
    return data


@fit_bp.route("/load", methods=["POST"])
def load_fit():
    """通过本地路径加载 FIT 文件"""
    body = request.get_json(silent=True) or {}
    path = body.get("path", "").strip().strip('"').strip("'")

    if not path:
        return jsonify({"error": "请提供 FIT 文件路径"}), 400
    if not os.path.isfile(path):
        return jsonify({"error": f"文件不存在: {path}"}), 404

    data = _get_or_parse(path)
    if data is None:
        return jsonify({"error": f"FIT 文件解析失败: {path}"}), 500

    return jsonify({
        "id": path,  # 用路径做 id
        "summary": data.to_dict(),
    })


@fit_bp.route("/<path:fit_id>/summary", methods=["GET"])
def fit_summary(fit_id):
    """获取 FIT 摘要"""
    data = _fit_cache.get(fit_id)
    if not data:
        return jsonify({"error": "未找到，请先加载"}), 404
    return jsonify(data.to_dict())


@fit_bp.route("/<path:fit_id>/records", methods=["GET"])
def fit_records(fit_id):
    """获取 FIT 时间序列数据（支持时间范围过滤 + 降采样）"""
    data = _fit_cache.get(fit_id)
    if not data:
        return jsonify({"error": "未找到，请先加载"}), 404

    start = request.args.get("start", type=float)
    end = request.args.get("end", type=float)
    max_points = request.args.get("max_points", 2000, type=int)

    session = data.primary_session
    if not session:
        return jsonify({"records": []})

    # 先过滤时间范围
    filtered = []
    for r in session.records:
        ts = r.timestamp
        if ts is None:
            continue
        epoch = ts.timestamp()
        if start is not None and epoch < start:
            continue
        if end is not None and epoch > end:
            continue
        filtered.append(r)

    # 降采样
    total = len(filtered)
    if total > max_points:
        step = total / max_points
        sampled = []
        i = 0.0
        while int(i) < total:
            sampled.append(filtered[int(i)])
            i += step
        if sampled[-1] != filtered[-1]:
            sampled.append(filtered[-1])
        filtered = sampled

    records = [r.to_dict() for r in filtered]
    return jsonify({"records": records, "count": len(records), "total": total})


@fit_bp.route("/<path:fit_id>/track", methods=["GET"])
def fit_track(fit_id):
    """获取轨迹 GeoJSON（每个点包含 FitRecord 信息）

    Query params:
        filter_glitches: 是否过滤 GPS glitch 点（默认 true）
        max_points: 最大点数（默认 5000，超过则等间隔采样）
        include_props: 是否在 properties 中包含完整记录信息（默认 true）
        include_points: 是否返回 Point features（默认 true，设 false 则仅返回 LineString）
    """
    data = _fit_cache.get(fit_id)
    if not data:
        return jsonify({"error": "未找到，请先加载"}), 404

    filter_glitches = request.args.get("filter_glitches", "true").lower() != "false"
    max_points = request.args.get("max_points", 5000, type=int)
    include_props = request.args.get("include_props", "true").lower() != "false"
    include_points = request.args.get("include_points", "true").lower() != "false"

    session = data.primary_session
    if not session or not session.records:
        return jsonify({"type": "FeatureCollection", "features": []})

    records = session.records

    # 获取 GPS glitch 索引（使用缓存）
    glitch_indices = set()
    glitch_result = None
    if filter_glitches:
        glitch_result = data.get_glitch_cache()
        glitch_indices = set(glitch_result["glitch_indices"])

    # 收集有效坐标点
    valid_points = []
    for i, r in enumerate(records):
        if r.latitude is not None and r.longitude is not None and i not in glitch_indices:
            valid_points.append((i, r))

    # 降采样
    total_valid = len(valid_points)
    if total_valid > max_points:
        step = total_valid / max_points
        sampled = []
        j = 0.0
        while int(j) < total_valid:
            sampled.append(valid_points[int(j)])
            j += step
        # 确保首尾点
        if sampled[0] != valid_points[0]:
            sampled.insert(0, valid_points[0])
        if sampled[-1] != valid_points[-1]:
            sampled.append(valid_points[-1])
        valid_points = sampled

    # 构建 GeoJSON FeatureCollection
    line_coords = []
    point_features = []

    for orig_idx, r in valid_points:
        line_coords.append([r.longitude, r.latitude])

        if include_points and include_props:
            props = {
                "index": orig_idx,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            }
            for field_name in ("altitude", "heart_rate", "cadence",
                               "speed", "distance", "power", "temperature"):
                v = getattr(r, field_name, None)
                if v is not None:
                    props[field_name] = v
                    if field_name == "speed":
                        props["speed_kmh"] = round(v * 3.6, 1)

            point_features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [r.longitude, r.latitude],
                },
                "properties": props,
            })

    geojson = {
        "type": "FeatureCollection",
        "features": [
            # LineString 用于绘制轨迹线
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": line_coords,
                },
                "properties": {
                    "point_count": len(valid_points),
                    "total_valid": total_valid,
                },
            }
        ] + point_features,
    }

    # GPS glitch 信息（复用上方已计算的结果）
    if filter_glitches and glitch_indices:
        geojson["gps_glitch"] = glitch_result

    return jsonify(geojson)


@fit_bp.route("/<path:fit_id>/track_aspect", methods=["GET"])
def fit_track_aspect(fit_id):
    """获取轨迹的地理宽高比，用于 MapTrack Widget 的自适应宽高

    返回: { lat_range, lon_range, aspect_ratio, min_lat, max_lat, min_lon, max_lon, center_lat, center_lon }
    aspect_ratio = lon_range / (lat_range * cos(center_lat))，即经度修正后的水平/垂直比
    """
    data = _fit_cache.get(fit_id)
    if not data:
        return jsonify({"error": "未找到，请先加载"}), 404

    coords = FitParserService.get_track_coords(data, filter_glitches=True)
    if len(coords) < 2:
        return jsonify({"aspect_ratio": 1.0, "lat_range": 0, "lon_range": 0,
                         "min_lat": 0, "max_lat": 0, "min_lon": 0, "max_lon": 0,
                         "center_lat": 0, "center_lon": 0})

    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)

    lat_range = max_lat - min_lat if max_lat > min_lat else 0.001
    lon_range = max_lon - min_lon if max_lon > min_lon else 0.001

    center_lat = (min_lat + max_lat) / 2
    center_lon = (min_lon + max_lon) / 2

    # 经度修正：在给定纬度下，1度经度对应的实际距离 = 1度纬度 * cos(lat)
    import math
    cos_lat = math.cos(math.radians(center_lat))
    effective_lon_range = lon_range * cos_lat
    aspect_ratio = effective_lon_range / lat_range if lat_range > 0 else 1.0

    return jsonify({
        "aspect_ratio": round(aspect_ratio, 4),
        "lat_range": round(lat_range, 6),
        "lon_range": round(lon_range, 6),
        "min_lat": round(min_lat, 6), "max_lat": round(max_lat, 6),
        "min_lon": round(min_lon, 6), "max_lon": round(max_lon, 6),
        "center_lat": round(center_lat, 6),
        "center_lon": round(center_lon, 6),
    })


@fit_bp.route("/<path:fit_id>/outliers", methods=["GET"])
def fit_outliers(fit_id):
    """检测异常值（兼容旧 API）

    Query params:
        fields: 逗号分隔的字段列表（可选，默认所有可用字段）
        sigma: Z-score 阈值（默认 3.0）
    """
    data = _fit_cache.get(fit_id)
    if not data:
        return jsonify({"error": "未找到，请先加载"}), 404

    fields_str = request.args.get("fields", "")
    fields = [f.strip() for f in fields_str.split(",") if f.strip()] or None
    sigma = request.args.get("sigma", 3.0, type=float)

    result = detect_outliers_compat(data, fields=fields, sigma=sigma)
    return jsonify(result)


@fit_bp.route("/<path:fit_id>/gps_glitch", methods=["GET"])
def fit_gps_glitch(fit_id):
    """检测 GPS glitch 点

    Query params:
        max_speed: 速度阈值 m/s（默认 55.0，≈200 km/h）
    """
    data = _fit_cache.get(fit_id)
    if not data:
        return jsonify({"error": "未找到，请先加载"}), 404

    max_speed = request.args.get("max_speed", 55.0, type=float)
    result = data.get_glitch_cache(max_speed_ms=max_speed)
    return jsonify(result)


# ── 新 API：数据清洗 ──────────────────────────────────

@fit_bp.route("/<path:fit_id>/sanitize", methods=["POST"])
def fit_sanitize(fit_id):
    """数据清洗：根据规则丢弃不合理记录

    Body (可选，不提供则使用默认配置):
    {
        "gps_filter_glitches": true,
        "gps_max_speed_ms": 55.0,
        "gps_out_of_range": true,
        "hr_range": [30, 250],
        "hr_max_rate": 30.0,
        "hr_enable_rate_check": true,
        "speed_range": [0, 55],
        "speed_max_accel": 10.0,
        "speed_enable_accel_check": true,
        "altitude_range": [-500, 9000],
        "cadence_range": [0, 250],
        "power_range": [0, 2500],
        "temperature_range": [-40, 60]
    }
    """
    data = _fit_cache.get(fit_id)
    if not data:
        return jsonify({"error": "未找到，请先加载"}), 404

    body = request.get_json(silent=True) or {}
    config = SanitizeConfig.from_dict(body)

    result = FitSanitize.sanitize(data, config)
    # 更新缓存（原地修改了 data）
    _fit_cache[fit_id] = data
    data.invalidate_glitch_cache()

    return jsonify({
        "message": "数据清洗完成",
        "result": result,
        "summary": data.to_dict(),
    })


# ── 新 API：平滑滤波 ──────────────────────────────────

@fit_bp.route("/<path:fit_id>/smooth", methods=["POST"])
def fit_smooth(fit_id):
    """平滑滤波：窗函数平滑（不移除记录）

    Body:
    {
        "enabled": true,
        "fields": ["heart_rate", "speed", "altitude"],
        "method": "moving_avg",  // median | moving_avg | gaussian
        "window_size": 5
    }
    """
    data = _fit_cache.get(fit_id)
    if not data:
        return jsonify({"error": "未找到，请先加载"}), 404

    body = request.get_json(silent=True) or {}
    config = SmoothingConfig.from_dict(body)
    if not config.fields:
        return jsonify({"error": "请指定要平滑的字段"}), 400

    result = FitFilter.smooth(data, config)
    _fit_cache[fit_id] = data
    data.invalidate_glitch_cache()

    return jsonify({
        "message": "平滑滤波完成",
        "result": result,
        "summary": data.to_dict(),
    })


# ── 旧 API：滤波（兼容） ──────────────────────────────

@fit_bp.route("/<path:fit_id>/filter", methods=["POST"])
def fit_filter(fit_id):
    """对 FIT 数据应用滤波（兼容旧 API）

    Body:
    {
        "fields": ["heart_rate", "speed"],
        "method": "median",       // median | moving_avg | remove_outliers
        "window_size": 5,
        "sigma": 3.0,
        "fill": "interpolate"     // interpolate | remove
    }
    """
    data = _fit_cache.get(fit_id)
    if not data:
        return jsonify({"error": "未找到，请先加载"}), 404

    body = request.get_json(silent=True) or {}
    filter_config = {
        "fields": body.get("fields", []),
        "method": body.get("method", "median"),
        "window_size": body.get("window_size", 5),
        "sigma": body.get("sigma", 3.0),
        "fill": body.get("fill", "interpolate"),
    }

    if not filter_config["fields"]:
        return jsonify({"error": "请指定要滤波的字段"}), 400

    new_data = FitFilter.apply_filter(data, filter_config)
    _fit_cache[fit_id] = new_data

    return jsonify({
        "message": "滤波已应用",
        "summary": new_data.to_dict(),
    })


@fit_bp.route("/<path:fit_id>/reset", methods=["POST"])
def fit_reset(fit_id):
    """重新从文件解析（重置滤波等修改）"""
    if fit_id not in _fit_cache:
        return jsonify({"error": "未找到，请先加载"}), 404

    data = FitParserService.parse(fit_id)
    if data is None:
        return jsonify({"error": "重新解析失败"}), 500

    _fit_cache[fit_id] = data
    return jsonify({
        "message": "已重置为原始数据",
        "summary": data.to_dict(),
    })


@fit_bp.route("/<path:fit_id>/export_gpx", methods=["POST"])
def fit_export_gpx(fit_id):
    """导出 GPX 文件

    Body: { "output_path": "C:\\path\\to\\output.gpx" }
    如果不提供 output_path，则直接返回 GPX 内容
    """
    data = _fit_cache.get(fit_id)
    if not data:
        return jsonify({"error": "未找到，请先加载"}), 404

    body = request.get_json(silent=True) or {}
    output_path = body.get("output_path", "").strip()

    try:
        if output_path:
            result_path = FitParserService.export_gpx(data, output_path)
            return jsonify({"message": "GPX 导出成功", "path": result_path})
        else:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".gpx", delete=False, mode="w",
                                              encoding="utf-8") as f:
                tmp_path = f.name
            FitParserService.export_gpx(data, tmp_path)
            return send_file(tmp_path, mimetype="application/gpx+xml",
                             as_attachment=True, download_name="track.gpx")
    except Exception as e:
        return jsonify({"error": f"GPX 导出失败: {str(e)}"}), 500
