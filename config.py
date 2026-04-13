"""CyclingWorkshop 配置"""
import os

# 基础路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# FFmpeg 路径
# python-ffmpeg 库默认查找系统 PATH 中的 ffmpeg/ffprobe
# 如需指定路径，设置 FFMPEG_PATH / FFPROBE_PATH 环境变量
FFMPEG_PATH = os.environ.get(
    "FFMPEG_PATH",
    r"C:\Projects\ffmpeg-7.1-full_build-shared\bin\ffmpeg.exe"
)
FFPROBE_PATH = os.environ.get(
    "FFPROBE_PATH",
    r"C:\Projects\ffmpeg-7.1-full_build-shared\bin\ffprobe.exe"
)

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
