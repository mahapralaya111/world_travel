# -*- coding: utf-8 -*-
"""行程路由：行程 CRUD + 批注/路线管理 + 整国同步"""
import json
import sys
from datetime import datetime

from flask import Blueprint, jsonify, request

from auth import verify_token, get_token_from_request
from database import query_one, query_all, execute, execute_many, transaction
from utils import route_distance, to_mysql_datetime

trip_bp = Blueprint("trip", __name__, url_prefix="/api/trips")


def _uid(request):
    payload = verify_token(get_token_from_request(request))
    return payload["uid"] if payload else None


def ok(data=None, msg="success"):
    return jsonify({"code": 0, "msg": msg, "data": data})


def fail(msg, code=1, http=200):
    return jsonify({"code": code, "msg": msg, "data": None}), http


def _trip_owned(trip_id, uid):
    return query_one("SELECT * FROM trips WHERE id = ? AND user_id = ?", (trip_id, uid))


def _to_float(v, default=0.0):
    """安全转 float：空值/非法值回退默认值，避免 float('abc') 抛异常导致 500"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if f == f else default  # 过滤 NaN


def _norm_points(raw):
    """清洗路线点：只保留经纬度合法的点，供距离计算与入库共用"""
    pts = []
    for p in (raw if isinstance(raw, list) else []):
        if not isinstance(p, dict):
            continue
        lat = _to_float(p.get("lat"), None)
        lng = _to_float(p.get("lng"), None)
        if lat is None or lng is None:
            continue
        q = dict(p)
        q["lat"], q["lng"] = lat, lng
        pts.append(q)
    return pts


@trip_bp.route("", methods=["GET"])
def list_trips():
    """获取当前用户的所有行程（含统计 + 自动状态）"""
    uid = _uid(request)
    if not uid:
        return fail("未登录或登录已过期", 401, 401)
    trips = query_all(
        "SELECT * FROM trips WHERE user_id = ? ORDER BY updated_at DESC, id DESC", (uid,))

    # 一次性查完所有统计，避免 N+1
    trip_ids = [t["id"] for t in trips]
    ann_stats = {}
    rt_stats = {}
    country_stats = {}
    if trip_ids:
        placeholders = ",".join(["?"] * len(trip_ids))
        ann_rows = query_all(
            f"SELECT trip_id, COUNT(*) AS c FROM annotations "
            f"WHERE trip_id IN ({placeholders}) GROUP BY trip_id", trip_ids)
        for r in ann_rows: ann_stats[r["trip_id"]] = r["c"]
        rt_rows = query_all(
            f"SELECT trip_id, COUNT(*) AS c FROM routes "
            f"WHERE trip_id IN ({placeholders}) GROUP BY trip_id", trip_ids)
        for r in rt_rows: rt_stats[r["trip_id"]] = r["c"]
        cnt_rows = query_all(
            f"SELECT trip_id, COUNT(DISTINCT country_id) AS c FROM ("
            f"SELECT trip_id, country_id FROM annotations WHERE trip_id IN ({placeholders}) "
            f"UNION ALL "
            f"SELECT trip_id, country_id FROM routes WHERE trip_id IN ({placeholders})"
            f") x GROUP BY trip_id", trip_ids + trip_ids)
        for r in cnt_rows: country_stats[r["trip_id"]] = r["c"]

    now_date = datetime.now().date()
    for t in trips:
        tid = t["id"]
        ann_count = ann_stats.get(tid, 0)
        rt_count = rt_stats.get(tid, 0)
        country_count = country_stats.get(tid, 0)
        t["annotation_count"] = ann_count
        t["route_count"] = rt_count
        t["country_count"] = country_count

        # --- 自动计算状态（computed_status）：用户手动设置优先，否则按日期/数据推断 ---
        # manual_status 就是 trips.status 列的值，允许用户在编辑弹窗覆盖
        manual = (t.get("status") or "").strip()
        if manual in ("ongoing", "finished"):
            t["computed_status"] = manual
            t["status_auto"] = False  # 标记：用户手动设置过
        else:
            t["status_auto"] = True  # 标记：自动推断
            sd = (t.get("start_date") or "").strip()
            ed = (t.get("end_date") or "").strip()
            try:
                start_d = datetime.strptime(sd, "%Y-%m-%d").date() if sd else None
            except Exception:
                start_d = None
            try:
                end_d = datetime.strptime(ed, "%Y-%m-%d").date() if ed else None
            except Exception:
                end_d = None
            if end_d and now_date > end_d:
                t["computed_status"] = "finished"
            elif start_d and now_date >= start_d:
                t["computed_status"] = "ongoing"
            elif ann_count or rt_count:
                # 有批注/路线但日期还没到 → 规划中
                t["computed_status"] = "planning"
            else:
                t["computed_status"] = "planning"
    return ok(trips)


@trip_bp.route("", methods=["POST"])
def create_trip():
    """创建新行程"""
    uid = _uid(request)
    if not uid:
        return fail("未登录或登录已过期", 401, 401)
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return fail("行程名称不能为空")
    trip_id = execute(
        "INSERT INTO trips (user_id, name, description, start_date, end_date, cover_color, status) "
        "VALUES (?,?,?,?,?,?,?)",
        (uid, name[:50], (body.get("description") or "").strip()[:200],
         (body.get("start_date") or ""), (body.get("end_date") or ""),
         body.get("cover_color") or "#4a9eff",
         body["status"] if "status" in body else ""),  # 空字符串=自动判断
    )
    return ok({"id": trip_id}, "行程创建成功")


@trip_bp.route("/<int:trip_id>", methods=["GET"])
def get_trip(trip_id):
    """获取单个行程详情（含批注与路线）"""
    uid = _uid(request)
    if not uid:
        return fail("未登录或登录已过期", 401, 401)
    trip = _trip_owned(trip_id, uid)
    if not trip:
        return fail("行程不存在", 404, 404)
    annotations = query_all(
        "SELECT * FROM annotations WHERE trip_id = ? ORDER BY id", (trip_id,))
    routes = query_all("SELECT * FROM routes WHERE trip_id = ? ORDER BY id", (trip_id,))
    return ok({"trip": trip, "annotations": annotations, "routes": routes})


@trip_bp.route("/<int:trip_id>", methods=["PUT"])
def update_trip(trip_id):
    """更新行程信息"""
    uid = _uid(request)
    if not uid:
        return fail("未登录或登录已过期", 401, 401)
    if not _trip_owned(trip_id, uid):
        return fail("行程不存在", 404, 404)
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return fail("行程名称不能为空")
    execute(
        "UPDATE trips SET name=?, description=?, start_date=?, end_date=?, cover_color=?, status=?, "
        "updated_at=? WHERE id=?",
        (name[:50], (body.get("description") or "").strip()[:200],
         body.get("start_date") or "", body.get("end_date") or "",
         body.get("cover_color") or "#4a9eff",
         body["status"] if "status" in body else (trip.get("status") or ""),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S"), trip_id),
    )
    return ok(msg="行程已更新")


@trip_bp.route("/<int:trip_id>", methods=["DELETE"])
def delete_trip(trip_id):
    """删除行程（批注与路线级联删除）"""
    uid = _uid(request)
    if not uid:
        return fail("未登录或登录已过期", 401, 401)
    if not _trip_owned(trip_id, uid):
        return fail("行程不存在", 404, 404)
    execute("DELETE FROM trips WHERE id = ?", (trip_id,))
    return ok(msg="行程已删除")


@trip_bp.route("/<int:trip_id>/routes", methods=["POST"])
def add_route(trip_id):
    """为行程添加一条路线"""
    uid = _uid(request)
    if not uid:
        return fail("未登录或登录已过期", 401, 401)
    if not _trip_owned(trip_id, uid):
        return fail("行程不存在", 404, 404)
    body = request.get_json(silent=True) or {}
    points = _norm_points(body.get("points") or [])
    if len(points) < 2:
        return fail("路线至少需要2个点")
    dist = route_distance(points)  # 只算一次，避免入库与返回不一致
    route_id = execute(
        "INSERT INTO routes (trip_id, country_id, country_name, name, points, color, distance) "
        "VALUES (?,?,?,?,?,?,?)",
        (trip_id, str(body.get("country_id") or ""), (body.get("country_name") or ""),
         (body.get("name") or "未命名路线")[:50],
         json.dumps(points, ensure_ascii=False),
         body.get("color") or "#4a9eff", dist),
    )
    return ok({"id": route_id, "distance": dist}, "路线已保存")


@trip_bp.route("/<int:trip_id>/routes/<int:route_id>", methods=["DELETE"])
def delete_route(trip_id, route_id):
    """删除行程中的一条路线"""
    uid = _uid(request)
    if not uid:
        return fail("未登录或登录已过期", 401, 401)
    if not _trip_owned(trip_id, uid):
        return fail("行程不存在", 404, 404)
    execute("DELETE FROM routes WHERE id = ? AND trip_id = ?", (route_id, trip_id))
    return ok(msg="路线已删除")


@trip_bp.route("/<int:trip_id>/annotations", methods=["POST"])
def add_annotation(trip_id):
    """为行程添加一条批注"""
    uid = _uid(request)
    if not uid:
        return fail("未登录或登录已过期", 401, 401)
    if not _trip_owned(trip_id, uid):
        return fail("行程不存在", 404, 404)
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return fail("批注标题不能为空")
    ann_id = execute(
        "INSERT INTO annotations (trip_id, country_id, country_name, title, content, lat, lng, color) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (trip_id, str(body.get("country_id") or ""), (body.get("country_name") or ""),
         title[:100], (body.get("content") or "").strip()[:1000],
         _to_float(body.get("lat")), _to_float(body.get("lng")),
         body.get("color") or "#ff5252"),
    )
    return ok({"id": ann_id}, "批注已保存")


@trip_bp.route("/<int:trip_id>/annotations/<int:ann_id>", methods=["PUT"])
def update_annotation(trip_id, ann_id):
    """更新批注内容"""
    uid = _uid(request)
    if not uid:
        return fail("未登录或登录已过期", 401, 401)
    if not _trip_owned(trip_id, uid):
        return fail("行程不存在", 404, 404)
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return fail("批注标题不能为空")
    execute(
        "UPDATE annotations SET title=?, content=? WHERE id=? AND trip_id=?",
        (title[:100], (body.get("content") or "").strip()[:1000], ann_id, trip_id),
    )
    return ok(msg="批注已更新")


@trip_bp.route("/<int:trip_id>/annotations/<int:ann_id>", methods=["DELETE"])
def delete_annotation(trip_id, ann_id):
    """删除批注"""
    uid = _uid(request)
    if not uid:
        return fail("未登录或登录已过期", 401, 401)
    if not _trip_owned(trip_id, uid):
        return fail("行程不存在", 404, 404)
    execute("DELETE FROM annotations WHERE id = ? AND trip_id = ?", (ann_id, trip_id))
    return ok(msg="批注已删除")


@trip_bp.route("/<int:trip_id>/sync", methods=["POST"])
def sync_country(trip_id):
    """
    整国同步：以"行程+国家"为粒度全量替换批注与路线
    请求体: { country_id, country_name, annotations:[{client_id,title,content,lat,lng,color,created_at}], routes:[{client_id,name,points,color,created_at}] }
    策略: 先删除该国家旧数据，再批量插入（保持前端 client_id 稳定）
    """
    uid = _uid(request)
    if not uid:
        return fail("未登录或登录已过期", 401, 401)
    if not _trip_owned(trip_id, uid):
        return fail("行程不存在", 404, 404)
    body = request.get_json(silent=True) or {}
    country_id = str(body.get("country_id") or "")
    country_name = (body.get("country_name") or "")[:80]
    if not country_id:
        return fail("缺少 country_id")

    annotations = body.get("annotations") or []
    routes = body.get("routes") or []
    if len(annotations) > 2000 or len(routes) > 2000:
        return fail("单次同步数据量过大（批注/路线各上限 2000 条）", http=413)

    # 1) 预清洗：非法经纬度/非法点直接过滤，避免 float('abc') 抛异常中断整次同步
    ann_rows = []
    for a in annotations:
        if not isinstance(a, dict):
            continue
        ann_rows.append((
            trip_id, country_id, country_name,
            str(a.get("client_id") or a.get("id") or ""),
            (a.get("title") or "").strip()[:100],
            (a.get("content") or "").strip()[:1000],
            _to_float(a.get("lat")), _to_float(a.get("lng")),
            a.get("color") or "#ff5252",
            to_mysql_datetime(a.get("created_at")),
        ))

    route_rows = []
    for r in routes:
        if not isinstance(r, dict):
            continue
        pts = _norm_points(r.get("points") or [])
        route_rows.append((
            trip_id, country_id, country_name,
            str(r.get("client_id") or r.get("id") or ""),
            (r.get("name") or "未命名路线")[:50],
            json.dumps(pts, ensure_ascii=False),
            r.get("color") or "#4a9eff",
            route_distance(pts),
            to_mysql_datetime(r.get("created_at")),
        ))

    # 2) 删除旧数据 + 批量插入放在同一事务：任一步失败整体回滚，
    #    避免"旧数据已删、新数据没插进去"导致用户地图数据丢失
    with transaction() as conn:
        conn.execute("DELETE FROM annotations WHERE trip_id = ? AND country_id = ?",
                     (trip_id, country_id))
        conn.execute("DELETE FROM routes WHERE trip_id = ? AND country_id = ?",
                     (trip_id, country_id))
        if ann_rows:
            conn.executemany(
                "INSERT INTO annotations (trip_id,country_id,country_name,client_id,title,content,lat,lng,color,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)", ann_rows)
        if route_rows:
            conn.executemany(
                "INSERT INTO routes (trip_id,country_id,country_name,client_id,name,points,color,distance,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)", route_rows)

    _log_sync(trip_id, body, len(ann_rows), len(route_rows))

    return ok({
        "annotation_count": len(ann_rows),
        "route_count": len(route_rows),
    }, "同步成功")


def _log_sync(trip_id, payload, ann_n, rt_n):
    """同步请求审计日志：出现数据被覆盖/清空时可在 server.err.log 追溯"""
    try:
        print("[sync] trip=%s country=%s annotations=%d routes=%d country_name=%s" % (
            trip_id, payload.get("country_id"), ann_n, rt_n,
            payload.get("country_name") or ""), file=sys.stderr, flush=True)
    except Exception:
        pass
