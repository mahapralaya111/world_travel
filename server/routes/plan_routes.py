# -*- coding: utf-8 -*-
"""
行程规划路由（Plan Engine / Intent Engine / Offline Pack 的 HTTP 入口）

接口清单：
  POST /api/plan/generate        生成行程（无需登录，数据确定性可复现）
  GET  /api/plan/schema          意图 JSON Schema（供前端动态表单/文档）
  POST /api/plan/offline         从任意 plan 生成离线包 HTML + 二维码文本
  GET  /api/plan/offline/decode  解码 trip:xxx 数据（离线页调用）
  GET  /api/plan/offline/<id>    从已保存行程生成离线包（需登录）
  GET  /api/trips/<id>/plan      获取已保存的行程计划（需登录）
  PUT  /api/trips/<id>/plan      保存/覆盖行程计划（需登录）
  POST /api/trips/<id>/plan/intent 自然语言/意图指令调整行程（需登录）
"""
import json

from flask import Blueprint, jsonify, request

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth import verify_token, get_token_from_request
from database import query_one, query_all, execute

from plan_engine import generate_plan, validate_plan, plan_to_markdown
import intent_engine
import offline_pack

plan_bp = Blueprint("plan", __name__, url_prefix="/api")


def _uid():
    payload = verify_token(get_token_from_request(request))
    return payload["uid"] if payload else None


def ok(data=None, msg="success"):
    return jsonify({"code": 0, "msg": msg, "data": data})


def fail(msg, code=1, http=200):
    return jsonify({"code": code, "msg": msg, "data": None}), http


def _trip_owned(trip_id, uid):
    return query_one("SELECT * FROM trips WHERE id = ? AND user_id = ?", (trip_id, uid))


def _as_int(v, default=0):
    """安全转 int：非法值回退默认，避免 int('abc') 抛异常导致 500"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _fetch_city_rows(ids):
    """从城市表批量读取城市数据（供生成引擎使用）"""
    if not ids:
        return []
    rows = []
    for cid in ids:
        try:
            cid = int(cid)
        except (TypeError, ValueError):
            continue
        row = query_one(
            "SELECT country_code, country_zh, country_en, name, name_en, name_local, "
            "lat, lng, sights, is_capital, population FROM cities WHERE id = ?", (cid,))
        if row:
            rows.append(row)
    return rows


@plan_bp.route("/plan/generate", methods=["POST"])
def generate():
    """生成行程：body = {city_ids:[int], days, travelers, budget, interests, pace, departure, start_date, title}"""
    body = request.get_json(silent=True) or {}
    city_ids = body.get("city_ids") or body.get("cities") or []
    if not city_ids:
        return fail("请至少选择一个目的地城市")
    rows = _fetch_city_rows(city_ids)
    if not rows:
        return fail("城市数据不存在")

    # 组装生成引擎所需的目的地结构
    destinations = []
    for r in rows:
        try:
            sights = json.loads(r.get("sights") or "[]")
        except Exception:
            sights = []
        destinations.append({
            "city": r["name"], "country": r["country_zh"] or r["country_en"],
            "country_code": r["country_code"], "lat": r["lat"], "lng": r["lng"],
            "population": r["population"] or 0, "is_capital": r["is_capital"] or 0,
            "sights": sights if isinstance(sights, list) else [],
        })

    # ---- 参数清洗与边界校验（避免 int('abc') / float(None) / 极端数值打崩引擎）----
    days = _as_int(body.get("days"), 7)
    if not 1 <= days <= 60:
        return fail("游玩天数需在 1-60 天之间")
    travelers = _as_int(body.get("travelers"), 2)
    if not 1 <= travelers <= 50:
        return fail("出行人数需在 1-50 人之间")
    try:
        budget = float(body.get("budget") or body.get("total_budget") or 10000)
    except (TypeError, ValueError):
        budget = 10000.0
    if budget != budget or budget < 0:  # NaN / 负数兜底
        budget = 10000.0
    interests = body.get("interests")
    if not isinstance(interests, list) or not interests:
        interests = ["city", "culture"]
    pace = body.get("pace") if body.get("pace") in ("relaxed", "normal", "intensive") else "normal"

    try:
        plan = generate_plan(
            destinations=destinations,
            days=days,
            travelers=travelers,
            total_budget=budget,
            interests=interests,
            pace=pace,
            departure=(body.get("departure") or "上海").strip()[:30],
            start_date=str(body.get("start_date") or "")[:20],
            title=(body.get("title") or "").strip()[:60] or None,
        )
    except Exception as e:
        return fail("生成失败: {}".format(e))
    return ok(plan, "行程生成成功")


@plan_bp.route("/plan/schema", methods=["GET"])
def intent_schema():
    """返回意图 Schema（供前端动态生成表单 / 功能文档）"""
    return ok({
        "intents": intent_engine.INTENT_SCHEMA,
        "interest_options": ["culture", "history", "nature", "food", "shopping", "city"],
        "pace_options": ["relaxed", "normal", "intensive"],
    })


# ---------------------------------------------------------------------------
# 已保存行程计划（trips 关联，需登录）
# ---------------------------------------------------------------------------
@plan_bp.route("/trips/<int:trip_id>/plan", methods=["GET"])
def get_plan(trip_id):
    uid = _uid()
    if not uid:
        return fail("未登录或登录已过期", 401, 401)
    if not _trip_owned(trip_id, uid):
        return fail("行程不存在", 404, 404)
    row = query_one("SELECT plan_data FROM trip_plans WHERE trip_id = ?", (trip_id,))
    if not row:
        return ok(None, "该行程尚未生成计划")
    try:
        plan = json.loads(row["plan_data"])
    except Exception:
        return fail("计划数据损坏")
    # 附带地图批注/路线（离线页 ?trip_id= 在线模式用）
    try:
        plan["map_data"] = _load_map_data(trip_id)
    except Exception:
        pass
    return ok(plan)


@plan_bp.route("/trips/<int:trip_id>/plan", methods=["PUT"])
def save_plan(trip_id):
    uid = _uid()
    if not uid:
        return fail("未登录或登录已过期", 401, 401)
    if not _trip_owned(trip_id, uid):
        return fail("行程不存在", 404, 404)
    body = request.get_json(silent=True) or {}
    plan = body.get("plan") or body.get("plan_data")
    if not plan:
        return fail("缺少行程计划数据")
    ok_flag, msg = validate_plan(plan)
    if not ok_flag:
        return fail("计划数据无效: " + msg)
    data = json.dumps(plan, ensure_ascii=False)
    existing = query_one("SELECT id FROM trip_plans WHERE trip_id = ?", (trip_id,))
    from datetime import datetime
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if existing:
        execute("UPDATE trip_plans SET plan_data=?, updated_at=? WHERE trip_id=?",
                (data, ts, trip_id))
    else:
        execute("INSERT INTO trip_plans (trip_id, plan_data, created_at, updated_at) VALUES (?,?,?,?)",
                (trip_id, data, ts, ts))
    execute("UPDATE trips SET updated_at=? WHERE id=?", (ts, trip_id))
    return ok(msg="行程计划已保存")


@plan_bp.route("/trips/<int:trip_id>/plan/intent", methods=["POST"])
def apply_intent(trip_id):
    """
    意图调整：body 两种模式
      1) {text: "自然语言"}           → 先解析意图再执行（AI 或规则）
      2) {intent: {...}}              → 直接执行结构化意图
    返回新的完整 plan（已保存到数据库）
    """
    uid = _uid()
    if not uid:
        return fail("未登录或登录已过期", 401, 401)
    if not _trip_owned(trip_id, uid):
        return fail("行程不存在", 404, 404)
    body = request.get_json(silent=True) or {}

    row = query_one("SELECT plan_data FROM trip_plans WHERE trip_id = ?", (trip_id,))
    if not row:
        return fail("请先生成行程计划", 404, 404)
    try:
        plan = json.loads(row["plan_data"])
    except Exception:
        return fail("计划数据损坏")

    intent = body.get("intent")
    provider_note = ""
    if not intent:
        text = (body.get("text") or "").strip()
        if not text:
            return fail("请提供调整内容或意图指令")
        intent, provider_note = intent_engine.parse_intent(text, use_llm=True)
        if not intent:
            return fail("无法理解您的调整要求，请换一种说法（例如：节奏放轻松一点 / 预算降低20%）")

    new_plan, msg = intent_engine.apply_intent(plan, intent)
    if new_plan is plan or msg != "ok":  # apply_intent 失败时返回原对象
        return fail("调整失败: {}".format(msg))

    ok_flag, vmsg = validate_plan(new_plan)
    if not ok_flag:
        return fail("调整后计划无效: " + vmsg)

    # 保存
    from datetime import datetime
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    execute("UPDATE trip_plans SET plan_data=?, updated_at=? WHERE trip_id=?",
            (json.dumps(new_plan, ensure_ascii=False), ts, trip_id))
    execute("UPDATE trips SET updated_at=? WHERE id=?", (ts, trip_id))

    return ok({
        "plan": new_plan,
        "intent": intent,
        "provider": provider_note,
        "msg": msg,
    }, "调整成功")


# ---------------------------------------------------------------------------
# 离线包
# ---------------------------------------------------------------------------
def _load_map_data(trip_id):
    """读取行程在地图上的批注与画线（离线包用）"""
    anns = query_all(
        "SELECT country_name, title, content, lat, lng, color FROM annotations "
        "WHERE trip_id = ? ORDER BY id", (trip_id,))
    routes = query_all(
        "SELECT country_name, name, points, color, distance FROM routes "
        "WHERE trip_id = ? ORDER BY id", (trip_id,))
    for r in routes:
        try:
            r["points"] = json.loads(r["points"] or "[]")
        except Exception:
            r["points"] = []
    return {"annotations": anns, "routes": routes}


@plan_bp.route("/plan/offline", methods=["POST"])
def build_offline():
    """从请求中的 plan 生成离线包：{plan: {...}, trip_id?: int} → {html, filename, qr_text}"""
    body = request.get_json(silent=True) or {}
    plan = body.get("plan") or body.get("plan_data")
    if not plan:
        return fail("缺少行程计划数据")

    # ====== 离线包地图数据组装（三级兜底）======
    # 优先级 1: 前端直接在 plan 里塞了 map_data.annotations/routes（最高保真，包含 localStorage 里未存 DB 的批注）
    # 优先级 2: 请求体带 trip_id → 从 DB 读 annotations/routes（已登录已存 DB 的场景）
    # 优先级 3: 都没有 → 留空（离线包无地图标注）
    existing_md = plan.get("map_data") or {}
    existing_anns = existing_md.get("annotations") if isinstance(existing_md, dict) else None
    existing_routes = existing_md.get("routes") if isinstance(existing_md, dict) else None

    trip_id = _as_int(body.get("trip_id"))
    backend_md = {"annotations": [], "routes": []}
    if trip_id:
        # 仅当行程归属当前登录用户时才从库里读，避免越权/未登录拿到他人行程的批注与路线
        uid = _uid()
        if uid and _trip_owned(trip_id, uid):
            try:
                backend_md = _load_map_data(trip_id)
            except Exception:
                backend_md = {"annotations": [], "routes": []}

    final_md = {
        # 前端直传优先；如果前端没传但 DB 有，用 DB 的；都没有就空数组
        "annotations": existing_anns if existing_anns is not None and len(existing_anns) > 0 else backend_md["annotations"],
        "routes": existing_routes if existing_routes is not None and len(existing_routes) > 0 else backend_md["routes"],
    }
    if final_md["annotations"] or final_md["routes"]:
        plan["map_data"] = final_md
    ok_flag, msg = validate_plan(plan)
    if not ok_flag:
        return fail("计划数据无效: " + msg)
    try:
        html = offline_pack.build_offline_html(plan)
        title = plan.get("meta", {}).get("title", "行程")
        filename = "行程离线包_{}.html".format(title.replace("/", "_"))
        return ok({
            "html": html,
            "filename": filename,
            "qr_text": offline_pack.qr_payload(plan),
            # 手机离线页链接数据：hash 无长度限制，始终带上 map_data（批注/路线）
            "page_text": offline_pack.page_payload(plan),
            # 二维码是否超 v40-L 物理上限（2850 bytes）—— 超了前端会提示用户用链接代替扫码
            "qr_oversized": offline_pack.qr_check_oversized(plan),
        })
    except Exception as e:
        return fail("离线包生成失败: {}".format(e))


@plan_bp.route("/plan/offline/decode", methods=["GET"])
def decode_offline():
    """离线页解码 trip:xxx → plan"""
    data = request.args.get("data") or ""
    if not data:
        return fail("缺少 data 参数")
    try:
        plan = offline_pack.deserialize_plan(data)
    except Exception as e:
        return fail("数据解码失败: {}".format(e))
    return ok(plan)


@plan_bp.route("/plan/offline/<int:trip_id>", methods=["GET"])
def build_offline_from_trip(trip_id):
    """从已保存行程生成离线包（直接返回 HTML）"""
    uid = _uid()
    if not uid:
        return fail("未登录或登录已过期", 401, 401)
    if not _trip_owned(trip_id, uid):
        return fail("行程不存在", 404, 404)
    row = query_one("SELECT plan_data FROM trip_plans WHERE trip_id = ?", (trip_id,))
    if not row:
        return fail("该行程尚未生成计划", 404, 404)
    try:
        plan = json.loads(row["plan_data"])
    except Exception:
        return fail("计划数据损坏")
    try:
        plan["map_data"] = _load_map_data(trip_id)
    except Exception:
        pass
    try:
        html = offline_pack.build_offline_html(plan)
        title = plan.get("meta", {}).get("title", "行程")
        from urllib.parse import quote
        filename = "行程离线包_{}.html".format(title.replace("/", "_"))
        from flask import Response
        return Response(
            html,
            mimetype="text/html",
            headers={"Content-Disposition": "attachment; filename*=UTF-8''{}".format(quote(filename))},
        )
    except Exception as e:
        return fail("离线包生成失败: {}".format(e))


@plan_bp.route("/plan/markdown/<int:trip_id>", methods=["GET"])
def plan_markdown(trip_id):
    """导出行程为 Markdown 文本"""
    uid = _uid()
    if not uid:
        return fail("未登录或登录已过期", 401, 401)
    if not _trip_owned(trip_id, uid):
        return fail("行程不存在", 404, 404)
    row = query_one("SELECT plan_data FROM trip_plans WHERE trip_id = ?", (trip_id,))
    if not row:
        return fail("该行程尚未生成计划", 404, 404)
    try:
        plan = json.loads(row["plan_data"])
    except Exception:
        return fail("计划数据损坏")
    return ok({"markdown": plan_to_markdown(plan), "title": plan.get("meta", {}).get("title", "行程")})
