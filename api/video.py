"""视频 API"""
import io
from flask import Blueprint, request, jsonify, send_file
import os

from services.video_analyzer import VideoAnalyzerService

video_bp = Blueprint("video", __name__)

_video_cache = {}  # path → VideoInfo


def _get_or_analyze(path: str):
    if path in _video_cache:
        return _video_cache[path]
    if not os.path.isfile(path):
        return None
    info = VideoAnalyzerService.analyze(path)
    if info:
        _video_cache[path] = info
    return info


@video_bp.route("/load", methods=["POST"])
def load_video():
    """通过本地路径加载视频"""
    body = request.get_json(silent=True) or {}
    path = body.get("path", "").strip().strip('"').strip("'")

    if not path:
        return jsonify({"error": "请提供视频文件路径"}), 400
    if not os.path.isfile(path):
        return jsonify({"error": f"文件不存在: {path}"}), 404

    info = _get_or_analyze(path)
    if info is None:
        return jsonify({"error": f"视频分析失败: {path}"}), 500

    return jsonify({
        "id": path,
        "info": info.to_dict(),
    })


@video_bp.route("/<path:video_id>/info", methods=["GET"])
def video_info(video_id):
    """获取视频元数据"""
    info = _video_cache.get(video_id)
    if not info:
        return jsonify({"error": "未找到，请先加载"}), 404
    return jsonify(info.to_dict())


@video_bp.route("/<path:video_id>/frame", methods=["GET"])
def video_frame(video_id):
    """提取视频帧预览 ?t=秒"""
    info = _video_cache.get(video_id)
    if not info:
        return jsonify({"error": "未找到，请先加载"}), 404

    t = request.args.get("t", 0, type=float)
    frame_bytes = VideoAnalyzerService.extract_frame(info.file_path, t, rotation=info.rotation)
    if frame_bytes is None:
        return jsonify({"error": "帧提取失败"}), 500

    buf = io.BytesIO(frame_bytes)
    return send_file(buf, mimetype="image/jpeg")


@video_bp.route("/<path:video_id>/thumbnail", methods=["GET"])
def video_thumbnail(video_id):
    """获取视频首帧缩略图"""
    info = _video_cache.get(video_id)
    if not info:
        return jsonify({"error": "未找到，请先加载"}), 404

    frame_bytes = VideoAnalyzerService.extract_frame(info.file_path, 0, rotation=info.rotation)
    if frame_bytes is None:
        return jsonify({"error": "缩略图提取失败"}), 500

    buf = io.BytesIO(frame_bytes)
    return send_file(buf, mimetype="image/jpeg")
