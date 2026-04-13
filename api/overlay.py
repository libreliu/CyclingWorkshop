"""叠加层 API"""
from flask import Blueprint, request, jsonify

from models.overlay_template import TEMPLATES, WidgetConfig

overlay_bp = Blueprint("overlay", __name__)


@overlay_bp.route("/templates", methods=["GET"])
def list_templates():
    """获取内置叠加模板列表"""
    result = []
    for key, tpl in TEMPLATES.items():
        result.append({
            "name": key,
            "display_name": tpl.name,
            "description": tpl.description,
        })
    return jsonify({"templates": result})


@overlay_bp.route("/template/<name>", methods=["GET"])
def get_template(name):
    """获取模板详情（含 Widget 配置）"""
    tpl = TEMPLATES.get(name)
    if not tpl:
        return jsonify({"error": f"模板不存在: {name}"}), 404

    w = request.args.get("width", 1920, type=int)
    h = request.args.get("height", 1080, type=int)
    return jsonify(tpl.to_dict(w, h))


@overlay_bp.route("/widget-types", methods=["GET"])
def widget_types():
    """获取可用 Widget 类型"""
    types = [
        {"type": "MapTrack", "label": "轨迹地图", "data_fields": ["track"],
         "description": "带当前位置标记和已走路径高亮的轨迹地图"},
        {"type": "SpeedGauge", "label": "速度表", "data_fields": ["speed"],
         "description": "速度表盘（数字/圆弧）"},
        {"type": "HeartRateGauge", "label": "心率表", "data_fields": ["heart_rate"],
         "description": "心率表盘（数字/色带）"},
        {"type": "CadenceGauge", "label": "踏频表", "data_fields": ["cadence"],
         "description": "踏频表盘"},
        {"type": "PowerGauge", "label": "功率表", "data_fields": ["power"],
         "description": "功率表盘"},
        {"type": "AltitudeChart", "label": "海拔剖面图", "data_fields": ["altitude"],
         "description": "海拔剖面图（带当前位置标记）"},
        {"type": "ElevationGauge", "label": "海拔数字", "data_fields": ["altitude"],
         "description": "当前海拔数字显示"},
        {"type": "DistanceCounter", "label": "累计距离", "data_fields": ["distance"],
         "description": "累计距离计数器"},
        {"type": "TimerDisplay", "label": "运动时间", "data_fields": ["timestamp"],
         "description": "运动时间/总时间显示"},
        {"type": "GradientIndicator", "label": "坡度指示", "data_fields": ["gradient"],
         "description": "当前坡度指示"},
        {"type": "CustomLabel", "label": "自定义标签", "data_fields": [],
         "description": "自定义文字标签"},
    ]
    return jsonify({"types": types})
