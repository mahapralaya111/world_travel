# -*- coding: utf-8 -*-
"""
全局配置模块
说明：所有配置都可通过环境变量覆盖，便于同一份代码在开发机/服务器上使用不同配置。
     数据库类型由 DB_TYPE 决定：'sqlite'（零配置，单文件）或 'mysql'（需安装 pymysql）。
"""
import os
import secrets

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_or_create_secret_key():
    """获取 JWT 签名密钥：环境变量 > 本地密钥文件 > 随机生成并落盘

    不要使用硬编码的公开默认密钥（任何人都能伪造 token）。
    密钥首次运行会写入 server/.secret_key，重启后保持不变，已登录用户不会掉线。
    """
    env_key = (os.environ.get("SECRET_KEY") or "").strip()
    if env_key:
        return env_key
    key_file = os.path.join(BASE_DIR, ".secret_key")
    try:
        if os.path.exists(key_file):
            with open(key_file, "r", encoding="utf-8") as f:
                saved = f.read().strip()
            if saved:
                return saved
        new_key = secrets.token_hex(32)
        with open(key_file, "w", encoding="utf-8") as f:
            f.write(new_key)
        print("[config] 已生成随机 SECRET_KEY 并保存到 %s（可用环境变量 SECRET_KEY 覆盖）" % key_file)
        return new_key
    except OSError as e:
        print("[config] 密钥文件不可读写(%s)，本次使用临时随机密钥（重启后需重新登录）" % e)
        return secrets.token_hex(32)


class Config:
    # ---------- 安全 ----------
    SECRET_KEY = _load_or_create_secret_key()
    # JWT 有效期（小时）
    JWT_EXPIRES_HOURS = 24 * 7
    # 允许跨域的来源：默认 *（开发方便）；部署时可用 ALLOWED_ORIGINS 收紧，如 "http://your-domain.com"
    ALLOWED_ORIGINS = [o.strip() for o in (os.environ.get("ALLOWED_ORIGINS") or "*").split(",")
                       if o.strip()] or ["*"]

    # ---------- 数据库 ----------
    # 'sqlite' | 'mysql'（本机默认连 MySQL，可用 DB_TYPE=sqlite 切到零配置的 SQLite；
    #  详细切换步骤见 docs/MySQL安装切换指南.md）
    DB_TYPE = os.environ.get("DB_TYPE", "mysql")
    # SQLite 数据库文件路径（DB_TYPE='sqlite' 时生效）
    SQLITE_PATH = os.environ.get("SQLITE_PATH", os.path.join(BASE_DIR, "travel_planner.db"))
    # MySQL 连接参数（DB_TYPE='mysql' 时生效，需 pip install pymysql）
    MYSQL_CONFIG = {
        "host": os.environ.get("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.environ.get("MYSQL_PORT", 3306)),
        "user": os.environ.get("MYSQL_USER", "root"),
        "password": os.environ.get("MYSQL_PASSWORD", ""),
        "database": os.environ.get("MYSQL_DB", "travel_planner"),
    }

    # ---------- AI 大模型（智能出行规划） ----------
    # 厂商: ollama(本地默认) | deepseek | qwen | glm | openai
    #   ollama:  本地部署，零成本，需先运行 ollama serve 并 ollama pull qwen2.5:7b
    #   deepseek: 国内可访问，需在 https://platform.deepseek.com 申请 API Key
    #   qwen:    阿里通义千问 DashScope 兼容模式
    #   glm:     智谱 glm-4-flash 有免费额度
    AI_PROVIDER = os.environ.get("AI_PROVIDER", "ollama")
    # API Key（云端厂商必填，本地 ollama 无需 Key）
    AI_API_KEY = os.environ.get("AI_API_KEY", "")
    # 自定义接口地址/模型（可选，覆盖厂商默认值）
    # 本地 Ollama 默认: AI_BASE_URL=http://127.0.0.1:11434/v1
    AI_BASE_URL = os.environ.get("AI_BASE_URL", "")
    AI_MODEL = os.environ.get("AI_MODEL", "")

    # ---------- 高德开放平台（境内地图/POI/天气，双地图方案） ----------
    # 注册 https://console.amap.com 创建应用后填入；留空则境内也走国际方案
    # JS Key    = "Web端(JS API)"       类型（前端浏览器用）
    # JS Secret = "Web端(JS API)"       配套的 jscode
    # WEB Key   = "Web服务"             类型（后端 REST 用）
    AMAP_JS_KEY = os.environ.get("AMAP_JS_KEY", "")
    AMAP_JS_SECRET = os.environ.get("AMAP_JS_SECRET", "")
    AMAP_WEB_KEY = os.environ.get("AMAP_WEB_KEY", "")

    # ---------- 静态数据 ----------
    DATA_DIR = os.path.join(BASE_DIR, "data")

    # ---------- 服务 ----------
    HOST = os.environ.get("HOST", "127.0.0.1")
    try:
        PORT = int(os.environ.get("PORT", 5000))
    except (TypeError, ValueError):
        PORT = 5000
    # 调试模式：显式设置 DEBUG 时以它为准；
    # 未设置时"仅监听本机=开发环境开调试，监听外部地址=自动关闭"，
    # 避免用 HOST=0.0.0.0 部署时把 Flask 调试器暴露到公网（RCE 风险）
    _debug_env = os.environ.get("DEBUG")
    if _debug_env is not None:
        DEBUG = _debug_env.strip().lower() in ("1", "true", "yes", "on")
    else:
        DEBUG = HOST in ("127.0.0.1", "localhost")
