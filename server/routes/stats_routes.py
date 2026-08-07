# -*- coding: utf-8 -*-
"""统计路由：个人统计（抗刷指标 + 成就 + 完成度）+ 全局统计"""
import json

from flask import Blueprint, jsonify, request

from auth import verify_token, get_token_from_request
from database import query_one, query_all

stats_bp = Blueprint("stats", __name__, url_prefix="/api/stats")


def ok(data=None, msg="success"):
    return jsonify({"code": 0, "msg": msg, "data": data})


def fail(msg, code=1, http=200):
    return jsonify({"code": code, "msg": msg, "data": None}), http


def _uid(request):
    payload = verify_token(get_token_from_request(request))
    return payload["uid"] if payload else None


# ========== 成就徽章定义（一次性解锁，不可重复刷） ==========
# 每个徽章的 check 是纯函数 fn(ctx) -> bool，从 _compute_achievement_progress 的 ctx 取指标
ACHIEVEMENTS = [
    # --- 规划类 ---
    {"key": "first_trip",       "name": "启程",       "icon": "🗺️", "desc": "创建第一个行程",           "check_fn": lambda c: c["trip_count"] >= 1},
    {"key": "first_plan",       "name": "精心规划",   "icon": "📋", "desc": "生成并保存第一份行程计划", "check_fn": lambda c: c["plan_count"] >= 1},
    {"key": "first_annotation", "name": "留下标记",   "icon": "📌", "desc": "添加第一条批注",           "check_fn": lambda c: c["annotation_count"] >= 1},
    {"key": "first_route",      "name": "画出路线",   "icon": "🛤️", "desc": "画出第一条有效路线",       "check_fn": lambda c: c["effective_route_count"] >= 1},
    # --- 地理类（布尔阈值，不可刷） ---
    {"key": "explorer_3",       "name": "初探世界",   "icon": "🌍", "desc": "覆盖 3 个国家",            "check_fn": lambda c: c["country_count"] >= 3},
    {"key": "explorer_10",      "name": "环球旅人",   "icon": "🌏", "desc": "覆盖 10 个国家",           "check_fn": lambda c: c["country_count"] >= 10},
    {"key": "explorer_20",      "name": "环游达人",   "icon": "🌐", "desc": "覆盖 20 个国家",           "check_fn": lambda c: c["country_count"] >= 20},
    # --- 里程类（distance 由后端几何计算，无法伪造） ---
    {"key": "km_100",           "name": "短途试手",   "icon": "🚗", "desc": "累计路线里程 100 km",      "check_fn": lambda c: c["effective_distance"] >= 100},
    {"key": "km_1000",          "name": "千里之行",   "icon": "🚙", "desc": "累计路线里程 1000 km",     "check_fn": lambda c: c["effective_distance"] >= 1000},
    {"key": "km_5000",          "name": "驰骋万里",   "icon": "✈️", "desc": "累计路线里程 5000 km",     "check_fn": lambda c: c["effective_distance"] >= 5000},
    # --- 内容创作类（有 content 的批注才算） ---
    {"key": "writer_5",         "name": "边走边记",   "icon": "✍️", "desc": "留下 5 条有效批注",       "check_fn": lambda c: c["effective_annotation_count"] >= 5},
    {"key": "writer_20",        "name": "旅行作家",   "icon": "📖", "desc": "留下 20 条有效批注",      "check_fn": lambda c: c["effective_annotation_count"] >= 20},
    {"key": "first_note",       "name": "发表游记",   "icon": "📝", "desc": "发布第一篇游记",           "check_fn": lambda c: c["note_count"] >= 1},
    {"key": "first_public",     "name": "分享世界",   "icon": "🌐", "desc": "游记设为公开可见",         "check_fn": lambda c: c["public_note_count"] >= 1},
    # --- 社交类 ---
    {"key": "first_fav",        "name": "收藏达人",   "icon": "⭐", "desc": "收藏第一个景点",           "check_fn": lambda c: c["favorite_count"] >= 1},
    {"key": "first_share",      "name": "分享路线",   "icon": "🔗", "desc": "分享行程并生成分享链接",   "check_fn": lambda c: c["share_count"] >= 1},
]


def _compute_achievement_progress(uid):
    """计算成就解锁状态（内部函数，供 /mine 和 /achievements 共用）"""
    trip_count = query_one("SELECT COUNT(*) AS c FROM trips WHERE user_id = ?", (uid,))["c"]
    plan_count = query_one(
        "SELECT COUNT(*) AS c FROM trip_plans WHERE trip_id IN (SELECT id FROM trips WHERE user_id = ?)",
        (uid,))["c"]
    ann_count = query_one(
        "SELECT COUNT(*) AS c FROM annotations WHERE trip_id IN (SELECT id FROM trips WHERE user_id = ?)",
        (uid,))["c"]
    eff_ann_count = query_one(
        "SELECT COUNT(*) AS c FROM annotations "
        "WHERE trip_id IN (SELECT id FROM trips WHERE user_id = ?) "
        "AND content IS NOT NULL AND TRIM(content) != ''", (uid,))["c"]
    rt_count = query_one(
        "SELECT COUNT(*) AS c FROM routes WHERE trip_id IN (SELECT id FROM trips WHERE user_id = ?)",
        (uid,))["c"]
    eff_rt_count = query_one(
        "SELECT COUNT(*) AS c FROM routes "
        "WHERE trip_id IN (SELECT id FROM trips WHERE user_id = ?) AND distance > 0",
        (uid,))["c"]
    eff_distance = float(query_one(
        "SELECT COALESCE(SUM(distance), 0) AS d FROM routes "
        "WHERE trip_id IN (SELECT id FROM trips WHERE user_id = ?) AND distance > 0",
        (uid,))["d"])
    country_count = query_one(
        "SELECT COUNT(DISTINCT country_id) AS c FROM ("
        "SELECT country_id FROM annotations WHERE trip_id IN (SELECT id FROM trips WHERE user_id = ?) "
        "UNION ALL SELECT country_id FROM routes WHERE trip_id IN (SELECT id FROM trips WHERE user_id = ?)"
        ") t", (uid, uid))["c"]
    note_count = query_one("SELECT COUNT(*) AS c FROM notes WHERE user_id = ?", (uid,))["c"]
    public_note_count = query_one(
        "SELECT COUNT(*) AS c FROM notes WHERE user_id = ? AND is_public = 1", (uid,))["c"]
    fav_count = 0
    try:
        fav_count = query_one("SELECT COUNT(*) AS c FROM favorites WHERE user_id = ?", (uid,))["c"]
    except Exception:
        pass
    share_count = query_one(
        "SELECT COUNT(*) AS c FROM share_tokens WHERE owner_id = ?", (uid,))["c"]

    ctx = {
        "trip_count": trip_count, "plan_count": plan_count,
        "annotation_count": ann_count, "effective_annotation_count": eff_ann_count,
        "route_count": rt_count, "effective_route_count": eff_rt_count,
        "effective_distance": eff_distance, "country_count": country_count,
        "note_count": note_count, "public_note_count": public_note_count,
        "favorite_count": fav_count, "share_count": share_count,
    }
    # 安全：用预定义的 check_fn 判断，完全不 eval 字符串
    unlocked = []
    for ach in ACHIEVEMENTS:
        fn = ach["check_fn"]
        try:
            ok_flag = bool(fn(ctx))
        except Exception:
            ok_flag = False
        unlocked.append({
            "key": ach["key"], "name": ach["name"], "icon": ach["icon"],
            "desc": ach["desc"], "unlocked": ok_flag,
        })
    return ctx, unlocked


# =========================================================================
#  /api/stats/mine  —  个人统计（抗刷版本）
# =========================================================================
@stats_bp.route("/mine", methods=["GET"])
def my_stats():
    """
    个人统计（抗刷版）：
      - annotation_count / route_count: 原始 count（保留用于兼容）
      - effective_annotation_count: 只统计 content 非空的批注
      - effective_route_count: 只统计 distance > 0 的路线
      - total_distance: 所有路线里程
      - effective_distance: 只计 distance > 0 的（避免空路线 0 值干扰）
      - effective_trip_count: 至少有 1 条有效批注或有效路线的行程
      - plan_count: 已保存 planner 规划的行程数
      - country_progress: 覆盖全球 244 国的百分比
      - unlocked_achievements: 已解锁成就列表
      - top_trips: 最近 5 个行程（含 computed_status 风格）
      - country_stats: 覆盖国家 Top（含 country_name 方便前端展示）
    """
    uid = _uid(request)
    if not uid:
        return fail("未登录或登录已过期", 401, 401)

    ctx, achievements = _compute_achievement_progress(uid)
    unlocked = [a for a in achievements if a["unlocked"]]

    # 覆盖国家 Top
    country_stats = query_all(
        "SELECT country_id, country_name, COUNT(*) AS cnt FROM ("
        "SELECT country_id, country_name FROM annotations "
        "WHERE trip_id IN (SELECT id FROM trips WHERE user_id = ?) "
        "AND (content IS NOT NULL AND TRIM(content) != '') "
        "UNION ALL "
        "SELECT country_id, country_name FROM routes "
        "WHERE trip_id IN (SELECT id FROM trips WHERE user_id = ?) AND distance > 0"
        ") t GROUP BY country_id, country_name ORDER BY cnt DESC LIMIT 10",
        (uid, uid))

    # 有效行程（至少 1 条有效批注/路线/规划 的行程）
    effective_trip_count = query_one(
        "SELECT COUNT(DISTINCT trip_id) AS c FROM ("
        "SELECT trip_id FROM annotations "
        "WHERE trip_id IN (SELECT id FROM trips WHERE user_id = ?) "
        "AND (content IS NOT NULL AND TRIM(content) != '') "
        "UNION "
        "SELECT trip_id FROM routes "
        "WHERE trip_id IN (SELECT id FROM trips WHERE user_id = ?) AND distance > 0"
        ") t", (uid, uid))["c"]

    # 全球 244 国覆盖进度
    global_country_total = query_one("SELECT COUNT(DISTINCT country_code) AS c FROM cities")["c"]
    progress_pct = round(ctx["country_count"] / max(global_country_total, 1) * 100, 2)

    # 最近行程（带 plan_data 标记，前端可以显示完成度雷达）
    top_trips = query_all(
        "SELECT t.id, t.name, t.cover_color, t.status, t.start_date, t.end_date, "
        "  (SELECT COUNT(*) FROM annotations a WHERE a.trip_id = t.id AND a.content IS NOT NULL AND TRIM(a.content) != '') AS ann_cnt, "
        "  (SELECT COUNT(*) FROM routes r WHERE r.trip_id = t.id AND r.distance > 0) AS rt_cnt, "
        "  (SELECT COUNT(*) FROM trip_plans tp WHERE tp.trip_id = t.id) AS has_plan "
        "FROM trips t WHERE t.user_id = ? ORDER BY t.updated_at DESC LIMIT 5", (uid,))

    return ok({
        # --- 基础计数 ---
        "trip_count": ctx["trip_count"],
        "annotation_count": ctx["annotation_count"],        # 原始 count
        "route_count": ctx["route_count"],                  # 原始 count
        "country_count": ctx["country_count"],
        "note_count": ctx["note_count"],
        # --- 抗刷有效指标 ---
        "effective_trip_count": effective_trip_count,
        "effective_annotation_count": ctx["effective_annotation_count"],
        "effective_route_count": ctx["effective_route_count"],
        "total_distance": round(ctx["effective_distance"], 1),
        "effective_distance": round(ctx["effective_distance"], 1),
        "plan_count": ctx["plan_count"],
        # --- 国家覆盖 ---
        "global_country_total": global_country_total,
        "country_progress_pct": progress_pct,
        # --- 成就 ---
        "achievement_total": len(achievements),
        "achievement_unlocked_count": len(unlocked),
        "unlocked_achievements": unlocked,
        # --- 其他 ---
        "top_trips": top_trips,
        "country_stats": country_stats,
    })


# =========================================================================
#  /api/stats/achievements  —  成就专用接口（含全部徽章：解锁 + 未解锁）
# =========================================================================
@stats_bp.route("/achievements", methods=["GET"])
def achievements():
    uid = _uid(request)
    if not uid:
        return fail("未登录或登录已过期", 401, 401)
    ctx, all_badges = _compute_achievement_progress(uid)
    return ok({
        "badges": all_badges,
        "unlocked_count": sum(1 for b in all_badges if b["unlocked"]),
        "total": len(all_badges),
    })


# =========================================================================
#  /api/stats/trip_completion  —  每个行程的规划完成度雷达
# =========================================================================
@stats_bp.route("/trip-completion", methods=["GET"])
def trip_completion():
    """
    每个行程的完成度 %：
      有 trip_plans.plan_data → 提取 days/activities/sights/budget 作为"规划目标"
      对比 annotations + routes 的实际落地情况 → 算出完成度
      没有 plan_data → 标记为 "未规划"，仅展示现有批注/路线数量
    """
    uid = _uid(request)
    if not uid:
        return fail("未登录或登录已过期", 401, 401)

    trips = query_all(
        "SELECT id, name FROM trips WHERE user_id = ? ORDER BY updated_at DESC", (uid,))
    results = []
    for t in trips:
        tid = t["id"]
        eff_anns = query_one(
            "SELECT COUNT(*) AS c FROM annotations "
            "WHERE trip_id = ? AND content IS NOT NULL AND TRIM(content) != ''", (tid,))["c"]
        eff_rts = query_one(
            "SELECT COUNT(*) AS c FROM routes WHERE trip_id = ? AND distance > 0", (tid,))["c"]
        total_dist = float(query_one(
            "SELECT COALESCE(SUM(distance),0) AS d FROM routes WHERE trip_id = ? AND distance > 0",
            (tid,))["d"])

        # 有没有 plan_data
        plan_row = query_one("SELECT plan_data FROM trip_plans WHERE trip_id = ?", (tid,))
        if plan_row:
            try:
                plan = json.loads(plan_row["plan_data"])
                days_list = plan.get("days", [])
                total_sights = sum(
                    1 for d in days_list
                    for a in d.get("activities", [])
                    if a.get("type") == "sight")
                total_days = len(days_list)

                # 完成度算法：
                #   有效批注数  对标  plan 里的 sights 数量
                #   有效路线数  对标  plan 里有几个不同城市（或默认 1）
                #   有规划 = 50% 起步（说明用了 planner）
                #   批注达标 +25%，路线达标 +25%
                score = 50  # 基础分：已规划
                sight_ratio = min(eff_anns / max(total_sights, 1), 1.0)
                route_ratio = min(eff_rts / max(1, 1), 1.0)  # 至少 1 条就行
                score += int(sight_ratio * 25)
                score += int(route_ratio * 25)

                results.append({
                    "trip_id": tid, "trip_name": t["name"],
                    "has_plan": True,
                    "plan_total_days": total_days,
                    "plan_total_sights": total_sights,
                    "actual_annotations": eff_anns,
                    "actual_routes": eff_rts,
                    "actual_distance": round(total_dist, 1),
                    "completion_pct": min(score, 100),
                })
            except Exception:
                results.append({
                    "trip_id": tid, "trip_name": t["name"],
                    "has_plan": True, "completion_pct": None,
                    "actual_annotations": eff_anns, "actual_routes": eff_rts,
                    "actual_distance": round(total_dist, 1),
                    "error": "计划数据解析失败",
                })
        else:
            # 没 plan：只展示现状，不给完成度分
            results.append({
                "trip_id": tid, "trip_name": t["name"],
                "has_plan": False, "completion_pct": None,
                "actual_annotations": eff_anns, "actual_routes": eff_rts,
                "actual_distance": round(total_dist, 1),
            })

    return ok(results)


# =========================================================================
#  /api/stats/global  —  全局统计（保留 + 加热门国家 Top）
# =========================================================================
@stats_bp.route("/global", methods=["GET"])
def global_stats():
    """全局统计：用户数 / 公开游记 / 城市数 / 国家数 / 批注 / 路线"""
    user_count = query_one("SELECT COUNT(*) AS c FROM users")["c"]
    note_count = query_one("SELECT COUNT(*) AS c FROM notes WHERE is_public = 1")["c"]
    city_count = query_one("SELECT COUNT(*) AS c FROM cities")["c"]
    country_count = query_one("SELECT COUNT(DISTINCT country_code) AS c FROM cities")["c"]
    # 全局也用有效统计（有 content 的批注 + distance > 0 的路线）
    ann_count = query_one(
        "SELECT COUNT(*) AS c FROM annotations WHERE content IS NOT NULL AND TRIM(content) != ''")["c"]
    route_count = query_one(
        "SELECT COUNT(*) AS c FROM routes WHERE distance > 0")["c"]

    recent_notes = query_all(
        "SELECT n.id, n.title, n.created_at, n.view_count, u.nickname FROM notes n "
        "LEFT JOIN users u ON n.user_id = u.id WHERE n.is_public = 1 "
        "ORDER BY n.created_at DESC LIMIT 5")

    top_countries = query_all(
        "SELECT country_id, country_name, COUNT(*) AS cnt FROM ("
        "SELECT country_id, country_name FROM annotations "
        "WHERE content IS NOT NULL AND TRIM(content) != '' "
        "UNION ALL "
        "SELECT country_id, country_name FROM routes WHERE distance > 0) t "
        "GROUP BY country_id, country_name ORDER BY cnt DESC LIMIT 10")

    return ok({
        "user_count": user_count,
        "note_count": note_count,
        "city_count": city_count,
        "country_count": country_count,
        "annotation_count": ann_count,
        "route_count": route_count,
        "recent_notes": recent_notes,
        "top_countries": top_countries,
    })
