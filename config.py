"""CyclingWorkshop 配置"""
import os
import shutil

# 基础路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# FFmpeg 路径
# 优先读取环境变量；未设置时从系统 PATH 查找；仍找不到时保留命令名，
# 让 subprocess/PyAV 在运行时报出清晰的缺失依赖错误。
FFMPEG_PATH = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE_PATH = os.environ.get("FFPROBE_PATH") or shutil.which("ffprobe") or "ffprobe"

# 数据目录
PROJECTS_DIR = os.path.join(BASE_DIR, "saved_states")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
TILE_CACHE_DIR = os.path.join(BASE_DIR, "tile_cache")

# Flask 配置
HOST = "127.0.0.1"
PORT = 5000
DEBUG = True
SECRET_KEY = "cycling-workshop-dev-key"

# 确保目录存在
for d in [PROJECTS_DIR, OUTPUT_DIR, TILE_CACHE_DIR]:
    os.makedirs(d, exist_ok=True)
