"""本地文件浏览 API"""
import os
from flask import Blueprint, request, jsonify

files_bp = Blueprint("files", __name__)

# 允许浏览的根目录（安全限制）
_ALLOWED_ROOTS = [
    "C:\\Projects",
    "C:\\Users",
]

# 首次访问时动态检测可用驱动器
def _get_available_roots():
    """获取可用的根目录"""
    roots = list(_ALLOWED_ROOTS)
    import string
    for letter in string.ascii_uppercase:
        drive = f"{letter}:\\"
        if os.path.isdir(drive) and drive not in roots:
            roots.append(drive)
    return roots


def _is_path_allowed(path: str) -> bool:
    """检查路径是否在允许范围内"""
    norm = os.path.normpath(path)
    for root in _get_available_roots():
        if norm.lower().startswith(root.lower()):
            return True
    return False


@files_bp.route("/browse", methods=["GET"])
def browse_directory():
    """浏览目录内容 ?path=D:\\DCIM"""
    path = request.args.get("path", "").strip().strip('"').strip("'")

    if not path:
        # 返回允许的根目录列表
        roots = []
        for r in _get_available_roots():
            if os.path.isdir(r):
                roots.append(r)
        return jsonify({"path": "", "parent": "", "dirs": roots, "files": []})

    # 安全检查
    if not _is_path_allowed(path):
        return jsonify({"error": f"路径不在允许范围内: {path}"}), 403

    if not os.path.isdir(path):
        return jsonify({"error": f"目录不存在: {path}"}), 404

    try:
        parent = os.path.dirname(path)
        if not _is_path_allowed(parent):
            parent = ""

        dirs = []
        files = []

        for entry in os.scandir(path):
            try:
                name = entry.name
                if name.startswith("."):
                    continue  # 跳过隐藏文件
                if entry.is_dir():
                    dirs.append({
                        "name": name,
                        "path": entry.path,
                    })
                elif entry.is_file():
                    ext = os.path.splitext(name)[1].lower()
                    size = entry.stat().st_size
                    files.append({
                        "name": name,
                        "path": entry.path,
                        "size": size,
                        "ext": ext,
                    })
            except (PermissionError, OSError):
                continue

        # 排序
        dirs.sort(key=lambda x: x["name"].lower())
        files.sort(key=lambda x: x["name"].lower())

        return jsonify({
            "path": path,
            "parent": parent,
            "dirs": dirs,
            "files": files,
        })

    except PermissionError:
        return jsonify({"error": "权限不足"}), 403
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@files_bp.route("/browse/filter", methods=["GET"])
def browse_filtered():
    """浏览目录，只返回特定扩展名的文件 ?path=D:\\DCIM&ext=.fit,.mp4"""
    path = request.args.get("path", "").strip().strip('"').strip("'")
    exts = request.args.get("ext", "").lower().split(",")
    exts = [e.strip() for e in exts if e.strip()]

    if not path:
        roots = []
        for r in _get_available_roots():
            if os.path.isdir(r):
                roots.append(r)
        return jsonify({"path": "", "parent": "", "dirs": roots, "files": []})

    if not _is_path_allowed(path):
        return jsonify({"error": f"路径不在允许范围内: {path}"}), 403

    if not os.path.isdir(path):
        return jsonify({"error": f"目录不存在: {path}"}), 404

    try:
        parent = os.path.dirname(path)
        if not _is_path_allowed(parent):
            parent = ""

        dirs = []
        files = []

        for entry in os.scandir(path):
            try:
                name = entry.name
                if name.startswith("."):
                    continue
                if entry.is_dir():
                    dirs.append({
                        "name": name,
                        "path": entry.path,
                    })
                elif entry.is_file():
                    ext = os.path.splitext(name)[1].lower()
                    if exts and ext not in exts:
                        continue
                    size = entry.stat().st_size
                    files.append({
                        "name": name,
                        "path": entry.path,
                        "size": size,
                        "ext": ext,
                    })
            except (PermissionError, OSError):
                continue

        dirs.sort(key=lambda x: x["name"].lower())
        files.sort(key=lambda x: x["name"].lower())

        return jsonify({
            "path": path,
            "parent": parent,
            "dirs": dirs,
            "files": files,
        })

    except PermissionError:
        return jsonify({"error": "权限不足"}), 403
    except Exception as e:
        return jsonify({"error": str(e)}), 500
