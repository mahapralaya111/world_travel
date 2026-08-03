# -*- coding: utf-8 -*-
"""
收藏 / 智能推荐 / 智能路线规划 路由
"""
import json

from flask import Blueprint, request, jsonify

from auth import verify_token, get_token_from_request
from database import get_conn
import recommend
import planner

favorite_bp = Blueprint("favorite", __name__, url_prefix="/api")


def _uid(request):
    payload = verify_token(get_token_from_request(request))
    return payload["uid"] if payload else None


def _to_float(v, default=0.0):
    """安全转 float：空值/非法值回退默认，避免 float('abc') 抛异常"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if f == f else default  # 过滤 NaN


def ok(data=None, msg="success"):
    return jsonify({"code": 0, "msg": msg, "data": data})


def fail(msg, code=1, http=200):
    return jsonify({"code": code, "msg": msg, "data": None}), http


@favorite_bp.route("/favorites", methods=["POST"])
def add_favorite():
    """
    添加收藏
    请求体: { item_type: 'city'|'sight', city_id, city_name?, sight_name? }
    """
    uid = _uid(request)
    if not uid:
        return fail("请先登录", http=401)

    data = request.get_json(silent=True) or {}
    item_type = data.get("item_type")
    city_id = data.get("city_id")
    city_name = (data.get("city_name") or "").strip()
    if item_type not in ("city", "sight"):
        # 容错推断：带 sight_name 视为景点收藏，否则视为城市收藏
        item_type = "sight" if (data.get("sight_name") or "").strip() else "city"
    if not city_id and not city_name:
        return fail("缺少 city_id 或 city_name", http=400)

    conn = get_conn()
    try:
        # 优先按 id 查找，否则按名称查找
        city = None
        if city_id:
            city = conn.execute("SELECT * FROM cities WHERE id=?", (city_id,)).fetchone()
        if not city and city_name:
            city = conn.execute("SELECT * FROM cities WHERE name=? OR name_en=? OR name_local=? LIMIT 1",
                                (city_name, city_name, city_name)).fetchone()

        if city:
            cid = city["id"]
            cname = city["name"]
            ccode = city["country_code"]
            czh = city["country_zh"]
            clat = city["lat"]
            clng = city["lng"]
        else:
            # 前端内置数据中有但后端城市库没有 → 按前端传入信息存储（仍可收藏/规划）
            cid = 0
            cname = city_name
            ccode = data.get("country_code", "")
            czh = data.get("country_zh", "")
            clat = _to_float(data.get("lat"))
            clng = _to_float(data.get("lng"))

        sight_name = ""
        if item_type == "sight":
            sight_name = (data.get("sight_name") or "").strip()
            if not sight_name:
                return fail("缺少 sight_name", http=400)
            if city:
                sights = []
                try:
                    sights = json.loads(city["sights"] or "[]")
                except Exception:
                    sights = []
                if sight_name not in sights:
                    # 不强制报错，仅提示（允许收藏自定义景点）
                    pass

        item_key = "city_{}".format(cid) if item_type == "city" else "sight_{}_{}".format(cid, sight_name)
        tags = [czh, ccode, "城市"]
        if item_type == "sight":
            tags = [czh, ccode, recommend._sight_tag(sight_name)]

        # 已存在则幂等返回
        exist = conn.execute(
            "SELECT id FROM favorites WHERE user_id=? AND item_key=?",
            (uid, item_key),
        ).fetchone()
        if exist:
            return ok({"id": exist["id"], "already": True, "item_key": item_key}, "已收藏")

        cur = conn.execute(
            "INSERT INTO favorites (user_id, item_type, item_key, city_id, city_name, sight_name, "
            "country_code, country_zh, lat, lng, tags) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (uid, item_type, item_key, cid, cname, sight_name,
             ccode, czh, clat, clng,
             json.dumps(tags, ensure_ascii=False)),
        )
        conn.commit()
        return ok({"id": cur.lastrowid, "already": False, "item_key": item_key}, "收藏成功")
    finally:
        conn.close()


@favorite_bp.route("/favorites", methods=["GET"])
def list_favorites():
    """我的收藏列表（支持 ?item_type=city|sight 过滤）"""
    uid = _uid(request)
    if not uid:
        return fail("请先登录", http=401)

    item_type = request.args.get("item_type")
    conn = get_conn()
    try:
        if item_type in ("city", "sight"):
            rows = conn.execute(
                "SELECT * FROM favorites WHERE user_id=? AND item_type=? ORDER BY id DESC",
                (uid, item_type),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM favorites WHERE user_id=? ORDER BY id DESC",
                (uid,),
            ).fetchall()
        items = []
        for r in rows:
            tags = []
            try:
                tags = json.loads(r["tags"] or "[]")
            except Exception:
                tags = []
            items.append({
                "id": r["id"],
                "item_type": r["item_type"],
                "item_key": r["item_key"],
                "city_id": r["city_id"],
                "city_name": r["city_name"],
                "sight_name": r["sight_name"],
                "country_code": r["country_code"],
                "country_zh": r["country_zh"],
                "lat": r["lat"],
                "lng": r["lng"],
                "tags": tags,
                "created_at": r["created_at"],
            })
        return ok(items)
    finally:
        conn.close()


@favorite_bp.route("/favorites/<int:fid>", methods=["DELETE"])
def remove_favorite(fid):
    """取消收藏"""
    uid = _uid(request)
    if not uid:
        return fail("请先登录", http=401)

    conn = get_conn()
    try:
        cur = conn.execute(
            "DELETE FROM favorites WHERE id=? AND user_id=?", (fid, uid)
        )
        conn.commit()
        if cur.rowcount == 0:
            return fail("收藏不存在", http=404)
        return ok(msg="已取消收藏")
    finally:
        conn.close()


@favorite_bp.route("/favorites/check", methods=["GET"])
def check_favorite():
    """批量查询收藏状态：?keys=city_1,sight_2_故宫"""
    uid = _uid(request)
    if not uid:
        return fail("请先登录", http=401)

    keys = request.args.get("keys", "").split(",")
    # 去重 + 限长：避免超长 keys 触发 SQL 占位符数量上限（SQLite 999）
    keys = list(dict.fromkeys(k for k in keys if k))[:300]
    conn = get_conn()
    try:
        if not keys:
            return ok({})
        placeholders = ",".join("?" * len(keys))
        rows = conn.execute(
            "SELECT item_key FROM favorites WHERE user_id=? AND item_key IN ({})".format(placeholders),
            (uid, *keys),
        ).fetchall()
        return ok({r["item_key"]: True for r in rows})
    finally:
        conn.close()


@favorite_bp.route("/recommend", methods=["GET"])
def get_recommend():
    """
    智能推荐
    参数: ?item_type=sight|city&limit=8
    """
    uid = _uid(request)
    if not uid:
        return fail("请先登录", http=401)

    item_type = request.args.get("item_type", "sight")
    if item_type not in ("city", "sight"):
        item_type = "sight"
    try:
        limit = int(request.args.get("limit", 8))
    except Exception:
        limit = 8
    limit = max(1, min(limit, 20))
    result = recommend.recommend(uid, item_type=item_type, top_n=limit)
    return ok(result)


@favorite_bp.route("/plan/route", methods=["POST"])
def plan_route():
    """
    智能路线规划：基于用户收藏的景点规划最优游览顺序
    请求体: { use_favorites: true, start_index: 0 } 或 { points: [{lat,lng,name}] }
    """
    uid = _uid(request)
    if not uid:
        return fail("请先登录", http=401)

    data = request.get_json(silent=True) or {}
    conn = get_conn()
    try:
        if data.get("points"):
            points = data["points"]
            for p in points:
                p.setdefault("name", "位置")
                p.setdefault("city_name", "")
        else:
            rows = conn.execute(
                "SELECT * FROM favorites WHERE user_id=? AND item_type='sight'",
                (uid,),
            ).fetchall()
            if not rows:
                # 退化为城市收藏
                rows = conn.execute(
                    "SELECT * FROM favorites WHERE user_id=? AND item_type='city'",
                    (uid,),
                ).fetchall()
            points = []
            for r in rows:
                points.append({
                    "name": r["sight_name"] or r["city_name"],
                    "city_name": r["city_name"],
                    "lat": r["lat"],
                    "lng": r["lng"],
                })
            if not points:
                return fail("请先收藏景点或城市，再进行智能规划", http=400)
    finally:
        conn.close()

    start_index = 0
    try:
        start_index = int(data.get("start_index", 0))
    except Exception:
        start_index = 0

    result = planner.plan_route(points, start_index)
    return ok({
        "sequence": result["sequence"],
        "total_distance": result["total_distance"],
        "count": len(points),
    }, "规划完成")
