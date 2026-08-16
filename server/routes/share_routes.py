# -*- coding: utf-8 -*-
"""
路线 / 批注 分享与导入路由
"""
import datetime
import json
import secrets

from flask import Blueprint, jsonify, request

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth import verify_token, get_token_from_request
from database import query_one, query_all, execute, execute_many
from utils import to_mysql_datetime

share_bp = Blueprint("share", __name__, url_prefix="/api/shares")


def _json_default(o):
    """支持 datetime / date / bytes -> 字符串"""
    if isinstance(o, (datetime.datetime, datetime.date)):
        return o.isoformat(sep=" ")
    if isinstance(o, bytes):
        return o.decode("utf-8", errors="replace")
    raise TypeError("Object of type %s is not JSON serializable" % type(o).__name__)


def _dumps(obj):
    return json.dumps(obj, ensure_ascii=False, default=_json_default)


def _to_str(v):
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat(sep=" ")
    return str(v)


def ok(data=None, msg="success"):
    return jsonify({"code": 0, "msg": msg, "data": data})


def fail(msg, code=1, http=200):
    return jsonify({"code": code, "msg": msg, "data": None}), http


def _uid():
    payload = verify_token(get_token_from_request(request))
    return payload["uid"] if payload else None


def _trip_owned(trip_id, uid):
    return bool(query_one(
        "SELECT id FROM trips WHERE id = ? AND user_id = ?", (trip_id, uid)))


def _as_int(v, default=0):
    """安全转 int：非法值回退默认，避免 int('abc') 抛异常导致 500"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _is_expired(expire_at):
    """分享是否已过期。expire_at 为空表示永不过期；
    无法解析的格式按"未过期"处理（fail-open，避免误封正常分享）"""
    if not expire_at:
        return False
    if isinstance(expire_at, datetime.datetime):
        return expire_at < datetime.datetime.now()
    if isinstance(expire_at, datetime.date):
        return expire_at < datetime.date.today()
    s = str(expire_at).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.datetime.strptime(s, fmt) < datetime.datetime.now()
        except ValueError:
            continue
    return False


def _gen_token():
    """生成不易猜的分享短码（URL 安全）"""
    # 10 字符 ~ 59 bits 熵，避免太短被暴力扫
    return "s" + secrets.token_urlsafe(8)


def _snapshot_for_trip(trip_id):
    """读取行程下全部国家的 (annotations, routes) 做成快照"""
    anns = query_all(
        "SELECT country_id, country_name, client_id, title, content, lat, lng, color, created_at "
        "FROM annotations WHERE trip_id = ? ORDER BY id", (trip_id,))
    routes = query_all(
        "SELECT country_id, country_name, client_id, name, points, color, distance, created_at "
        "FROM routes WHERE trip_id = ? ORDER BY id", (trip_id,))
    for r in routes:
        try:
            r["points"] = json.loads(r["points"] or "[]")
        except Exception:
            r["points"] = []
        r["created_at"] = _to_str(r.get("created_at"))
    for a in anns:
        a["created_at"] = _to_str(a.get("created_at"))
    return {"annotations": anns, "routes": routes}


def _snapshot_for_country(trip_id, country_id):
    anns = query_all(
        "SELECT country_id, country_name, client_id, title, content, lat, lng, color, created_at "
        "FROM annotations WHERE trip_id = ? AND country_id = ? ORDER BY id",
        (trip_id, str(country_id)))
    routes = query_all(
        "SELECT country_id, country_name, client_id, name, points, color, distance, created_at "
        "FROM routes WHERE trip_id = ? AND country_id = ? ORDER BY id",
        (trip_id, str(country_id)))
    for r in routes:
        try:
            r["points"] = json.loads(r["points"] or "[]")
        except Exception:
            r["points"] = []
        r["created_at"] = _to_str(r.get("created_at"))
    for a in anns:
        a["created_at"] = _to_str(a.get("created_at"))
    return {"annotations": anns, "routes": routes}


# ---------------------------------------------------------------------------
# 分享方接口
# ---------------------------------------------------------------------------
@share_bp.route("", methods=["GET"])
def my_shares():
    uid = _uid()
    if not uid:
        return fail("未登录", 401, 401)
    page = request.args.get("page", 1, type=int) or 1
    page = max(page, 1)
    size = request.args.get("size", 20, type=int) or 20
    size = min(max(size, 1), 50)
    total = query_one(
        "SELECT COUNT(*) AS c FROM share_tokens WHERE owner_id = ?", (uid,))["c"]
    rows = query_all(
        "SELECT id, token, trip_id, scope, country_id, country_name, title, created_at, snapshot "
        "FROM share_tokens WHERE owner_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
        (uid, size, (page - 1) * size))
    for r in rows:
        snap_raw = r.pop("snapshot") or "{}"
        try:
            snap = json.loads(snap_raw)
        except Exception:
            snap = {}
        r["annotation_count"] = len(snap.get("annotations", []) or [])
        r["route_count"] = len(snap.get("routes", []) or [])
        # created_at 可能是 datetime，JSONify 之前转字符串
        if r.get("created_at") and hasattr(r["created_at"], "isoformat"):
            r["created_at"] = r["created_at"].isoformat(sep=" ")
    return ok({"total": total, "page": page, "size": size, "items": rows})


@share_bp.route("", methods=["POST"])
def create_share():
    """生成分享码
    body: {
      trip_id: int,                 必填
      scope: 'country' | 'trip',    默认 country
      country_id?: str,             scope='country' 必填
      country_name?: str,
      title?: str,
    }
    """
    uid = _uid()
    if not uid:
        return fail("未登录", 401, 401)
    body = request.get_json(silent=True) or {}
    trip_id = _as_int(body.get("trip_id"))
    if trip_id <= 0:
        return fail("缺少 trip_id")
    if not _trip_owned(trip_id, uid):
        return fail("行程不存在或不是你的", 404, 404)
    scope = "country" if str(body.get("scope", "country")).lower() != "trip" else "trip"
    country_id = str(body.get("country_id") or "")
    country_name = (body.get("country_name") or "")[:80]
    title = (body.get("title") or "")[:120]
    if scope == "country" and not country_id:
        return fail("按国家分享时缺少 country_id")

    # 生成快照
    if scope == "country":
        snapshot = _snapshot_for_country(trip_id, country_id)
    else:
        snapshot = _snapshot_for_trip(trip_id)
    if not snapshot["annotations"] and not snapshot["routes"]:
        return fail("没有任何批注或路线可分享")

    trip_row = query_one("SELECT name FROM trips WHERE id = ?", (trip_id,))
    if not title:
        if scope == "country" and country_name:
            title = "{} · 地图路线分享".format(country_name)
        elif trip_row and trip_row["name"]:
            title = "{} · 地图路线分享".format(trip_row["name"][:50])
        else:
            title = "地图路线分享"

    token = _gen_token()
    snap_json = _dumps(snapshot)
    sid = execute(
        "INSERT INTO share_tokens (token, owner_id, trip_id, scope, country_id, country_name, title, snapshot) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (token, uid, trip_id, scope, country_id, country_name, title, snap_json))
    return ok({
        "id": sid,
        "token": token,
        "title": title,
        "annotation_count": len(snapshot["annotations"]),
        "route_count": len(snapshot["routes"]),
    }, "分享链接已生成")


@share_bp.route("/<int:share_id>", methods=["DELETE"])
def revoke_share(share_id):
    uid = _uid()
    if not uid:
        return fail("未登录", 401, 401)
    row = query_one("SELECT * FROM share_tokens WHERE id = ?", (share_id,))
    if not row or row["owner_id"] != uid:
        return fail("分享不存在", 404, 404)
    execute("DELETE FROM share_tokens WHERE id = ?", (share_id,))
    return ok(msg="已撤销分享")


# ---------------------------------------------------------------------------
# 接收方接口（无需登录可看内容，导入需要登录）
# ---------------------------------------------------------------------------
@share_bp.route("/<token>", methods=["GET"])
def inspect_share(token):
    """公开读取分享详情 + 预览数据（用于前端展示导入前预览）"""
    token = (token or "").strip()
    row = query_one(
        "SELECT t.*, u.nickname AS owner_nickname, u.username AS owner_username "
        "FROM share_tokens t LEFT JOIN users u ON u.id = t.owner_id WHERE t.token = ?",
        (token,))
    if not row:
        return fail("分享不存在或已被撤销", 404, 404)
    if _is_expired(row.get("expire_at")):
        return fail("分享链接已过期", 404, 404)
    try:
        snapshot = json.loads(row["snapshot"] or "{}")
    except Exception:
        snapshot = {"annotations": [], "routes": []}
    owner = (row.get("owner_nickname") or row.get("owner_username") or "旅行者")
    return ok({
        "token": token,
        "title": row["title"],
        "scope": row["scope"],
        "country_id": row["country_id"],
        "country_name": row["country_name"],
        "owner": owner,
        "created_at": _to_str(row.get("created_at")),
        "annotation_count": len(snapshot.get("annotations", []) or []),
        "route_count": len(snapshot.get("routes", []) or []),
        "preview": snapshot,  # 打开分享链接时可先看到缩略图/列表
    })


@share_bp.route("/<token>/import", methods=["POST"])
def import_share(token):
    """把分享快照写入自己的行程
    body: { trip_id: int, mode?: 'merge'|'replace' }    默认 merge
    """
    uid = _uid()
    if not uid:
        return fail("未登录或登录已过期", 401, 401)
    token = (token or "").strip()
    row = query_one("SELECT * FROM share_tokens WHERE token = ?", (token,))
    if not row:
        return fail("分享不存在或已被撤销", 404, 404)
    try:
        snapshot = json.loads(row["snapshot"] or "{}")
    except Exception:
        return fail("分享数据损坏")
    body = request.get_json(silent=True) or {}
    trip_id = _as_int(body.get("trip_id"))
    if trip_id <= 0:
        return fail("请选择目标行程")
    if not _trip_owned(trip_id, uid):
        return fail("行程不存在或不是你的", 404, 404)
    mode = "replace" if str(body.get("mode") or "merge").lower() == "replace" else "merge"

    anns = snapshot.get("annotations") or []
    routes = snapshot.get("routes") or []

    # 按 country_id 分组，复用 /sync 语义一致地写入
    from collections import defaultdict
    groups = defaultdict(lambda: {"annotations": [], "routes": []})
    for a in anns:
        key = str(a.get("country_id") or "") or "__generic__"
        groups[key]["annotations"].append(a)
    for r in routes:
        key = str(r.get("country_id") or "") or "__generic__"
        groups[key]["routes"].append(r)

    added_ann = 0
    added_rt = 0
    affected_countries = []
    for country_id, g in groups.items():
        if country_id == "__generic__":
            country_id = row.get("country_id") or ""
        if not country_id:
            # 缺国家时，尝试从路线/批注点推算（取中点经纬度反查国家 id，不做；跳过）
            continue
        ann_rows = []
        for a in g["annotations"]:
            cid_map = a.get("client_id") or secrets.token_urlsafe(6)
            ann_rows.append((
                trip_id, country_id, (row.get("country_name") or a.get("country_name") or "")[:80],
                cid_map,
                (a.get("title") or "未命名标注")[:100],
                (a.get("content") or "")[:1000],
                float(a.get("lat") or 0), float(a.get("lng") or 0),
                (a.get("color") or "#ff5252"),
                to_mysql_datetime(a.get("created_at")),
            ))
        rt_rows = []
        for r in g["routes"]:
            pts = r.get("points") or []
            if not isinstance(pts, list) or len(pts) < 2:
                continue
            cid_map = r.get("client_id") or secrets.token_urlsafe(6)
            # distance 可选，没有则不计算
            try:
                dist = float(r.get("distance") or 0)
            except Exception:
                dist = 0
            rt_rows.append((
                trip_id, country_id, (row.get("country_name") or r.get("country_name") or "")[:80],
                cid_map,
                (r.get("name") or "未命名路线")[:50],
                _dumps(pts),
                (r.get("color") or "#4a9eff"),
                dist,
                to_mysql_datetime(r.get("created_at")),
            ))

        affected_countries.append(country_id)
        if mode == "replace":
            execute(
                "DELETE FROM annotations WHERE trip_id = ? AND country_id = ?", (trip_id, country_id))
            execute(
                "DELETE FROM routes WHERE trip_id = ? AND country_id = ?", (trip_id, country_id))

        if ann_rows:
            execute_many(
                "INSERT INTO annotations "
                "(trip_id,country_id,country_name,client_id,title,content,lat,lng,color,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)", ann_rows)
            added_ann += len(ann_rows)
        if rt_rows:
            execute_many(
                "INSERT INTO routes "
                "(trip_id,country_id,country_name,client_id,name,points,color,distance,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)", rt_rows)
            added_rt += len(rt_rows)

    if added_ann == 0 and added_rt == 0:
        return fail("分享内容已为空或全部被跳过")

    return ok({
        "mode": mode,
        "trip_id": trip_id,
        "added_annotations": added_ann,
        "added_routes": added_rt,
        "countries": list(dict.fromkeys(affected_countries)),
    }, "导入成功（已新增 {} 个批注，{} 条路线）".format(added_ann, added_rt))
