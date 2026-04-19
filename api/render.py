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
_render_batches = {}
_render_batch_lock = threading.Lock()

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
    global_style = body.get("global_style")

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
        global_style=global_style,
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
    """启动渲染任务（批量协议；单视频视为批量大小 1）"""
    body = request.get_json(silent=True) or {}
    project = body.get("project", {})
    video_items = _collect_video_items(project)
    if not video_items:
        return jsonify({"error": "至少需要一个视频条目"}), 400

    batch_id = uuid.uuid4().hex[:12]
    batch = {
        "batch_id": batch_id,
        "status": "queued",
        "created_at": time.time(),
        "cancelled": False,
        "jobs": [],
        "active_task_id": None,
        "active_task_ids": [],
        "max_concurrent": 1,
    }

    prepared_jobs = []
    try:
        for index, item in enumerate(video_items):
            prepared = _prepare_render_job(project, item, index=index)
            task_id = uuid.uuid4().hex[:12]
            task = _new_render_task(
                task_id=task_id,
                total_frames=prepared["total_frames"],
                output_path=prepared["output_path"],
                batch_id=batch_id,
                video_item_id=item.get("id") or f"video_{index+1}",
                video_path=prepared["video_path"],
                display_name=os.path.basename(prepared["video_path"]),
                initial_status="queued",
            )
            _render_tasks[task_id] = task
            batch["jobs"].append({
                "task_id": task_id,
                "video_item_id": task["video_item_id"],
                "video_path": prepared["video_path"],
                "display_name": task["display_name"],
            })
            prepared_jobs.append((task_id, prepared))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"创建批量任务失败: {e}"}), 500

    batch["max_concurrent"] = _resolve_batch_concurrency(prepared_jobs)
    _render_batches[batch_id] = batch

    thread = threading.Thread(
        target=_render_batch_worker,
        args=(batch_id, prepared_jobs),
        daemon=True,
    )
    thread.start()

    response = _build_batch_status_payload(batch_id)
    if len(batch["jobs"]) == 1:
        response["task_id"] = batch["jobs"][0]["task_id"]
    return jsonify(response)


def _render_worker_v3(
    task_id, task, pipeline, fit_data, video_path, widgets, time_sync,
    canvas_width, canvas_height, fps, start_sec, end_sec,
    output_path, codec, preset, crf, audio_mode, overlay_only, overlay_codec,
    hwaccel_decode, num_workers, batch_size, render_mode, max_ticks,
    global_style,
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
        hwaccel_decode=hwaccel_decode,
        use_tick_mode=(render_mode == "tick"),
        max_ticks=max_ticks,
        global_style=global_style,
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


def _collect_video_items(project: dict) -> list[dict]:
    items = project.get("video_items") or []
    if items:
        return items
    video_path = project.get("video_path", "").strip()
    if not video_path:
        return []
    return [{
        "id": "video_1",
        "video_path": video_path,
        "time_sync": project.get("time_sync", {}) or {},
        "render_settings": {},
    }]


def _default_output_path(video_path: str, overlay_only: bool, overlay_codec: str) -> str:
    root, _ = os.path.splitext(video_path)
    if overlay_only:
        ext = ".webm" if overlay_codec == "libvpx-vp9" else ".mov"
        return root + "_overlay" + ext
    return root + "_overlay.mp4"


def _coerce_positive_int(value, default: int = 1) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _resolve_batch_concurrency(prepared_jobs: list[tuple[str, dict]]) -> int:
    if not prepared_jobs:
        return 1
    requested = prepared_jobs[0][1].get("batch_concurrency", 1)
    return min(_coerce_positive_int(requested, 1), len(prepared_jobs))


def _prepare_render_job(project: dict, item: dict, index: int = 0) -> dict:
    fit_path = project.get("fit_path", "").strip()
    fit_data = _fit_cache.get(fit_path)
    if not fit_data:
        raise ValueError(f"FIT 数据未加载: {fit_path}")

    video_path = str(item.get("video_path", "")).strip()
    if not video_path or not os.path.isfile(video_path):
        raise ValueError(f"视频文件不存在: {video_path}")

    video_info = VideoAnalyzerService.analyze(video_path)
    if not video_info:
        raise ValueError(f"视频分析失败: {video_path}")

    widgets_data = project.get("widgets", [])
    widgets = [WidgetConfig.from_dict(w) for w in widgets_data]

    shared_render_settings = project.get("render_settings", {}) or {}
    item_render_settings = item.get("render_settings", {}) or {}
    render_settings = dict(shared_render_settings)
    render_settings.update(item_render_settings)

    canvas_width = render_settings.get("width", video_info.width or 1920)
    canvas_height = render_settings.get("height", video_info.height or 1080)
    fps = render_settings.get("fps", video_info.fps)
    if fps <= 0:
        fps = 29.97

    render_start_sec = render_settings.get("start_sec", 0)
    render_end_sec = render_settings.get("end_sec", video_info.duration)
    total_duration = render_end_sec - render_start_sec
    total_frames = int(total_duration * fps)
    if total_frames <= 0:
        raise ValueError(f"渲染范围无效: {render_start_sec}s ~ {render_end_sec}s")

    codec = render_settings.get("codec", "libx264")
    preset = render_settings.get("preset", "fast")
    crf = render_settings.get("crf", 23)
    audio_mode = render_settings.get("audio", "copy")
    overlay_only = render_settings.get("overlay_only", False)
    overlay_codec = render_settings.get("overlay_codec", "qtrle")
    hwaccel_decode = render_settings.get("hwaccel_decode", False)
    num_workers = render_settings.get("num_workers", 4)
    batch_size = render_settings.get("batch_size", 8)
    render_mode = render_settings.get("render_mode", "pipeline")
    max_ticks = render_settings.get("max_ticks", None)
    batch_concurrency = _coerce_positive_int(render_settings.get("batch_concurrency", 1), 1)
    output_path = render_settings.get("output_path", "")
    if not output_path:
        output_path = _default_output_path(video_path, overlay_only, overlay_codec)

    return {
        "fit_data": fit_data,
        "video_path": video_path,
        "widgets": widgets,
        "time_sync": _build_time_sync(item.get("time_sync") or project.get("time_sync", {}) or {}),
        "canvas_width": canvas_width,
        "canvas_height": canvas_height,
        "fps": fps,
        "start_sec": render_start_sec,
        "end_sec": render_end_sec,
        "total_frames": total_frames,
        "output_path": output_path,
        "codec": codec,
        "preset": preset,
        "crf": crf,
        "audio_mode": audio_mode,
        "overlay_only": overlay_only,
        "overlay_codec": overlay_codec,
        "hwaccel_decode": hwaccel_decode,
        "num_workers": num_workers,
        "batch_size": batch_size,
        "render_mode": render_mode,
        "max_ticks": max_ticks,
        "batch_concurrency": batch_concurrency,
        "global_style": project.get("global_style") or {},
        "video_item_id": item.get("id") or f"video_{index+1}",
    }


def _new_render_task(
    task_id: str,
    total_frames: int,
    output_path: str,
    batch_id: str | None = None,
    video_item_id: str | None = None,
    video_path: str = "",
    display_name: str = "",
    initial_status: str = "queued",
):
    return {
        "task_id": task_id,
        "batch_id": batch_id,
        "video_item_id": video_item_id,
        "video_path": video_path,
        "display_name": display_name or os.path.basename(video_path),
        "status": initial_status,
        "progress": 0,
        "total_frames": total_frames,
        "current_frame": 0,
        "output_path": output_path,
        "error": None,
        "cancelled": False,
        "overlay_fps": 0.0,
        "encode_fps": 0.0,
        "elapsed_sec": 0.0,
        "eta_sec": 0.0,
        "phase": "queued",
    }


def _run_prepared_render_task(task_id: str, prepared: dict):
    task = _render_tasks[task_id]
    if task["cancelled"]:
        task["status"] = "cancelled"
        task["phase"] = "cancelled"
        return

    task["status"] = "running"
    task["phase"] = "rendering"
    pipeline = RenderPipeline()
    task["_pipeline"] = pipeline

    try:
        _render_worker_v3(
            task_id, task, pipeline,
            prepared["fit_data"], prepared["video_path"], prepared["widgets"], prepared["time_sync"],
            prepared["canvas_width"], prepared["canvas_height"], prepared["fps"],
            prepared["start_sec"], prepared["end_sec"],
            prepared["output_path"], prepared["codec"], prepared["preset"], prepared["crf"],
            prepared["audio_mode"], prepared["overlay_only"], prepared["overlay_codec"],
            prepared["hwaccel_decode"], prepared["num_workers"], prepared["batch_size"],
            prepared["render_mode"], prepared["max_ticks"], prepared["global_style"],
        )
    finally:
        task.pop("_pipeline", None)


def _set_batch_active_tasks(batch: dict, task_ids: list[str]):
    batch["active_task_ids"] = list(task_ids)
    batch["active_task_id"] = task_ids[0] if task_ids else None


def _mark_batch_task_started(batch_id: str, task_id: str):
    with _render_batch_lock:
        batch = _render_batches.get(batch_id)
        if not batch:
            return
        active = list(batch.get("active_task_ids") or [])
        if task_id not in active:
            active.append(task_id)
        _set_batch_active_tasks(batch, active)


def _mark_batch_task_finished(batch_id: str, task_id: str):
    with _render_batch_lock:
        batch = _render_batches.get(batch_id)
        if not batch:
            return
        active = [tid for tid in (batch.get("active_task_ids") or []) if tid != task_id]
        _set_batch_active_tasks(batch, active)


def _run_batch_render_slot(batch_id: str, task_id: str, prepared: dict, semaphore: threading.Semaphore):
    with semaphore:
        batch = _render_batches.get(batch_id)
        task = _render_tasks.get(task_id)
        if not batch or not task:
            return
        if batch.get("cancelled") or task.get("cancelled"):
            task["status"] = "cancelled"
            task["phase"] = "cancelled"
            return

        _mark_batch_task_started(batch_id, task_id)
        try:
            _run_prepared_render_task(task_id, prepared)
        finally:
            _mark_batch_task_finished(batch_id, task_id)


def _render_batch_worker(batch_id: str, prepared_jobs: list[tuple[str, dict]]):
    batch = _render_batches.get(batch_id)
    if not batch:
        return

    batch["status"] = "running"
    semaphore = threading.Semaphore(batch.get("max_concurrent") or 1)
    job_threads = []

    for task_id, prepared in prepared_jobs:
        task = _render_tasks.get(task_id)
        if not task:
            continue
        if batch.get("cancelled"):
            task["status"] = "cancelled"
            task["phase"] = "cancelled"
            continue

        thread = threading.Thread(
            target=_run_batch_render_slot,
            args=(batch_id, task_id, prepared, semaphore),
            daemon=True,
        )
        thread.start()
        job_threads.append(thread)

    for thread in job_threads:
        thread.join()

    _set_batch_active_tasks(batch, [])
    payload = _build_batch_status_payload(batch_id)
    batch["status"] = payload["status"]


def _build_batch_status_payload(batch_id: str) -> dict:
    batch = _render_batches.get(batch_id)
    if not batch:
        return {}

    jobs = []
    status_counts = {
        "queued": 0,
        "running": 0,
        "completed": 0,
        "error": 0,
        "cancelled": 0,
    }
    progress_sum = 0.0

    for job in batch["jobs"]:
        task = _render_tasks.get(job["task_id"], {})
        status = task.get("status", "queued")
        if status not in status_counts:
            status_counts[status] = 0
        status_counts[status] += 1
        progress_sum += float(task.get("progress", 0) or 0)
        jobs.append({
            "task_id": job["task_id"],
            "video_item_id": job.get("video_item_id"),
            "video_path": job.get("video_path", ""),
            "display_name": job.get("display_name", ""),
            "status": status,
            "progress": task.get("progress", 0),
            "current_frame": task.get("current_frame", 0),
            "total_frames": task.get("total_frames", 0),
            "output_path": task.get("output_path", ""),
            "error": task.get("error"),
        })

    total_jobs = len(batch["jobs"])
    overall_progress = round(progress_sum / total_jobs, 1) if total_jobs else 0.0

    if batch.get("cancelled") and status_counts["running"] == 0:
        batch_status = "cancelled"
    elif status_counts["running"] > 0:
        batch_status = "running"
    elif status_counts["queued"] > 0:
        batch_status = "queued"
    elif status_counts["error"] > 0 and status_counts["completed"] > 0:
        batch_status = "completed_with_errors"
    elif status_counts["error"] > 0:
        batch_status = "error"
    elif status_counts["completed"] == total_jobs and total_jobs > 0:
        batch_status = "completed"
    else:
        batch_status = batch.get("status", "queued")

    return {
        "batch_id": batch_id,
        "status": batch_status,
        "overall_progress": overall_progress,
        "total_jobs": total_jobs,
        "max_concurrent": batch.get("max_concurrent", 1),
        "active_task_id": batch.get("active_task_id"),
        "active_task_ids": list(batch.get("active_task_ids") or []),
        "status_counts": status_counts,
        "jobs": jobs,
    }


@render_bp.route("/batch/<batch_id>/status", methods=["GET"])
def render_batch_status(batch_id):
    batch = _render_batches.get(batch_id)
    if not batch:
        return jsonify({"error": "批量任务不存在"}), 404
    return jsonify(_build_batch_status_payload(batch_id))


@render_bp.route("/batch/<batch_id>/cancel", methods=["POST"])
def render_batch_cancel(batch_id):
    batch = _render_batches.get(batch_id)
    if not batch:
        return jsonify({"error": "批量任务不存在"}), 404

    batch["cancelled"] = True
    for job in batch["jobs"]:
        task = _render_tasks.get(job["task_id"])
        if not task:
            continue
        task["cancelled"] = True
        if task["status"] == "queued":
            task["status"] = "cancelled"
            task["phase"] = "cancelled"
        pipeline = task.get("_pipeline")
        if pipeline:
            pipeline.cancel()

    return jsonify(_build_batch_status_payload(batch_id))


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
