"""项目模型"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import os
import uuid

import yaml

from models.video_config import VideoConfig, TimeSyncConfig
from models.overlay_template import WidgetConfig
from models.fit_data import SanitizeConfig, SmoothingConfig


@dataclass
class Project:
    """项目配置"""
    id: str = ""
    name: str = ""
    created_at: str = ""
    fit_path: str = ""
    video_path: str = ""
    video_config: Optional[VideoConfig] = None
    overlay_template_name: str = ""
    widgets: list = field(default_factory=list)  # list[WidgetConfig]
    global_style: dict = field(default_factory=dict)
    render_settings: dict = field(default_factory=dict)
    sanitize_config: Optional[SanitizeConfig] = None
    smoothing_config: Optional[SmoothingConfig] = None

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if self.video_config is None:
            self.video_config = VideoConfig()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "fit_path": self.fit_path,
            "video_path": self.video_path,
            "video_config": self.video_config.to_dict() if self.video_config else None,
            "overlay_template_name": self.overlay_template_name,
            "widgets": [w.to_dict() for w in self.widgets],
            "global_style": self.global_style,
            "render_settings": self.render_settings,
            "sanitize_config": self.sanitize_config.to_dict() if self.sanitize_config else None,
            "smoothing_config": self.smoothing_config.to_dict() if self.smoothing_config else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        vc_data = d.get("video_config")
        video_config = None
        if vc_data:
            from models.video_config import VideoInfo
            vi_data = vc_data.get("video_info")
            video_info = VideoInfo(**vi_data) if vi_data else None
            ts_data = vc_data.get("time_sync")
            time_sync = TimeSyncConfig.from_dict(ts_data) if ts_data else TimeSyncConfig()
            video_config = VideoConfig(
                video_info=video_info,
                time_sync=time_sync,
                output_path=vc_data.get("output_path", ""),
            )

        widgets = [WidgetConfig.from_dict(w) for w in d.get("widgets", [])]

        sanitize_config = None
        sc_data = d.get("sanitize_config")
        if sc_data:
            sanitize_config = SanitizeConfig.from_dict(sc_data)

        smoothing_config = None
        sm_data = d.get("smoothing_config")
        if sm_data:
            smoothing_config = SmoothingConfig.from_dict(sm_data)

        return cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            created_at=d.get("created_at", ""),
            fit_path=d.get("fit_path", ""),
            video_path=d.get("video_path", ""),
            video_config=video_config,
            overlay_template_name=d.get("overlay_template_name", ""),
            widgets=widgets,
            global_style=d.get("global_style", {}),
            render_settings=d.get("render_settings", {}),
            sanitize_config=sanitize_config,
            smoothing_config=smoothing_config,
        )

    def save(self, projects_dir: str):
        """保存到 YAML 文件"""
        path = os.path.join(projects_dir, f"{self.id}.yaml")
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(self.to_dict(), f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    @classmethod
    def load(cls, project_id: str, projects_dir: str) -> Optional["Project"]:
        """从 YAML 文件加载（兼容旧 JSON 格式）"""
        # 优先 YAML
        yaml_path = os.path.join(projects_dir, f"{project_id}.yaml")
        json_path = os.path.join(projects_dir, f"{project_id}.json")

        if os.path.exists(yaml_path):
            with open(yaml_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return cls.from_dict(data)
        elif os.path.exists(json_path):
            # 兼容旧 JSON 格式
            import json
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls.from_dict(data)
        return None

    @classmethod
    def list_projects(cls, projects_dir: str) -> list[dict]:
        """列出所有项目摘要"""
        result = []
        if not os.path.isdir(projects_dir):
            return result
        for fname in os.listdir(projects_dir):
            if fname.endswith((".yaml", ".yml", ".json")):
                fpath = os.path.join(projects_dir, fname)
                try:
                    project = cls.load(fname.rsplit(".", 1)[0], projects_dir)
                    if project:
                        result.append({
                            "id": project.id,
                            "name": project.name,
                            "created_at": project.created_at,
                            "fit_path": project.fit_path,
                            "video_path": project.video_path,
                            "widget_count": len(project.widgets),
                        })
                except Exception:
                    pass
        result.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return result

    @classmethod
    def delete(cls, project_id: str, projects_dir: str) -> bool:
        """删除项目文件"""
        for ext in (".yaml", ".yml", ".json"):
            fpath = os.path.join(projects_dir, f"{project_id}{ext}")
            if os.path.exists(fpath):
                os.remove(fpath)
                return True
        return False
