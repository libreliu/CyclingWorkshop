"""批量视频项目/渲染基础回归测试。"""

import os
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app import app
from models.project import Project
from api.fit import _fit_cache
from api.render import _render_batch_worker, _render_batches, _render_tasks, _new_render_task


class _FakeThread:
    def __init__(self, target=None, args=None, daemon=None):
        self.target = target
        self.args = args or ()
        self.daemon = daemon

    def start(self):
        # 测试中不实际启动后台线程；只验证 batch/job 创建。
        return None


class TestBatchProjectAndRender(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        _fit_cache.clear()
        _render_batches.clear()
        _render_tasks.clear()

    def test_project_roundtrip_with_video_items(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project = Project(
                name="Batch Demo",
                fit_path="demo.fit",
                video_items=[
                    {
                        "id": "v1",
                        "video_path": r"C:\Videos\a.mp4",
                        "time_sync": {"offset_seconds": 1.5, "time_scale": 30},
                        "render_settings": {"output_path": r"C:\Videos\a_overlay.mp4"},
                    },
                    {
                        "id": "v2",
                        "video_path": r"C:\Videos\b.mp4",
                        "time_sync": {"offset_seconds": -0.5, "time_scale": 30},
                        "render_settings": {"output_path": r"C:\Videos\b_overlay.mp4"},
                    },
                ],
            )
            project.save(tmpdir)

            loaded = Project.load(project.id, tmpdir)
            self.assertIsNotNone(loaded)
            self.assertEqual(len(loaded.video_items), 2)
            self.assertEqual(loaded.video_items[0]["video_path"], r"C:\Videos\a.mp4")
            self.assertEqual(loaded.to_dict()["video_path"], r"C:\Videos\a.mp4")

    def test_project_api_create_with_video_items(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = {
                "name": "Batch API Project",
                "fit_path": "demo.fit",
                "video_items": [
                    {
                        "id": "v1",
                        "video_path": r"C:\Videos\a.mp4",
                        "time_sync": {"offset_seconds": 0, "time_scale": 30},
                    },
                    {
                        "id": "v2",
                        "video_path": r"C:\Videos\b.mp4",
                        "time_sync": {"offset_seconds": 2, "time_scale": 30},
                    },
                ],
            }
            with patch("config.PROJECTS_DIR", tmpdir), patch("api.project.config.PROJECTS_DIR", tmpdir):
                resp = self.client.post("/api/project/", json=payload)
                self.assertEqual(resp.status_code, 201, resp.get_data(as_text=True))
                data = resp.get_json()
                self.assertEqual(len(data["video_items"]), 2)
                self.assertEqual(data["video_path"], r"C:\Videos\a.mp4")

    def test_render_start_creates_batch_jobs(self):
        _fit_cache["demo.fit"] = object()

        payload = {
            "project": {
                "fit_path": "demo.fit",
                "widgets": [],
                "global_style": {"bg_color": "#00000022"},
                "render_settings": {
                    "codec": "libx264",
                    "preset": "fast",
                    "crf": 23,
                    "audio": "none",
                    "batch_concurrency": 2,
                    "width": 1920,
                    "height": 1080,
                    "fps": 30,
                    "start_sec": 0,
                    "end_sec": 10,
                },
                "video_items": [
                    {
                        "id": "v1",
                        "video_path": r"C:\Videos\a.mp4",
                        "time_sync": {"video_start_time": "2026-04-04T07:38:19", "offset_seconds": 0, "time_scale": 30},
                        "render_settings": {"output_path": r"C:\Videos\a_overlay.mp4"},
                    },
                    {
                        "id": "v2",
                        "video_path": r"C:\Videos\b.mp4",
                        "time_sync": {"video_start_time": "2026-04-04T08:00:00", "offset_seconds": 1, "time_scale": 30},
                        "render_settings": {"output_path": r"C:\Videos\b_overlay.mp4"},
                    },
                ],
            }
        }

        fake_video_info = SimpleNamespace(width=1920, height=1080, fps=30.0, duration=10.0, rotation=0)
        with patch("api.render.os.path.isfile", return_value=True), \
             patch("api.render.VideoAnalyzerService.analyze", return_value=fake_video_info), \
             patch("api.render.threading.Thread", _FakeThread):
            resp = self.client.post("/api/render/start", json=payload)

        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        data = resp.get_json()
        self.assertEqual(data["total_jobs"], 2)
        self.assertEqual(len(data["jobs"]), 2)
        self.assertIn("batch_id", data)
        self.assertEqual(data["max_concurrent"], 2)
        self.assertEqual(data["jobs"][0]["status"], "queued")
        self.assertEqual(len(_render_tasks), 2)

    def test_render_batch_worker_respects_batch_concurrency(self):
        batch_id = "batch_demo"
        _render_batches[batch_id] = {
            "batch_id": batch_id,
            "status": "queued",
            "created_at": time.time(),
            "cancelled": False,
            "jobs": [],
            "active_task_id": None,
            "active_task_ids": [],
            "max_concurrent": 2,
        }

        prepared_jobs = []
        for idx in range(3):
            task_id = f"task_{idx + 1}"
            _render_tasks[task_id] = _new_render_task(
                task_id=task_id,
                total_frames=100,
                output_path=fr"C:\Videos\out_{idx + 1}.mp4",
                batch_id=batch_id,
                video_item_id=f"video_{idx + 1}",
                video_path=fr"C:\Videos\video_{idx + 1}.mp4",
                display_name=f"video_{idx + 1}.mp4",
                initial_status="queued",
            )
            _render_batches[batch_id]["jobs"].append({
                "task_id": task_id,
                "video_item_id": f"video_{idx + 1}",
                "video_path": fr"C:\Videos\video_{idx + 1}.mp4",
                "display_name": f"video_{idx + 1}.mp4",
            })
            prepared_jobs.append((task_id, {"batch_concurrency": 2}))

        state_lock = threading.Lock()
        running = 0
        peak_running = 0

        def fake_run(task_id, prepared):
            nonlocal running, peak_running
            with state_lock:
                running += 1
                peak_running = max(peak_running, running)
                _render_tasks[task_id]["status"] = "running"
            time.sleep(0.05)
            with state_lock:
                running -= 1
                _render_tasks[task_id]["status"] = "completed"
                _render_tasks[task_id]["progress"] = 100
                _render_tasks[task_id]["current_frame"] = _render_tasks[task_id]["total_frames"]

        with patch("api.render._run_prepared_render_task", side_effect=fake_run):
            _render_batch_worker(batch_id, prepared_jobs)

        self.assertEqual(peak_running, 2)
        self.assertEqual(_render_batches[batch_id]["status"], "completed")
        self.assertFalse(_render_batches[batch_id]["active_task_ids"])
        self.assertTrue(all(task["status"] == "completed" for task in _render_tasks.values()))


if __name__ == "__main__":
    unittest.main()
