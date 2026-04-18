"""项目 API"""
from flask import Blueprint, request, jsonify
import config
from models.project import Project

project_bp = Blueprint("project", __name__)


@project_bp.route("/", methods=["GET"])
def list_projects():
    """列出所有项目"""
    projects = Project.list_projects(config.PROJECTS_DIR)
    return jsonify(projects)


@project_bp.route("/", methods=["POST"])
def create_project():
    """创建项目"""
    body = request.get_json(silent=True) or {}
    project = Project(
        name=body.get("name", "未命名项目"),
        fit_path=body.get("fit_path", ""),
        video_path=body.get("video_path", ""),
        overlay_template_name=body.get("overlay_template_name", ""),
        global_style=body.get("global_style", {}) or {},
        render_settings=body.get("render_settings", {}) or {},
    )
    if "widgets" in body:
        from models.overlay_template import WidgetConfig
        project.widgets = [WidgetConfig.from_dict(w) for w in body["widgets"]]
    if "time_sync" in body:
        from models.video_config import TimeSyncConfig
        project.video_config.time_sync = TimeSyncConfig.from_dict(body["time_sync"])
    if "sanitize_config" in body:
        from models.fit_data import SanitizeConfig
        project.sanitize_config = SanitizeConfig.from_dict(body["sanitize_config"])
    if "smoothing_config" in body:
        from models.fit_data import SmoothingConfig
        project.smoothing_config = SmoothingConfig.from_dict(body["smoothing_config"])
    project.save(config.PROJECTS_DIR)
    return jsonify(project.to_dict()), 201


@project_bp.route("/<project_id>", methods=["GET"])
def get_project(project_id):
    """获取项目详情"""
    project = Project.load(project_id, config.PROJECTS_DIR)
    if not project:
        return jsonify({"error": "项目不存在"}), 404
    return jsonify(project.to_dict())


@project_bp.route("/<project_id>", methods=["PUT"])
def update_project(project_id):
    """更新项目配置"""
    project = Project.load(project_id, config.PROJECTS_DIR)
    if not project:
        return jsonify({"error": "项目不存在"}), 404

    body = request.get_json(silent=True) or {}

    if "name" in body:
        project.name = body["name"]
    if "fit_path" in body:
        project.fit_path = body["fit_path"]
    if "video_path" in body:
        project.video_path = body["video_path"]
    if "overlay_template_name" in body:
        project.overlay_template_name = body["overlay_template_name"]
    if "widgets" in body:
        from models.overlay_template import WidgetConfig
        project.widgets = [WidgetConfig.from_dict(w) for w in body["widgets"]]
    if "global_style" in body:
        project.global_style = body["global_style"] or {}
    if "time_sync" in body:
        from models.video_config import TimeSyncConfig
        project.video_config.time_sync = TimeSyncConfig.from_dict(body["time_sync"])
    if "render_settings" in body:
        project.render_settings = body["render_settings"]
    if "sanitize_config" in body:
        from models.fit_data import SanitizeConfig
        project.sanitize_config = SanitizeConfig.from_dict(body["sanitize_config"])
    if "smoothing_config" in body:
        from models.fit_data import SmoothingConfig
        project.smoothing_config = SmoothingConfig.from_dict(body["smoothing_config"])

    project.save(config.PROJECTS_DIR)
    return jsonify(project.to_dict())


@project_bp.route("/<project_id>", methods=["DELETE"])
def delete_project(project_id):
    """删除项目"""
    success = Project.delete(project_id, config.PROJECTS_DIR)
    if not success:
        return jsonify({"error": "项目不存在"}), 404
    return jsonify({"ok": True})
