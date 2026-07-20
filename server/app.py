# -*- coding: utf-8 -*-
"""
国际旅游规划助手 - 后端服务入口
启动方式: python app.py
"""
import sys
import traceback
from flask import Flask, jsonify, request, send_from_directory

from config import Config
from models import init_db

import threading
import time
import webbrowser

# 注册路由
from routes.auth_routes import auth_bp
from routes.trip_routes import trip_bp
from routes.city_routes import city_bp
from routes.note_routes import note_bp
from routes.stats_routes import stats_bp
from routes.favorite_routes import favorite_bp
from routes.ai_routes import ai_bp
from routes.plan_routes import plan_bp
from routes.landmark_routes import landmarks_bp
from routes.share_routes import share_bp
from routes.weather_routes import weather_bp
from routes.poi_routes import poi_bp

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)


def create_app():
    # 禁用 Flask 默认 static 目录，改用自定义静态托管（指向项目根 static/）
    app = Flask(__name__, static_folder=None)
    app.config["JSON_AS_ASCII"] = False

    # ---------- CORS（跨域支持，供前后端分离开发调试） ----------
    # 默认 ["*"]（开发方便）；部署时可用 ALLOWED_ORIGINS="http://a.com,http://b.com" 收紧
    _allowed = Config.ALLOWED_ORIGINS

    def _pick_origin(origin):
        if "*" in _allowed:
            return "*"
        return origin if origin in _allowed else ""

    @app.after_request
    def add_cors_headers(resp):
        origin = request.headers.get("Origin", "")
        allowed = _pick_origin(origin)
        if allowed:
            resp.headers["Access-Control-Allow-Origin"] = allowed
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            resp.headers["Access-Control-Max-Age"] = "3600"
        return resp

    @app.before_request
    def handle_options():
        if request.method == "OPTIONS":
            return add_cors_headers(app.make_default_options_response())

    # 初始化数据库（建表 + 导入城市数据）
    init_db()

    # ---------- API 路由 ----------
    app.register_blueprint(auth_bp)
    app.register_blueprint(trip_bp)
    app.register_blueprint(city_bp)
    app.register_blueprint(note_bp)
    app.register_blueprint(stats_bp)
    app.register_blueprint(favorite_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(plan_bp)
    app.register_blueprint(landmarks_bp)
    app.register_blueprint(share_bp)
    app.register_blueprint(weather_bp)
    app.register_blueprint(poi_bp)

    @app.get("/api/health")
    def health():
        return jsonify({"code": 0, "msg": "ok", "data": {"status": "running"}})

    @app.get("/api/config/frontend")
    def frontend_config():
        """前端运行时配置：是否启用高德引擎+key、AI provider等"""
        from config import Config
        cn = bool(Config.AMAP_JS_KEY and Config.AMAP_WEB_KEY)
        return jsonify({"code": 0, "msg": "ok", "data": {
            "provider": "amap" if cn else "osm",
            "amap": {
                "enabled": cn,
                "js_key": Config.AMAP_JS_KEY,
                "js_secret": Config.AMAP_JS_SECRET,
            },
            "ai_provider": Config.AI_PROVIDER,
        }})

    # ---------- 前端页面托管（前后端一体部署） ----------
    @app.get("/")
    def index_page():
        return send_from_directory(PROJECT_DIR, "index.html")

    @app.get("/pages/<path:filename>")
    def pages(filename):
        return send_from_directory(os.path.join(PROJECT_DIR, "pages"), filename)

    @app.get("/static/<path:filename>")
    def statics(filename):
        return send_from_directory(os.path.join(PROJECT_DIR, "static"), filename)

    @app.errorhandler(404)
    def not_found(e):
        if request.path.startswith("/api/"):
            return jsonify({"code": 404, "msg": "接口不存在", "data": None}), 404
        # 非 API 的 404：尝试当作前端页面返回（支持刷新）
        return send_from_directory(PROJECT_DIR, "index.html")

    @app.errorhandler(Exception)
    def handle_any_exception(e):
        code = getattr(e, "code", None)
        if not isinstance(code, int) or not 400 <= code < 600:
            code = 500
        # 打印堆栈到 stderr（start.bat 运行时能直接看到）
        print("[API %s] %s %s -> %s" % (code, request.method, request.path, repr(e)), file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        # 5xx 不把内部异常信息返回给前端（避免泄露实现细节/表结构），详情留在服务端日志；
        # 4xx 保留原始提示，方便定位参数问题
        if code >= 500:
            msg = "服务器内部错误，请稍后重试（详情见服务端日志）"
        else:
            msg = getattr(e, "description", None) or str(e) or "请求有误"
        payload = {"code": code, "msg": msg, "data": None}
        if code != 404 and request.path.startswith("/api/"):
            return jsonify(payload), code
        if 400 <= code < 600:
            return jsonify(payload), code
        return jsonify({"code": 500, "msg": "服务器内部错误"}), 500

    return app


app = create_app()

if __name__ == "__main__":
    url = f"http://{Config.HOST}:{Config.PORT}"
    print(f"\n[国际旅游规划助手] 后端启动: {url}")
    print(f"[国际旅游规划助手] 数据库: {Config.DB_TYPE.upper()} | 调试模式: {'开' if Config.DEBUG else '关'}\n")

    # 服务器就绪后自动打开浏览器（延迟 1.5 秒，等 Flask 完成监听）
    def _open_browser():
        time.sleep(1.5)
        try:
            webbrowser.open(url)
        except Exception:
            pass  # 打开失败不影响服务运行

    threading.Thread(target=_open_browser, daemon=True).start()

    # use_reloader=False：避免多进程导致静态资源/数据库连接冲突
    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG, use_reloader=False)
