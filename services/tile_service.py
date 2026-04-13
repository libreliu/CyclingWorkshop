"""瓦片底图下载和缓存服务（支持代理 + 进度追踪）"""
import io
import math
import os
import hashlib
import urllib.request
import threading
import time
from PIL import Image
from typing import Optional, Dict, Any

# 瓦片缓存目录
TILE_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tile_cache")

# ── 代理配置（全局） ──────────────────────────────
_proxy_config: Dict[str, Any] = {
    "enabled": False,
    "type": "http",          # "http" | "socks5"
    "host": "",
    "port": 0,
    "username": "",
    "password": "",
}
_proxy_lock = threading.Lock()


def set_proxy_config(enabled=False, proxy_type="http", host="", port=0,
                     username="", password=""):
    """设置代理配置"""
    with _proxy_lock:
        _proxy_config["enabled"] = enabled
        _proxy_config["type"] = proxy_type
        _proxy_config["host"] = host
        _proxy_config["port"] = port
        _proxy_config["username"] = username
        _proxy_config["password"] = password


def get_proxy_config():
    """获取代理配置"""
    with _proxy_lock:
        return dict(_proxy_config)


def _build_opener():
    """根据代理配置构建 urllib opener（支持 HTTP/HTTPS 和 SOCKS5 代理）"""
    cfg = get_proxy_config()
    if not cfg["enabled"] or not cfg["host"] or not cfg["port"]:
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))

    auth_str = ""
    if cfg["username"]:
        auth_str = f"{cfg['username']}:{cfg['password']}@"

    if cfg["type"] == "socks5":
        # SOCKS5 代理：使用 PySocks 库（如果可用）
        try:
            import socks as socksmod  # noqa: PySocks
            import socket as socketmod

            # 保存原始 socket 以便恢复
            _orig_socket = socketmod.socket

            socksmod.set_default_proxy(
                socksmod.SOCKS5, cfg["host"], cfg["port"],
                rdns=True,
                username=cfg["username"] or None,
                password=cfg["password"] or None,
            )
            socketmod.socket = socksmod.socksocket

            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

            # 恢复原始 socket
            socketmod.socket = _orig_socket

            # 需要在每次请求时临时切换 socket
            class SocksAwareOpener:
                """封装 opener，在请求期间临时替换 socket"""
                def __init__(self, real_opener, socks_mod, socket_mod, orig_socket):
                    self._opener = real_opener
                    self._socks = socks_mod
                    self._socket = socket_mod
                    self._orig_socket = orig_socket

                def open(self, req, timeout=None):
                    self._socket.socket = self._socks.socksocket
                    try:
                        return self._opener.open(req, timeout=timeout if timeout else socketmod._GLOBAL_DEFAULT_TIMEOUT)
                    finally:
                        self._socket.socket = self._orig_socket

            return SocksAwareOpener(opener, socksmod, socketmod, _orig_socket)

        except ImportError:
            # PySocks 未安装，回退到 HTTP 代理格式
            # 很多 SOCKS5 代理（如 Clash）也支持 HTTP CONNECT
            proxy_url = f"http://{auth_str}{cfg['host']}:{cfg['port']}"
            return urllib.request.build_opener(urllib.request.ProxyHandler({
                "http": proxy_url,
                "https": proxy_url,
            }))
    else:
        # HTTP/HTTPS 代理
        proxy_url = f"http://{auth_str}{cfg['host']}:{cfg['port']}"
        return urllib.request.build_opener(urllib.request.ProxyHandler({
            "http": proxy_url,
            "https": proxy_url,
        }))


# ── 进度追踪 ────────────────────────────────────────
class TileDownloadProgress:
    """瓦片下载进度追踪器"""
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self.tasks: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def start_task(self, task_id: str, total: int, description: str = ""):
        """开始一个下载任务"""
        self.tasks[task_id] = {
            "task_id": task_id,
            "total": total,
            "completed": 0,
            "failed": 0,
            "cached": 0,
            "status": "running",        # running | completed | failed | cancelled
            "description": description,
            "started_at": time.time(),
            "updated_at": time.time(),
            "errors": [],
        }

    def update(self, task_id: str, completed_delta: int = 0,
               failed_delta: int = 0, cached_delta: int = 0,
               error_msg: str = ""):
        """更新下载进度"""
        if task_id not in self.tasks:
            return
        t = self.tasks[task_id]
        t["completed"] += completed_delta
        t["failed"] += failed_delta
        t["cached"] += cached_delta
        t["updated_at"] = time.time()
        if error_msg:
            t["errors"].append(error_msg)
        # 检查是否完成
        if t["completed"] + t["failed"] + t["cached"] >= t["total"]:
            t["status"] = "completed" if t["failed"] == 0 else "completed"
            t["completed_at"] = time.time()

    def set_status(self, task_id: str, status: str):
        """设置任务状态"""
        if task_id in self.tasks:
            self.tasks[task_id]["status"] = status
            self.tasks[task_id]["updated_at"] = time.time()

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self.tasks.get(task_id)

    def get_all_tasks(self) -> Dict[str, Dict[str, Any]]:
        return dict(self.tasks)

    def cancel_task(self, task_id: str):
        """取消任务"""
        if task_id in self.tasks:
            self.tasks[task_id]["status"] = "cancelled"
            self.tasks[task_id]["updated_at"] = time.time()


def _ensure_cache_dir():
    os.makedirs(TILE_CACHE_DIR, exist_ok=True)


def _tile_cache_path(url: str) -> str:
    """根据 URL 生成缓存文件路径"""
    h = hashlib.md5(url.encode()).hexdigest()
    return os.path.join(TILE_CACHE_DIR, f"{h}.png")


def _is_cached(url: str) -> bool:
    """检查瓦片是否已缓存"""
    return os.path.isfile(_tile_cache_path(url))


def download_tile(url: str, timeout: int = 15, retries: int = 2) -> Image.Image:
    """下载单个瓦片，优先从缓存读取

    Args:
        url: 瓦片 URL
        timeout: 下载超时（秒）
        retries: 重试次数

    Returns:
        PIL Image (RGBA)
    """
    cache_path = _tile_cache_path(url)

    # 尝试缓存
    if os.path.isfile(cache_path):
        try:
            return Image.open(cache_path).convert("RGBA")
        except Exception:
            pass

    # 下载（带代理支持 + 重试）
    last_error = None
    for attempt in range(retries + 1):
        try:
            opener = _build_opener()

            req = urllib.request.Request(url, headers={
                "User-Agent": "CyclingWorkshop/1.0 (map tile cache)"
            })
            with opener.open(req, timeout=timeout) as resp:
                data = resp.read()

            # 保存缓存
            _ensure_cache_dir()
            with open(cache_path, "wb") as f:
                f.write(data)

            return Image.open(io.BytesIO(data)).convert("RGBA")
        except Exception as e:
            last_error = e
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))

    # 返回灰色占位
    return Image.new("RGBA", (256, 256), (60, 60, 80, 255))


def download_tiles_batch(urls: list, task_id: str = "", timeout: int = 15,
                         concurrency: int = 4) -> Dict[str, Any]:
    """批量下载瓦片，支持进度追踪

    Args:
        urls: 瓦片 URL 列表
        task_id: 进度追踪任务 ID
        timeout: 单个瓦片下载超时
        concurrency: 并发下载数

    Returns:
        {"total": N, "completed": N, "failed": N, "cached": N}
    """
    progress = TileDownloadProgress.get_instance()

    if not task_id:
        task_id = f"dl_{int(time.time()*1000)}"

    total = len(urls)
    progress.start_task(task_id, total, description=f"下载 {total} 个瓦片")

    # 检查已缓存的
    to_download = []
    for url in urls:
        if _is_cached(url):
            progress.update(task_id, cached_delta=1)
        else:
            to_download.append(url)

    # 使用线程池并发下载
    completed = 0
    failed = 0
    lock = threading.Lock()

    def _download_one(url):
        nonlocal completed, failed
        try:
            download_tile(url, timeout=timeout)
            with lock:
                completed += 1
                progress.update(task_id, completed_delta=1)
        except Exception as e:
            with lock:
                failed += 1
                progress.update(task_id, failed_delta=1, error_msg=str(e))

    # 简单的线程池
    threads = []
    for i, url in enumerate(to_download):
        # 检查取消
        task = progress.get_task(task_id)
        if task and task["status"] == "cancelled":
            break

        t = threading.Thread(target=_download_one, args=(url,))
        t.start()
        threads.append(t)

        # 控制并发
        if len(threads) >= concurrency:
            for t in threads:
                t.join(timeout=timeout + 5)
            threads = []

    # 等待剩余线程
    for t in threads:
        t.join(timeout=timeout + 5)

    task = progress.get_task(task_id)
    if task and task["status"] == "running":
        progress.set_status(task_id, "completed")

    return {
        "task_id": task_id,
        "total": total,
        "completed": completed,
        "failed": failed,
        "cached": progress.get_task(task_id)["cached"] if progress.get_task(task_id) else 0,
    }


def render_tile_map(
    center_lat: float,
    center_lon: float,
    zoom: int,
    width: int,
    height: int,
    tile_url_template: str = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    task_id: str = "",
) -> Image.Image:
    """渲染瓦片底图

    Args:
        center_lat: 中心纬度
        center_lon: 中心经度
        zoom: 缩放级别
        width: 输出图像宽度
        height: 输出图像高度
        tile_url_template: 瓦片 URL 模板
        task_id: 可选进度追踪任务 ID

    Returns:
        PIL Image (RGBA)
    """
    progress = TileDownloadProgress.get_instance()

    # 计算中心点对应的像素坐标（全局 Web Mercator）
    n = 2 ** zoom
    center_x = (center_lon + 180.0) / 360.0 * n
    lat_rad = math.radians(center_lat)
    center_y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n

    # 中心像素在全局坐标系中的位置（256px/瓦片）
    center_px_x = center_x * 256
    center_px_y = center_y * 256

    # 需要覆盖的像素范围
    half_w = width / 2
    half_h = height / 2

    start_px_x = center_px_x - half_w
    start_px_y = center_px_y - half_h
    end_px_x = center_px_x + half_w
    end_px_y = center_px_y + half_h

    # 确定需要的瓦片范围
    tile_x_start = int(math.floor(start_px_x / 256))
    tile_y_start = int(math.floor(start_px_y / 256))
    tile_x_end = int(math.floor(end_px_x / 256))
    tile_y_end = int(math.floor(end_px_y / 256))

    # 收集所有需要下载的瓦片 URL
    tile_urls = []
    tile_coords = []
    for ty in range(tile_y_start, tile_y_end + 1):
        for tx in range(tile_x_start, tile_x_end + 1):
            if ty < 0 or ty >= n:
                continue
            tx_mod = tx % int(n)
            s = "abc"[(tx_mod + ty) % 3]
            url = tile_url_template.format(s=s, x=tx_mod, y=ty, z=zoom, r="")
            tile_urls.append(url)
            tile_coords.append((tx, ty))

    # 如果有 task_id，先批量预下载所有瓦片（带进度追踪）
    if task_id:
        download_tiles_batch(tile_urls, task_id=task_id)

    # 创建输出图像
    result = Image.new("RGBA", (width, height), (20, 20, 30, 255))

    # 下载并拼接瓦片
    for (tx, ty), url in zip(tile_coords, tile_urls):
        try:
            tile_img = download_tile(url)
        except Exception:
            tile_img = Image.new("RGBA", (256, 256), (40, 40, 50, 255))

        # 瓦片在结果图像中的位置
        dest_x = int(tx * 256 - start_px_x)
        dest_y = int(ty * 256 - start_px_y)
        result.alpha_composite(tile_img, (dest_x, dest_y))

    return result


# 瓦片底图 URL 模板
TILE_URLS = {
    "osm": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    "carto_dark": "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    "carto_light": "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
    "stamen_terrain": "https://tiles.stadiamaps.com/tiles/stamen_terrain/{z}/{x}/{y}{r}.png",
    "esri_satellite": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
}

TILE_NAMES = {
    "osm": "OpenStreetMap",
    "carto_dark": "CartoDB 暗色",
    "carto_light": "CartoDB 亮色",
    "stamen_terrain": "Stamen 地形",
    "esri_satellite": "ESRI 卫星",
}


def get_tile_url(style: str) -> str:
    """根据样式名获取瓦片 URL 模板"""
    return TILE_URLS.get(style, TILE_URLS["carto_dark"])


def get_cache_stats() -> Dict[str, Any]:
    """获取瓦片缓存统计"""
    if not os.path.isdir(TILE_CACHE_DIR):
        return {"count": 0, "size_mb": 0.0}

    total_size = 0
    count = 0
    for f in os.listdir(TILE_CACHE_DIR):
        fp = os.path.join(TILE_CACHE_DIR, f)
        if os.path.isfile(fp):
            count += 1
            total_size += os.path.getsize(fp)

    return {
        "count": count,
        "size_mb": round(total_size / 1024 / 1024, 2),
        "cache_dir": TILE_CACHE_DIR,
    }


def clear_cache() -> int:
    """清除所有瓦片缓存，返回删除的文件数"""
    if not os.path.isdir(TILE_CACHE_DIR):
        return 0
    count = 0
    for f in os.listdir(TILE_CACHE_DIR):
        fp = os.path.join(TILE_CACHE_DIR, f)
        if os.path.isfile(fp):
            try:
                os.remove(fp)
                count += 1
            except Exception:
                pass
    return count


def resolve_tile_url(style: str, z: int, x: int, y: int) -> str:
    """根据样式和瓦片坐标，解析出实际瓦片 URL

    Args:
        style: 底图样式名（osm / carto_dark / ...）
        z: 缩放级别
        x: 瓦片 X 坐标
        y: 瓦片 Y 坐标

    Returns:
        实际瓦片下载 URL
    """
    template = get_tile_url(style)
    n = 2 ** z
    tx_mod = x % n
    s = "abc"[(tx_mod + y) % 3]
    return template.format(s=s, x=tx_mod, y=y, z=z, r="")


def get_cached_tile(style: str, z: int, x: int, y: int):
    """获取已缓存的瓦片数据

    Returns:
        (image_bytes, content_type) 或 None
    """
    url = resolve_tile_url(style, z, x, y)
    cache_path = _tile_cache_path(url)
    if os.path.isfile(cache_path):
        try:
            with open(cache_path, "rb") as f:
                return f.read(), "image/png"
        except Exception:
            pass
    return None


def get_cache_inventory() -> Dict[str, Any]:
    """获取缓存瓦片的详细清单（按样式+zoom分组统计）

    Returns:
        {"by_style": {style: {zoom: count}}, "total_count": N, "total_size_mb": M}
    """
    if not os.path.isdir(TILE_CACHE_DIR):
        return {"by_style": {}, "total_count": 0, "total_size_mb": 0}

    # 反向映射：URL → (style, z, x, y)
    # 通过遍历所有可能的 z/x/y 组合来反查成本太高，
    # 改为从 URL 模板反推
    total_size = 0
    count = 0
    by_style_zoom: Dict[str, Dict[int, int]] = {}

    for f in os.listdir(TILE_CACHE_DIR):
        fp = os.path.join(TILE_CACHE_DIR, f)
        if not os.path.isfile(fp):
            continue
        count += 1
        total_size += os.path.getsize(fp)

    # 重建缓存索引：对每种样式/zoom组合计算所需瓦片 URL 并比对缓存
    # 这个方法较慢但准确——先返回基本统计，索引在需要时按需构建
    return {
        "by_style": by_style_zoom,
        "total_count": count,
        "total_size_mb": round(total_size / 1024 / 1024, 2),
        "cache_dir": TILE_CACHE_DIR,
    }


def get_cache_tiles_for_region(
    min_lat: float, max_lat: float,
    min_lon: float, max_lon: float,
    zoom: int,
) -> Dict[str, Any]:
    """获取指定区域内已缓存瓦片的信息（用于缓存可视化）

    Returns:
        {"tiles": [{x, y, z, cached, url}], "total": N, "cached_count": M}
    """
    # 检查所有样式的缓存情况
    all_tiles = []
    for style in TILE_URLS:
        urls = compute_tile_urls_for_region(min_lat, max_lat, min_lon, max_lon, zoom, style)
        for url in urls:
            # 从 URL 中解析 x, y（简化处理）
            is_cached = _is_cached(url)
            all_tiles.append({
                "style": style,
                "url": url,
                "cached": is_cached,
            })

    cached_count = sum(1 for t in all_tiles if t["cached"])
    return {
        "tiles": all_tiles,
        "total": len(all_tiles),
        "cached_count": cached_count,
    }


def latlon_to_pixel(lat, lon, zoom):
    """经纬度 → Web Mercator 像素坐标"""
    n = 2 ** zoom
    x = (lon + 180.0) / 360.0 * n * 256
    lat_rad = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n * 256
    return x, y


def pixel_to_latlon(px_x, px_y, zoom):
    """Web Mercator 像素坐标 → 经纬度"""
    n = 2 ** zoom
    lon = px_x / (n * 256) * 360.0 - 180.0
    y_norm = px_y / (n * 256)
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y_norm)))
    lat = math.degrees(lat_rad)
    return lat, lon


def compute_zoom_for_size(min_lat, max_lat, min_lon, max_lon, width, height, padding=0.1):
    """计算合适的 zoom 级别，使轨迹在指定尺寸内完全可见

    Args:
        min_lat, max_lat, min_lon, max_lon: 轨迹范围
        width, height: 输出图像尺寸（像素）
        padding: 边距比例（默认 10%）

    Returns:
        zoom 级别 (int)
    """
    center_lat = (min_lat + max_lat) / 2
    center_lon = (min_lon + max_lon) / 2

    lat_range = max_lat - min_lat
    lon_range = max_lon - min_lon

    if lat_range <= 0 or lon_range <= 0:
        return 14

    cos_lat = math.cos(math.radians(center_lat))
    effective_lon_range = lon_range * cos_lat

    # 需要的视野范围（加上 padding）
    needed_lat = lat_range * (1 + padding * 2)
    needed_lon = effective_lon_range * (1 + padding * 2)

    # 从 zoom=18 开始递减，找到能完整包含轨迹的最大 zoom
    for zoom in range(18, 5, -1):
        n = 2 ** zoom
        view_lon = width / (n * 256) * 360.0 * cos_lat
        view_lat = height / (n * 256) * 360.0

        if view_lon >= needed_lon and view_lat >= needed_lat:
            return zoom

    return 10


def compute_tile_urls_for_region(
    min_lat: float, max_lat: float,
    min_lon: float, max_lon: float,
    zoom: int,
    tile_style: str = "carto_dark",
) -> list:
    """计算指定区域和 zoom 级别所需的所有瓦片 URL

    Args:
        min_lat, max_lat, min_lon, max_lon: 区域范围
        zoom: 缩放级别
        tile_style: 底图样式

    Returns:
        瓦片 URL 列表
    """
    tile_url_template = get_tile_url(tile_style)
    n = 2 ** zoom

    # 计算四个角的像素坐标
    tl_x, tl_y = latlon_to_pixel(max_lat, min_lon, zoom)
    br_x, br_y = latlon_to_pixel(min_lat, max_lon, zoom)

    tile_x_start = int(math.floor(tl_x / 256))
    tile_y_start = int(math.floor(tl_y / 256))
    tile_x_end = int(math.floor(br_x / 256))
    tile_y_end = int(math.floor(br_y / 256))

    urls = []
    for ty in range(tile_y_start, tile_y_end + 1):
        for tx in range(tile_x_start, tile_x_end + 1):
            if ty < 0 or ty >= n:
                continue
            tx_mod = tx % int(n)
            s = "abc"[(tx_mod + ty) % 3]
            url = tile_url_template.format(s=s, x=tx_mod, y=ty, z=zoom, r="")
            urls.append(url)

    return urls


def preload_tiles_for_fit(
    fit_data,
    fit_id: str = "",
    tile_style: str = "carto_dark",
    zoom: int = 0,
    width: int = 400,
    height: int = 300,
    task_id: str = "",
) -> Dict[str, Any]:
    """为 FIT 轨迹预下载所需瓦片

    Args:
        fit_data: FitData 对象
        fit_id: FIT 数据 ID（用于生成 task_id）
        tile_style: 底图样式
        zoom: 缩放级别（0=自动计算）
        width: 渲染宽度
        height: 渲染高度
        task_id: 可选任务 ID

    Returns:
        {"task_id": ..., "total": N, "style": ..., "zoom": ...}
    """
    from services.fit_parser import FitParserService
    coords = FitParserService.get_track_coords(fit_data)
    if len(coords) < 2:
        return {"error": "轨迹数据不足"}

    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)

    if zoom <= 0:
        zoom = compute_zoom_for_size(min_lat, max_lat, min_lon, max_lon, width, height)

    urls = compute_tile_urls_for_region(min_lat, max_lat, min_lon, max_lon, zoom, tile_style)

    if not task_id:
        task_id = f"preload_{fit_id}_{tile_style}_z{zoom}_{int(time.time()*1000)}"

    # 启动异步下载
    def _async_download():
        download_tiles_batch(urls, task_id=task_id)

    t = threading.Thread(target=_async_download, daemon=True)
    t.start()

    return {
        "task_id": task_id,
        "total_tiles": len(urls),
        "style": tile_style,
        "style_name": TILE_NAMES.get(tile_style, tile_style),
        "zoom": zoom,
        "region": {
            "min_lat": min_lat, "max_lat": max_lat,
            "min_lon": min_lon, "max_lon": max_lon,
        },
    }
