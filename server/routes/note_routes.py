# -*- coding: utf-8 -*-
"""游记路由：公开浏览 + 个人管理"""
from flask import Blueprint, jsonify, request

from auth import verify_token, get_token_from_request
from database import query_one, query_all, execute

# 复用 share_routes 里的工具函数
from routes import share_routes

note_bp = Blueprint("note", __name__, url_prefix="/api/notes")


def ok(data=None, msg="success"):
    return jsonify({"code": 0, "msg": msg, "data": data})


def fail(msg, code=1, http=200):
    return jsonify({"code": code, "msg": msg, "data": None}), http


def _uid(request):
    payload = verify_token(get_token_from_request(request))
    return payload["uid"] if payload else None


def _as_int(v, default=0):
    """安全转 int：非法值回退默认，避免 int('abc') 抛异常导致 500"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _trip_owned(trip_id, uid):
    """校验行程归属，避免把游记关联到别人的行程上（越权）"""
    return query_one("SELECT id FROM trips WHERE id = ? AND user_id = ?", (trip_id, uid))


def _ensure_share_token(note_id, uid, trip_id):
    """为关联了行程的游记生成（或重建）share_token + share_tokens 记录。
    如果 trip_id 没有批注/路线则清除 share_token。"""
    # 先清掉旧 token 关联的 share_tokens 记录
    old = query_one("SELECT share_token FROM notes WHERE id = ?", (note_id,))
    if old and old.get("share_token"):
        execute("DELETE FROM share_tokens WHERE token = ? AND owner_id = ?",
                (old["share_token"], uid))

    if not trip_id:
        execute("UPDATE notes SET share_token = NULL WHERE id = ?", (note_id,))
        return None

    # 确认行程归属
    trip = query_one("SELECT id, name FROM trips WHERE id = ? AND user_id = ?", (trip_id, uid))
    if not trip:
        execute("UPDATE notes SET share_token = NULL WHERE id = ?", (note_id,))
        return None

    # 生成快照
    snapshot = share_routes._snapshot_for_trip(trip_id)
    if not snapshot["annotations"] and not snapshot["routes"]:
        # 行程里没有批注/路线，不生成 share_token
        execute("UPDATE notes SET share_token = NULL WHERE id = ?", (note_id,))
        return None

    title = "{} · 游记关联".format((trip.get("name") or "我的行程")[:50])
    token = share_routes._gen_token()
    snap_json = share_routes._dumps(snapshot)
    execute(
        "INSERT INTO share_tokens (token, owner_id, trip_id, scope, country_id, country_name, title, snapshot) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (token, uid, trip_id, "trip", "", "", title, snap_json))
    execute("UPDATE notes SET share_token = ? WHERE id = ?", (token, note_id))
    return token


@note_bp.route("", methods=["GET"])
def list_notes():
    """公开游记列表（分页），登录用户可附带自己的私有游记"""
    uid = _uid(request)
    page = request.args.get("page", 1, type=int) or 1
    page = max(page, 1)
    size = request.args.get("size", 10, type=int) or 10
    size = min(max(size, 1), 50)
    keyword = (request.args.get("q") or "").strip()[:50]

    where = "is_public = 1"
    params = []
    if keyword:
        where += " AND (title LIKE ? OR content LIKE ?)"
        params += [f"%{keyword}%", f"%{keyword}%"]
    total = query_one(f"SELECT COUNT(*) AS c FROM notes WHERE {where}", params)["c"]

    rows = query_all(
        f"SELECT n.*, u.username, u.nickname, t.name AS trip_name FROM notes n "
        f"LEFT JOIN users u ON n.user_id = u.id "
        f"LEFT JOIN trips t ON n.trip_id = t.id "
        f"WHERE {where} ORDER BY n.created_at DESC LIMIT ? OFFSET ?",
        params + [size, (page - 1) * size],
    )
    return ok({"total": total, "page": page, "size": size, "items": rows})


@note_bp.route("/mine", methods=["GET"])
def my_notes():
    """我的游记（含私有）"""
    uid = _uid(request)
    if not uid:
        return fail("未登录或登录已过期", 401, 401)
    rows = query_all(
        "SELECT n.*, t.name AS trip_name FROM notes n LEFT JOIN trips t ON n.trip_id = t.id "
        "WHERE n.user_id = ? ORDER BY n.created_at DESC", (uid,))
    return ok(rows)


@note_bp.route("", methods=["POST"])
def create_note():
    """发布游记"""
    uid = _uid(request)
    if not uid:
        return fail("未登录或登录已过期", 401, 401)
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    content = (body.get("content") or "").strip()
    if not title:
        return fail("游记标题不能为空")
    if not content:
        return fail("游记内容不能为空")
    trip_id = _as_int(body.get("trip_id")) or None
    if trip_id and not _trip_owned(trip_id, uid):
        return fail("行程不存在或不是你的", 404, 404)
    note_id = execute(
        "INSERT INTO notes (user_id, trip_id, title, content, is_public) VALUES (?,?,?,?,?)",
        (uid, trip_id, title[:100], content,
         1 if body.get("is_public", True) else 0),
    )
    # 自动生成 share_token（如果关联了有批注/路线的行程）
    generated = None
    if trip_id:
        generated = _ensure_share_token(note_id, uid, trip_id)
    return ok({"id": note_id, "share_token": generated}, "游记发布成功")


@note_bp.route("/<int:note_id>", methods=["GET"])
def get_note(note_id):
    """获取单篇游记（公开或本人私有）"""
    row = query_one(
        "SELECT n.*, t.name AS trip_name, u.username, u.nickname FROM notes n "
        "LEFT JOIN trips t ON n.trip_id = t.id "
        "LEFT JOIN users u ON n.user_id = u.id "
        "WHERE n.id = ?", (note_id,))
    if not row or (not row["is_public"] and row["user_id"] != (_uid(request) or 0)):
        return fail("游记不存在或未公开", 404, 404)
    execute("UPDATE notes SET view_count = view_count + 1 WHERE id = ?", (note_id,))
    row["view_count"] += 1
    return ok(row)


@note_bp.route("/<int:note_id>", methods=["PUT"])
def update_note(note_id):
    """更新自己的游记"""
    uid = _uid(request)
    if not uid:
        return fail("未登录或登录已过期", 401, 401)
    note = query_one("SELECT * FROM notes WHERE id = ?", (note_id,))
    if not note or note["user_id"] != uid:
        return fail("无权操作该游记", 403, 403)
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    content = (body.get("content") or "").strip()
    if not title:
        return fail("游记标题不能为空")
    new_trip_id = _as_int(body.get("trip_id")) or None
    if new_trip_id and not _trip_owned(new_trip_id, uid):
        return fail("行程不存在或不是你的", 404, 404)
    execute(
        "UPDATE notes SET title=?, content=?, is_public=?, trip_id=? WHERE id=?",
        (title[:100], content, 1 if body.get("is_public", True) else 0,
         new_trip_id, note_id),
    )
    # 如果 trip_id 变了（或旧 token 可能过期），重新生成 share_token
    old_trip_id = note.get("trip_id")
    old_token = note.get("share_token")
    need_regen = (str(new_trip_id or "") != str(old_trip_id or "")) or not old_token
    if need_regen:
        _ensure_share_token(note_id, uid, new_trip_id)
    return ok(msg="游记已更新")


@note_bp.route("/<int:note_id>", methods=["DELETE"])
def delete_note(note_id):
    """删除自己的游记"""
    uid = _uid(request)
    if not uid:
        return fail("未登录或登录已过期", 401, 401)
    note = query_one("SELECT * FROM notes WHERE id = ?", (note_id,))
    if not note or note["user_id"] != uid:
        return fail("无权操作该游记", 403, 403)
    # 清掉关联的 share_tokens 记录
    if note.get("share_token"):
        execute("DELETE FROM share_tokens WHERE token = ? AND owner_id = ?",
                (note["share_token"], uid))
    execute("DELETE FROM notes WHERE id = ?", (note_id,))
    return ok(msg="游记已删除")
