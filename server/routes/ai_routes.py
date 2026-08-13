# -*- coding: utf-8 -*-
"""
AI 智能出行规划路由
- POST /api/ai/plan    根据出行时间/天数/预算等生成完整规划（目的地、交通、住宿）
- GET  /api/ai/status  查询大模型配置状态（前端用于友好提示）
"""
from flask import Blueprint, request, jsonify

import ai_planner

ai_bp = Blueprint("ai", __name__, url_prefix="/api/ai")


def ok(data=None, msg="success"):
    return jsonify({"code": 0, "msg": msg, "data": data})


def fail(msg, code=1, http=200):
    return jsonify({"code": code, "msg": msg, "data": None}), http


@ai_bp.route("/status", methods=["GET"])
def ai_status():
    """返回大模型配置状态（是否可用、当前厂商/模型），不暴露 Key"""
    configured = ai_planner.is_configured()
    name, prov = ai_planner._resolve_provider()
    hint = ""
    if not configured:
        if prov.get("need_key", False):
            hint = f"当前厂商 {name} 需要 API Key，请在环境变量 AI_API_KEY 中配置"
        else:
            hint = f"当前厂商 {name} 未检测到服务，请先启动（如: ollama serve）并确保模型已下载"
    return ok({
        "configured": configured,
        "provider": name,
        "model": prov.get("model", ""),
        "base_url": prov.get("base_url", ""),
        "hint": hint,
    })


@ai_bp.route("/plan", methods=["POST"])
def ai_plan():
    """AI 出行规划
    请求体: {
        days: 游玩天数(int, 1-30),
        budget: 总预算元(float, 100-1000000),
        month: 出行月份(str, 如 "6月" / "2026-06" 或 1-12),
        travelers: 出行人数(int, 1-20),
        departure: 出发城市(str, 默认 上海),
        preferences: 偏好(str, 可选)
    }
    返回: 结构化规划（目的地/交通/住宿/行程/美食/预算/贴士）
    """
    data = request.get_json(silent=True) or {}

    # ---- 参数清洗与校验 ----
    try:
        days = int(data.get("days", 7))
    except (TypeError, ValueError):
        days = 7
    if not 1 <= days <= 30:
        return fail("游玩天数需在 1-30 天之间", http=400)

    try:
        budget = float(data.get("budget", 10000))
    except (TypeError, ValueError):
        budget = 10000
    if not 100 <= budget <= 1000000:
        return fail("预算需在 100-1000000 元之间", http=400)
    budget = int(budget)

    try:
        travelers = int(data.get("travelers", 2))
    except (TypeError, ValueError):
        travelers = 2
    if not 1 <= travelers <= 20:
        return fail("出行人数需在 1-20 人之间", http=400)

    # 出行月份：兼容 "6" / "6月" / "2026-06" / "2026年6月" 等形式
    raw_month = str(data.get("month", "") or "").strip()
    if raw_month.isdigit():
        month = f"{int(raw_month)}月"
    elif "月" in raw_month:
        month = raw_month
    elif raw_month:
        import re
        m = re.search(r"(\d{1,2})", raw_month)
        month = f"{int(m.group(1))}月" if m else raw_month
    else:
        month = "6月"

    departure = (str(data.get("departure", "")).strip() or "上海")
    preferences = (str(data.get("preferences", "")).strip() or "")

    if not ai_planner.is_configured():
        name, prov = ai_planner._resolve_provider()
        return fail(
            f"尚未配置 AI 服务（当前厂商：{name}，模型：{prov.get('model', '')}）。"
            f"请在 server/config.py 中设置 AI_API_KEY（申请地址见 README）后重试。",
            code=2,
        )

    try:
        result = ai_planner.generate_travel_plan(
            days=days, budget=budget, month=month,
            travelers=travelers, departure=departure, preferences=preferences,
        )
    except Exception as e:
        return fail(f"AI 规划失败：{e}", code=3)

    # 组装返回
    payload = {
        "inputs": {
            "days": days,
            "budget": budget,
            "month": month,
            "travelers": travelers,
            "departure": departure,
            "preferences": preferences,
        },
        "plan": result,
    }
    return ok(payload)
