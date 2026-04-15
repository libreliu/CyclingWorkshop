"""瓦片底图管理 API"""
import io
from flask import Blueprint, request, jsonify, send_file, Response
from services.tile_service import (
    set_proxy_config, get_proxy_config, get_cache_stats, clear_cache,
    TileDownloadProgress, preload_tiles_for_fit, TILE_URLS, TILE_NAMES,
    compute_tile_urls_for_region, compute_zoom_for_size,
    resolve_tile_url, get_cached_tile, download_tile,
    get_cache_inventory, get_cache_tiles_for_region,
)

tiles_bp = Blueprint("tiles", __name__)


def _get_fit_cache():
    """获取 FIT 数据缓存（从 fit blueprint）"""
    from api.fit import _fit_cache
    return _fit_cache


@tiles_bp.route("/styles", methods=["GET"])
def get_tile_styles():
    """获取所有瓦片底图样式"""
    styles = []
    for key, url in TILE_URLS.items():
        styles.append({
            "id": key,
            "name": TILE_NAMES.get(key, key),
            "url_template": url,
        })
    return jsonify(styles)


@tiles_bp.route("/cache/stats", methods=["GET"])
def cache_stats():
    """获取瓦片缓存统计"""
    return jsonify(get_cache_stats())


@tiles_bp.route("/cache/clear", methods=["POST"])
def cache_clear():
    """清除所有瓦片缓存"""
    count = clear_cache()
    return jsonify({"deleted": count})


@tiles_bp.route("/proxy", methods=["GET"])
def get_proxy():
    """获取代理配置"""
    cfg = get_proxy_config()
    # 隐藏密码
    safe_cfg = dict(cfg)
    if safe_cfg.get("password"):
        safe_cfg["password"] = "********"
    return jsonify(safe_cfg)


@tiles_bp.route("/proxy", methods=["POST"])
def set_proxy():
    """设置代理配置"""
    data = request.get_json(force=True)
    enabled = data.get("enabled", False)
    proxy_type = data.get("type", "http")
    host = data.get("host", "")
    port = data.get("port", 0)
    username = data.get("username", "")
    password = data.get("password", "")

    # 验证
    if proxy_type not in ("http", "socks5"):
        return jsonify({"error": "不支持的代理类型，仅支持 http/socks5"}), 400

    if enabled and (not host or not port):
        return jsonify({"error": "启用代理时，主机和端口不能为空"}), 400

    # 保留旧密码（如果新密码是掩码）
    old_cfg = get_proxy_config()
    if password == "********" or password == "":
        password = old_cfg.get("password", "")

    set_proxy_config(
        enabled=enabled,
        proxy_type=proxy_type,
        host=host,
        port=int(port),
        username=username,
        password=password,
    )
    return jsonify({"ok": True})


@tiles_bp.route("/proxy/test", methods=["POST"])
def test_proxy():
    """测试代理连接"""
    import time
    from services.tile_service import _build_opener, download_tile

    cfg = get_proxy_config()
    if not cfg["enabled"]:
        return jsonify({"ok": False, "error": "代理未启用"})

    try:
        start = time.time()
        # 尝试下载一个 OSM 瓦片来测试代理
        tile_url = "https://tile.openstreetmap.org/0/0/0.png"
        img = download_tile(tile_url, timeout=15)
        elapsed = time.time() - start

        # 检查返回的瓦片是否是有效的 256x256
        w, h = img.size
        is_valid = (w == 256 and h == 256)

        return jsonify({
            "ok": is_valid,
            "elapsed_ms": round(elapsed * 1000),
            "tile_size": f"{w}x{h}",
            "error": "" if is_valid else "下载的瓦片尺寸不正确，可能代理返回了错误页面",
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@tiles_bp.route("/preload", methods=["POST"])
def preload_tiles():
    """为 FIT 轨迹预下载瓦片"""
    data = request.get_json(force=True)
    fit_id = data.get("fit_id")
    tile_style = data.get("tile_style", "carto_dark")
    zoom = data.get("zoom", 0)
    width = data.get("width", 400)
    height = data.get("height", 300)
    task_id = data.get("task_id", "")

    if not fit_id:
        return jsonify({"error": "缺少 fit_id"}), 400

    # 从 fit 缓存获取 FitData 对象
    fit_cache = _get_fit_cache()
    fit_data = fit_cache.get(fit_id)
    if not fit_data:
        return jsonify({"error": "FIT 数据未找到，请先在步骤①加载 FIT 文件"}), 400

    result = preload_tiles_for_fit(fit_data, fit_id=fit_id, tile_style=tile_style, zoom=zoom, width=width, height=height, task_id=task_id)
    if "error" in result:
        return jsonify(result), 400

    return jsonify(result)


@tiles_bp.route("/preload/region", methods=["POST"])
def preload_region():
    """为指定区域预下载瓦片"""
    data = request.get_json(force=True)
    min_lat = data.get("min_lat")
    max_lat = data.get("max_lat")
    min_lon = data.get("min_lon")
    max_lon = data.get("max_lon")
    zoom = data.get("zoom", 14)
    tile_style = data.get("tile_style", "carto_dark")
    task_id = data.get("task_id", "")

    if None in (min_lat, max_lat, min_lon, max_lon):
        return jsonify({"error": "缺少区域参数"}), 400

    urls = compute_tile_urls_for_region(min_lat, max_lat, min_lon, max_lon, zoom, tile_style)

    if not task_id:
        import time
        task_id = f"region_{tile_style}_z{zoom}_{int(time.time()*1000)}"

    # 异步下载
    import threading
    from services.tile_service import download_tiles_batch

    def _async_download():
        download_tiles_batch(urls, task_id=task_id)

    t = threading.Thread(target=_async_download, daemon=True)
    t.start()

    return jsonify({
        "task_id": task_id,
        "total_tiles": len(urls),
        "style": tile_style,
        "style_name": TILE_NAMES.get(tile_style, tile_style),
        "zoom": zoom,
    })


@tiles_bp.route("/progress/<task_id>", methods=["GET"])
def get_progress(task_id):
    """获取下载任务进度"""
    progress = TileDownloadProgress.get_instance()
    task = progress.get_task(task_id)
    if not task:
        return jsonify({"error": "任务未找到"}), 404
    return jsonify(task)


@tiles_bp.route("/progress", methods=["GET"])
def get_all_progress():
    """获取所有下载任务"""
    progress = TileDownloadProgress.get_instance()
    return jsonify(progress.get_all_tasks())


@tiles_bp.route("/progress/<task_id>/cancel", methods=["POST"])
def cancel_progress(task_id):
    """取消下载任务"""
    progress = TileDownloadProgress.get_instance()
    progress.cancel_task(task_id)
    return jsonify({"ok": True})


# ── 瓦片代理 API（Leaflet 通过此接口获取瓦片，自动应用代理和缓存） ──────


@tiles_bp.route("/map/<style>/<int:z>/<int:x>/<int:y>.png", methods=["GET"])
def proxy_tile(style, z, x, y):
    """瓦片代理：Leaflet 请求此接口获取瓦片图片

    1. 先查缓存，命中则直接返回
    2. 未命中则下载（应用代理），写入缓存，返回
    3. 返回 PNG 图片，带 7 天客户端缓存头
    """
    if style not in TILE_URLS:
        return jsonify({"error": f"未知样式: {style}"}), 404

    # 限制 zoom 范围
    if z < 0 or z > 20:
        return jsonify({"error": "zoom 超出范围"}), 400

    # 1. 查缓存
    cached = get_cached_tile(style, z, x, y)
    if cached:
        data, content_type = cached
        resp = Response(data, mimetype=content_type)
        resp.headers["Cache-Control"] = "public, max-age=604800"  # 7 天
        resp.headers["X-Tile-Cache"] = "HIT"
        return resp

    # 2. 下载
    try:
        url = resolve_tile_url(style, z, x, y)
        img = download_tile(url, timeout=2)

        # 保存到字节流
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        data = buf.getvalue()

        resp = Response(data, mimetype="image/png")
        resp.headers["Cache-Control"] = "public, max-age=604800"
        resp.headers["X-Tile-Cache"] = "MISS"
        return resp

    except Exception as e:
        # 返回 1x1 透明 PNG
        import struct
        transparent_png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
            b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
            b"\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        resp = Response(transparent_png, mimetype="image/png")
        resp.headers["X-Tile-Error"] = str(e)[:200]
        return resp


# ── 缓存可视化 API ──────────────────────────────────────────


@tiles_bp.route("/cache/inventory", methods=["GET"])
def cache_inventory():
    """获取缓存瓦片清单（按样式/zoom 分组统计）"""
    return jsonify(get_cache_inventory())


@tiles_bp.route("/cache/region", methods=["GET"])
def cache_region():
    """查询指定区域内瓦片缓存状态

    Query params: min_lat, max_lat, min_lon, max_lon, zoom
    """
    try:
        min_lat = float(request.args.get("min_lat", 0))
        max_lat = float(request.args.get("max_lat", 0))
        min_lon = float(request.args.get("min_lon", 0))
        max_lon = float(request.args.get("max_lon", 0))
        zoom = int(request.args.get("zoom", 14))
    except (ValueError, TypeError):
        return jsonify({"error": "参数格式错误"}), 400

    result = get_cache_tiles_for_region(min_lat, max_lat, min_lon, max_lon, zoom)
    return jsonify(result)


@tiles_bp.route("/cache/region/download", methods=["POST"])
def cache_region_download():
    """下载指定区域瓦片（通过区域选择框触发）

    Body: {min_lat, max_lat, min_lon, max_lon, zoom, tile_style}
    """
    data = request.get_json(force=True)
    min_lat = data.get("min_lat")
    max_lat = data.get("max_lat")
    min_lon = data.get("min_lon")
    max_lon = data.get("max_lon")
    zoom = data.get("zoom", 14)
    tile_style = data.get("tile_style", "carto_dark")

    if None in (min_lat, max_lat, min_lon, max_lon):
        return jsonify({"error": "缺少区域参数"}), 400

    # 如果 zoom=0，自动计算
    if zoom <= 0:
        zoom = compute_zoom_for_size(min_lat, max_lat, min_lon, max_lon, 400, 300)

    urls = compute_tile_urls_for_region(min_lat, max_lat, min_lon, max_lon, zoom, tile_style)

    import time
    import threading
    from services.tile_service import download_tiles_batch

    task_id = f"region_{tile_style}_z{zoom}_{int(time.time()*1000)}"

    def _async_download():
        download_tiles_batch(urls, task_id=task_id)

    t = threading.Thread(target=_async_download, daemon=True)
    t.start()

    return jsonify({
        "task_id": task_id,
        "total_tiles": len(urls),
        "style": tile_style,
        "style_name": TILE_NAMES.get(tile_style, tile_style),
        "zoom": zoom,
        "region": {
            "min_lat": min_lat, "max_lat": max_lat,
            "min_lon": min_lon, "max_lon": max_lon,
        },
    })
