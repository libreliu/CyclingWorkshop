"""渲染 API — 基于 PyAV 渲染管线（实时日志）"""
import json
import os
import time
import uuid
import threading
from datetime import datetime

from flask import Blueprint, request, jsonify, send_file, Response

from models.overlay_template import WidgetConfig
from models.video_config import TimeSyncConfig
from services.frame_renderer import FrameRenderer
from services.render_pipeline import RenderPipeline
from services.video_analyzer import VideoAnalyzerService
import config

render_bp = Blueprint("render", __name__)

# 渲染任务状态
_render_tasks = {}

# FIT 数据缓存引用（从 fit API 导入）
from api.fit import _fit_cache


@render_bp.route("/preview", methods=["POST"])
def render_preview():
    """渲染单帧预览：接收配置，返回叠加后的 JPEG 图片"""
    import io

    body = request.get_json(silent=True) or {}

    fit_path = body.get("fit_path", "").strip()
    video_path = body.get("video_path", "").strip()
    video_time_sec = body.get("video_time_sec", 0)
    widgets_data = body.get("widgets", [])
    time_sync_data = body.get("time_sync", {})
    include_background = body.get("include_background", True)
    canvas_width = body.get("canvas_width", 1920)
    canvas_height = body.get("canvas_height", 1080)

    # 获取 FIT 数据
    fit_data = _fit_cache.get(fit_path)
    if not fit_data:
        return jsonify({"error": f"FIT 数据未加载: {fit_path}，请先在步骤1加载"}), 400

    # 构造 Widget 配置
    widgets = [WidgetConfig.from_dict(w) for w in widgets_data]

    # 构造时间同步配置
    time_sync = _build_time_sync(time_sync_data)

    # 计算对应的 FIT 时间
    fit_time = time_sync.fit_time_at_video_seconds(video_time_sec)

    # 渲染叠加层
    overlay_img = FrameRenderer.render_frame(
        fit_data=fit_data,
        fit_time=fit_time,
        widgets=widgets,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )

    # 如果需要视频背景，合成视频帧
    if include_background and video_path and os.path.isfile(video_path):
        try:
            # 获取视频 rotation metadata（PyAV 不自动旋转，需手动处理）
            vi = VideoAnalyzerService.analyze(video_path)
            rotation = vi.rotation if vi else 0

            frame_bytes = VideoAnalyzerService.extract_frame(
                video_path, video_time_sec, rotation=rotation
            )
            if frame_bytes:
                from PIL import Image
                bg_img = Image.open(io.BytesIO(frame_bytes)).convert("RGBA")
                # resize 到画布尺寸
                if bg_img.size != (canvas_width, canvas_height):
                    bg_img = bg_img.resize((canvas_width, canvas_height), Image.LANCZOS)
                bg_img.alpha_composite(overlay_img)
                overlay_img = bg_img
            else:
                # 帧提取失败 — 用纯黑背景代替透明背景
                bg = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 255))
                bg.alpha_composite(overlay_img)
                overlay_img = bg
        except Exception as e:
            print(f"[RenderPreview] 背景合成失败: {e}")
            try:
                from PIL import Image
                bg = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 255))
                bg.alpha_composite(overlay_img)
                overlay_img = bg
            except:
                pass

    # 输出为 JPEG
    output = overlay_img.convert("RGB")
    buf = io.BytesIO()
    output.save(buf, format="JPEG", quality=90)
    buf.seek(0)

    return send_file(buf, mimetype="image/jpeg")


@render_bp.route("/start", methods=["POST"])
def render_start():
    """启动渲染任务（后台线程）"""
    body = request.get_json(silent=True) or {}
    project = body.get("project", {})

    fit_path = project.get("fit_path", "").strip()
    video_path = project.get("video_path", "").strip()
    widgets_data = project.get("widgets", [])
    time_sync_data = project.get("time_sync", {})
    render_settings = project.get("render_settings", {})
    canvas_width = render_settings.get("width", 1920)
    canvas_height = render_settings.get("height", 1080)

    # 参数校验
    fit_data = _fit_cache.get(fit_path)
    if not fit_data:
        return jsonify({"error": f"FIT 数据未加载: {fit_path}"}), 400

    if not video_path or not os.path.isfile(video_path):
        return jsonify({"error": f"视频文件不存在: {video_path}"}), 400

    # 获取视频信息
    video_info = VideoAnalyzerService.analyze(video_path)
    if not video_info:
        return jsonify({"error": "视频分析失败"}), 500

    # 输出路径
    output_path = render_settings.get("output_path", "")
    if not output_path:
        output_path = os.path.join(config.OUTPUT_DIR, f"render_{uuid.uuid4().hex[:8]}.mp4")

    # 构造时间同步配置
    time_sync = _build_time_sync(time_sync_data)

    # 计算总帧数
    fps = render_settings.get("fps", video_info.fps)
    if fps <= 0:
        fps = 29.97

    # 渲染范围
    render_start_sec = render_settings.get("start_sec", 0)
    render_end_sec = render_settings.get("end_sec", video_info.duration)
    total_duration = render_end_sec - render_start_sec
    total_frames = int(total_duration * fps)

    if total_frames <= 0:
        return jsonify({"error": f"渲染范围无效: {render_start_sec}s ~ {render_end_sec}s"}), 400

    # 构造 Widget 配置
    widgets = [WidgetConfig.from_dict(w) for w in widgets_data]

    # 编码设置
    codec = render_settings.get("codec", "libx264")
    preset = render_settings.get("preset", "fast")
    crf = render_settings.get("crf", 23)
    audio_mode = render_settings.get("audio", "copy")  # "copy" | "none"
    overlay_only = render_settings.get("overlay_only", False)  # 仅输出 overlay 层
    overlay_codec = render_settings.get("overlay_codec", "qtrle")  # overlay 编码器: qtrle | libvpx-vp9
    num_workers = render_settings.get("num_workers", 4)  # 并行渲染线程数
    batch_size = render_settings.get("batch_size", 8)  # 每批并行帧数

    # 创建任务
    task_id = uuid.uuid4().hex[:12]
    task = {
        "task_id": task_id,
        "status": "running",
        "progress": 0,
        "total_frames": total_frames,
        "current_frame": 0,
        "output_path": output_path,
        "error": None,
        "cancelled": False,
        # 详细统计
        "overlay_fps": 0.0,
        "encode_fps": 0.0,
        "elapsed_sec": 0.0,
        "eta_sec": 0.0,
        "phase": "rendering",
    }
    _render_tasks[task_id] = task

    # 创建 pipeline（带日志功能）
    pipeline = RenderPipeline()
    task["_pipeline"] = pipeline

    # 启动后台渲染线程
    thread = threading.Thread(
        target=_render_worker_v3,
        args=(
            task_id, task, pipeline, fit_data, video_path, widgets, time_sync,
            canvas_width, canvas_height, fps, render_start_sec, render_end_sec,
            output_path, codec, preset, crf, audio_mode, overlay_only, overlay_codec,
            num_workers, batch_size,
        ),
        daemon=True,
    )
    thread.start()

    return jsonify({"task_id": task_id, "status": "running", "total_frames": total_frames})


def _render_worker_v3(
    task_id, task, pipeline, fit_data, video_path, widgets, time_sync,
    canvas_width, canvas_height, fps, start_sec, end_sec,
    output_path, codec, preset, crf, audio_mode, overlay_only, overlay_codec,
    num_workers, batch_size,
):
    """后台渲染线程 — 使用 PyAV 渲染管线（多进程流水线 + 实时日志）"""

    def progress_callback(stats):
        """进度回调：更新 task 字典"""
        if task["cancelled"]:
            pipeline.cancel()
        task["current_frame"] = stats.get("frames_rendered", 0)
        task["progress"] = stats.get("progress_pct", 0)
        task["overlay_fps"] = stats.get("overlay_fps", 0.0)
        task["encode_fps"] = stats.get("encode_fps", 0.0)
        task["elapsed_sec"] = stats.get("elapsed_sec", 0.0)
        task["eta_sec"] = stats.get("eta_sec", 0.0)
        task["phase"] = stats.get("phase", "rendering")

    # 检查取消
    if task["cancelled"]:
        task["status"] = "cancelled"
        return

    result = pipeline.render_video(
        video_path=video_path,
        fit_data=fit_data,
        widgets=widgets,
        time_sync=time_sync,
        output_path=output_path,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        fps=fps,
        start_sec=start_sec,
        end_sec=end_sec,
        codec=codec,
        preset=preset,
        crf=crf,
        audio_mode=audio_mode,
        overlay_only=overlay_only,
        overlay_codec=overlay_codec,
        num_workers=num_workers,
        batch_size=batch_size,
        progress_callback=progress_callback,
    )

    task["status"] = result["status"]
    if result["status"] == "completed":
        task["current_frame"] = task["total_frames"]
        task["progress"] = 100
        # overlay-only 模式可能自动调整了输出路径（.mp4 → .mov/.webm）
        if result.get("output_path"):
            task["output_path"] = result["output_path"]
    elif result["status"] == "error":
        task["error"] = result.get("error", "未知错误")


@render_bp.route("/<task_id>/status", methods=["GET"])
def render_status(task_id):
    """查询渲染进度"""
    task = _render_tasks.get(task_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify({
        "task_id": task["task_id"],
        "status": task["status"],
        "progress": task["progress"],
        "total_frames": task["total_frames"],
        "current_frame": task["current_frame"],
        "output_path": task.get("output_path", ""),
        "error": task.get("error"),
        "overlay_fps": task.get("overlay_fps", 0.0),
        "encode_fps": task.get("encode_fps", 0.0),
        "elapsed_sec": task.get("elapsed_sec", 0.0),
        "eta_sec": task.get("eta_sec", 0.0),
        "phase": task.get("phase", "rendering"),
    })


@render_bp.route("/<task_id>/logs", methods=["GET"])
def render_logs(task_id):
    """获取渲染日志（SSE 流式或 JSON）"""
    task = _render_tasks.get(task_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404

    pipeline = task.get("_pipeline")
    if not pipeline:
        return jsonify({"logs": [], "total": 0})

    since = request.args.get("since", 0, type=int)

    # 检查是否请求 SSE 流
    accept = request.headers.get("Accept", "")
    if "text/event-stream" in accept:
        return _render_logs_sse(pipeline, task)

    # JSON 模式：返回日志
    logs, total = pipeline.get_logs(since)
    return jsonify({
        "logs": logs,
        "total": total,
    })


def _render_logs_sse(pipeline, task):
    """SSE 流式日志推送"""
    def generate():
        idx = 0
        while task["status"] == "running":
            logs, total = pipeline.get_logs(idx)
            for log in logs:
                yield f"data: {json.dumps(log)}\n\n"
            idx = total
            time.sleep(0.3)

        # 发送剩余日志
        logs, total = pipeline.get_logs(idx)
        for log in logs:
            yield f"data: {json.dumps(log)}\n\n"

        yield f"data: {json.dumps({'event': 'done', 'status': task['status']})}\n\n"

    return Response(generate(), mimetype="text/event-stream")


@render_bp.route("/<task_id>/cancel", methods=["POST"])
def render_cancel(task_id):
    """取消渲染"""
    task = _render_tasks.get(task_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404
    task["cancelled"] = True
    task["status"] = "cancelled"
    # 通知 pipeline
    pipeline = task.get("_pipeline")
    if pipeline:
        pipeline.cancel()
    return jsonify({"status": "cancelled"})


@render_bp.route("/<task_id>/result", methods=["GET"])
def render_result(task_id):
    """获取渲染结果（本地输出路径）"""
    task = _render_tasks.get(task_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404
    if task.get("status") != "completed":
        return jsonify({"error": "渲染尚未完成", "status": task.get("status")}), 400
    return jsonify({"output_path": task.get("output_path", ""), "status": "completed"})


def _build_time_sync(ts_data: dict) -> TimeSyncConfig:
    """从请求数据构建 TimeSyncConfig"""
    video_start_time = None
    if ts_data.get("video_start_time"):
        try:
            video_start_time = datetime.fromisoformat(ts_data["video_start_time"])
        except (ValueError, TypeError):
            pass
    fit_start_time = None
    if ts_data.get("fit_start_time"):
        try:
            fit_start_time = datetime.fromisoformat(ts_data["fit_start_time"])
        except (ValueError, TypeError):
            pass

    return TimeSyncConfig(
        video_start_time=video_start_time,
        fit_start_time=fit_start_time,
        offset_seconds=ts_data.get("offset_seconds", 0),
        time_scale=ts_data.get("time_scale", 1.0),
    )
