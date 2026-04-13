"""叠加层设计服务"""
from models.overlay_template import TEMPLATES, WidgetConfig


class OverlayDesignerService:
    """叠加层布局/样式管理"""

    @staticmethod
    def get_template(name: str, canvas_width: int = 1920, canvas_height: int = 1080):
        tpl = TEMPLATES.get(name)
        if not tpl:
            return None
        return tpl.create_widgets(canvas_width, canvas_height)

    @staticmethod
    def apply_template(name: str, canvas_width: int, canvas_height: int) -> list:
        """应用模板，返回 Widget 列表"""
        widgets = OverlayDesignerService.get_template(name, canvas_width, canvas_height)
        return widgets if widgets else []

    @staticmethod
    def list_templates() -> list:
        return [
            {"name": k, "display_name": v.name, "description": v.description}
            for k, v in TEMPLATES.items()
        ]
