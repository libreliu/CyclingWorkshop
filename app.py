"""CyclingWorkshop Flask 应用入口"""
from flask import Flask, send_from_directory
from flask_socketio import SocketIO
import config

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["SECRET_KEY"] = config.SECRET_KEY

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ── 注册 Blueprint ────────────────────────────────
from api.fit import fit_bp
from api.video import video_bp
from api.overlay import overlay_bp
from api.render import render_bp
from api.project import project_bp
from api.files import files_bp
from api.tiles import tiles_bp

app.register_blueprint(fit_bp, url_prefix="/api/fit")
app.register_blueprint(video_bp, url_prefix="/api/video")
app.register_blueprint(overlay_bp, url_prefix="/api/overlay")
app.register_blueprint(render_bp, url_prefix="/api/render")
app.register_blueprint(project_bp, url_prefix="/api/project")
app.register_blueprint(files_bp, url_prefix="/api/files")
app.register_blueprint(tiles_bp, url_prefix="/api/tiles")


# ── 前端路由 ──────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("templates", "index.html")


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory("static", filename)


if __name__ == "__main__":
    print(f"🚴 CyclingWorkshop 启动: http://{config.HOST}:{config.PORT}")
    print(f"   FFmpeg: {config.FFMPEG_PATH}")
    socketio.run(app, host=config.HOST, port=config.PORT, debug=config.DEBUG, allow_unsafe_werkzeug=True)
