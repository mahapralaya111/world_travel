# -*- coding: utf-8 -*-
"""
智能推荐算法 - 改良型 Item-CF 混合推荐
========================================
算法设计（对比普通 Item-CF 的改进点）：

1. 余弦相似度：基于「用户-物品收藏矩阵」计算景点之间的相似度
2. 时间衰减：用户越早收藏的记录权重越低（近期兴趣更重要）
3. 热门惩罚：被大量用户收藏的热门景点做分数衰减，避免"头部景点霸屏"
4. 标签融合：协同过滤分数 70% + 标签匹配分数 30% 加权合并
5. 多样性控制：最终返回 TOP8，限制同标签景点最多 3 个
6. 冷启动：无收藏记录 → 完全使用标签匹配；无标签偏好 → 返回评分最高 8 个
7. 过滤掉用户已收藏的景点

算法局限（论文"系统不足与展望"可用）：
- 协同过滤在小数据量下相似度矩阵稀疏，推荐质量受限
- 未考虑用户收藏强度（如评分/点击时长），仅用收藏行为二值化
- 后续可引入矩阵分解 ALS 进一步优化
"""
import json
import math
import time
from datetime import datetime

from database import get_conn

# -------------------- 常量配置 --------------------
CF_WEIGHT = 0.7        # 协同过滤分数权重
TAG_WEIGHT = 0.3       # 标签匹配分数权重
TOP_N = 8              # 最终推荐数量
MAX_SAME_TAG = 3       # 同标签最多出现次数（多样性控制）
TIME_DECAY_HALF_DAYS = 90.0   # 时间衰减半衰期（天）：90 天前收藏的权重降为 1/2
HOT_PENALTY_K = 0.2    # 热门惩罚系数：被收藏数达到该值时开始显著衰减


def _now_ts():
    """当前时间戳"""
    return time.time()


def _parse_ts(s):
    """将数据库时间（字符串或 MySQL datetime 对象）解析为时间戳"""
    try:
        if isinstance(s, datetime):
            return s.timestamp()
        return datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S").timestamp()
    except Exception:
        return _now_ts()


def _get_all_favorites():
    """读取全站收藏记录（用于构建用户-物品矩阵）"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT user_id, item_type, item_key, sight_name, tags, created_at "
            "FROM favorites"
        ).fetchall()
        return rows
    finally:
        conn.close()


def _load_sights_pool():
    """
    加载推荐物品池：城市 + 景点
    返回 dict: item_key -> 景点信息
    """
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, name, name_en, country_code, country_zh, country_en, lat, lng, sights, "
            "population, is_capital "
            "FROM cities"
        ).fetchall()
    finally:
        conn.close()

    pool = {}
    for c in rows:
        cid = c["id"]
        city_key = "city_{}".format(cid)
        pool[city_key] = {
            "item_key": city_key,
            "item_type": "city",
            "name": c["name"],
            "name_en": c["name_en"] or "",
            "city_id": cid,
            "city_name": c["name"],
            "country_code": c["country_code"],
            "country_zh": c["country_zh"],
            "country_en": c["country_en"],
            "lat": c["lat"],
            "lng": c["lng"],
            "population": c["population"] or 0,
            "is_capital": c["is_capital"] or 0,
            "tags": [c["country_zh"], c["country_en"], "城市"],
        }
        sights = []
        try:
            sights = json.loads(c["sights"] or "[]")
        except Exception:
            sights = []
        for s in sights:
            s_key = "sight_{}_{}".format(cid, s)
            pool[s_key] = {
                "item_key": s_key,
                "item_type": "sight",
                "name": s,
                "name_en": "",
                "city_id": cid,
                "city_name": c["name"],
                "country_code": c["country_code"],
                "country_zh": c["country_zh"],
                "country_en": c["country_en"],
                "lat": c["lat"],
                "lng": c["lng"],
                "tags": [c["country_zh"], c["country_en"], _sight_tag(s)],
            }
    return pool


def _sight_tag(name):
    """
    根据景点名称提取简单分类标签（中文关键词启发式）
    用于标签匹配与多样性控制
    """
    rules = [
        (["寺", "庙", "殿", "教堂", "神社"], "宗教文化"),
        (["博物馆", "博物院", "美术馆", "展览", "画廊"], "博物馆"),
        (["公园", "植物园", "动物园", "乐园", "乐园"], "公园乐园"),
        (["广场", "街", "市场", "商圈"], "街市广场"),
        (["山", "峰", "岭", "岩"], "山川自然"),
        (["湖", "海", "河", "湾", "泉", "岛"], "湖泊海滨"),
        (["塔", "桥", "观景台", "塔楼"], "地标建筑"),
        (["宫", "城堡", "府", "故居", "遗址", "古城", "城墙"], "古迹遗址"),
        (["大学", "学", "学院"], "文化教育"),
        (["城", "区", "里", "町", "村"], "城镇街区"),
    ]
    for kws, tag in rules:
        for kw in kws:
            if kw in name:
                return tag
    return "热门景点"


def _item_popularity(favs):
    """统计每个物品被收藏的次数（用于热门惩罚）"""
    pop = {}
    for f in favs:
        key = f["item_key"]
        pop[key] = pop.get(key, 0) + 1
    return pop


def _hot_penalty(count):
    """热门惩罚系数：收藏数越多，惩罚越大，返回 0~1 的衰减因子"""
    # 当 count 从 0 增长时，衰减因子从 1 平滑下降
    return 1.0 / (1.0 + HOT_PENALTY_K * max(0, count - 1))


def _time_decay(created_at, now_ts):
    """时间衰减：越早收藏权重越低（半衰期模型）"""
    ts = _parse_ts(created_at)
    days = max(0.0, (now_ts - ts) / 86400.0)
    return math.pow(0.5, days / TIME_DECAY_HALF_DAYS)


def _cosine_sim(a_vec, b_vec):
    """余弦相似度（稀疏向量）"""
    dot = 0.0
    for k in a_vec:
        if k in b_vec:
            dot += a_vec[k] * b_vec[k]
    na = math.sqrt(sum(v * v for v in a_vec.values()))
    nb = math.sqrt(sum(v * v for v in b_vec.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _build_user_item_matrix(favs, now_ts):
    """
    构建用户-物品矩阵（带时间衰减权重的二值化收藏）
    返回: { user_id: { item_key: weight } }
    """
    matrix = {}
    for f in favs:
        uid = f["user_id"]
        key = f["item_key"]
        w = _time_decay(f["created_at"], now_ts)  # 时间衰减
        matrix.setdefault(uid, {})[key] = w
    return matrix


def _build_item_vectors(matrix, target_uid):
    """
    为候选物品构建"被哪些用户以多大权重收藏"的向量
    只保留与目标用户有过共同收藏行为的用户，缩小计算范围
    """
    target_items = set(matrix.get(target_uid, {}).keys())
    if not target_items:
        return {}

    # 找到与目标用户有共同收藏的用户
    related_users = set()
    for uid, items in matrix.items():
        if uid == target_uid:
            continue
        if target_items & set(items.keys()):
            related_users.add(uid)

    vectors = {}
    for uid in related_users:
        for key, w in matrix[uid].items():
            vectors.setdefault(key, {})[uid] = w
    return vectors


def _cf_score(item_key, vectors, target_vec, pop):
    """
    计算某个候选物品的协同过滤得分
    = Σ(相似度(u_items, item) × 时间衰减权重) / |u_items|，再乘热门惩罚
    """
    item_vec = vectors.get(item_key, {})
    if not item_vec:
        return 0.0
    total = 0.0
    for t_key, t_w in target_vec.items():
        if t_key == item_key:
            continue
        sim = _cosine_sim(vectors.get(t_key, {}), item_vec)
        total += sim * t_w
    if not target_vec:
        return 0.0
    base = total / len(target_vec)
    # 热门惩罚
    return base * _hot_penalty(pop.get(item_key, 0))


def _tag_match_score(item, target_tags):
    """标签匹配分数：目标用户收藏物品标签集合 与 候选物品标签集合 的 Jaccard 相似度"""
    if not target_tags:
        return 0.0
    item_tags = set(item.get("tags", []))
    inter = item_tags & target_tags
    union = item_tags | target_tags
    if not union:
        return 0.0
    return len(inter) / len(union)


def _cold_start_by_tags(pool, all_favs, uid):
    """冷启动：无收藏记录时，用其他用户收藏最热的景点标签推断用户偏好"""
    conn = get_conn()
    try:
        user = conn.execute("SELECT id FROM users WHERE id=?", (uid,)).fetchone()
        if not user:
            return []
    finally:
        conn.close()
    # 收集热门标签
    tag_count = {}
    for f in all_favs:
        try:
            tags = json.loads(f["tags"] or "[]")
        except Exception:
            tags = []
        for t in tags:
            tag_count[t] = tag_count.get(t, 0) + 1
    # 取热门标签作为虚拟偏好
    hot_tags = {t for t, _ in sorted(tag_count.items(), key=lambda x: -x[1])[:6]}
    scored = []
    for key, item in pool.items():
        item_tags = set(item.get("tags", []))
        if hot_tags & item_tags:
            s = _tag_match_score(item, hot_tags)
            item = dict(item)
            item["score"] = round(s, 4)
            scored.append(item)
    scored.sort(key=lambda x: -x["score"])
    return _diversity_filter(scored)[:TOP_N]


def _cold_start_by_popularity(pool, pop):
    """冷启动兜底：无任何偏好时，按热门度优先，其次知名大城市（人口多、首都）"""
    def rank_key(kv):
        key, it = kv
        pop_score = pop.get(key, 0)
        is_city = 1 if it.get("item_type") == "city" else 0
        # 统一元组结构：热门收藏数 -> 城市优先 -> 首都 -> 人口 -> 名称
        return (
            -pop_score,
            -is_city,
            -(it.get("is_capital") or 0) if is_city else 0,
            -(it.get("population") or 0) if is_city else 0,
            it.get("name", ""),
        )

    ranked = sorted(pool.items(), key=rank_key)
    return [it for _, it in ranked[:TOP_N]]


def _diversity_filter(scored_items, max_same=MAX_SAME_TAG):
    """多样性控制：同标签最多出现 max_same 个，保证推荐结果多样化"""
    result = []
    tag_count = {}
    for item in scored_items:
        tags = item.get("tags", [])
        # 取第一个标签作为主分类（保证主标签多样性）
        main_tag = tags[0] if tags else "other"
        if tag_count.get(main_tag, 0) >= max_same:
            continue
        tag_count[main_tag] = tag_count.get(main_tag, 0) + 1
        result.append(item)
        if len(result) >= TOP_N:
            break
    return result


def recommend(uid, item_type=None, top_n=TOP_N):
    """
    对外主入口：为用户 uid 推荐景点（默认）
    item_type: 'city' 只推荐城市 | 'sight' 只推荐景点 | None 都推荐
    返回: 推荐物品列表 [{item_key, item_type, name, city_name, country_zh, lat, lng, tags, score}]
    """
    now_ts = _now_ts()
    all_favs = _get_all_favorites()
    pool = _load_sights_pool()
    pop = _item_popularity(all_favs)

    # 目标用户已收藏的 item_key
    my_keys = {f["item_key"] for f in all_favs if f["user_id"] == uid}
    my_favs = [f for f in all_favs if f["user_id"] == uid]

    # ---------- 冷启动处理 ----------
    if not my_favs:
        if item_type == "city":
            # 城市冷启动：直接推荐知名大城市（首都/人口优先）
            cold = _cold_start_by_popularity(pool, pop)
        else:
            # 景点冷启动：用全站热门标签推断偏好，失败回退热门
            cold = _cold_start_by_tags(pool, all_favs, uid)
            if not cold:
                cold = _cold_start_by_popularity(pool, pop)
        return _to_result(cold, my_keys, item_type, top_n)

    matrix = _build_user_item_matrix(all_favs, now_ts)
    target_vec = matrix.get(uid, {})
    vectors = _build_item_vectors(matrix, uid)
    target_tags = set()
    for f in my_favs:
        try:
            target_tags |= set(json.loads(f["tags"] or "[]"))
        except Exception:
            pass

    # ---------- 计算每个候选物品的混合得分 ----------
    scored = []
    for key, item in pool.items():
        if key in my_keys:
            continue  # 过滤已收藏
        if item_type and item["item_type"] != item_type:
            continue
        cf = _cf_score(key, vectors, target_vec, pop)
        tag = _tag_match_score(item, target_tags)
        score = CF_WEIGHT * cf + TAG_WEIGHT * tag
        if score > 0:
            item = dict(item)
            item["score"] = round(score, 4)
            scored.append(item)

    scored.sort(key=lambda x: -x["score"])

    # ---------- 多样性控制 ----------
    if item_type == "city":
        result = scored[:top_n]  # 城市少，不做强多样性约束
    else:
        result = _diversity_filter(scored, top_n if top_n > 1 else 1)

    return _to_result(result, my_keys, item_type, top_n)


def _to_result(items, my_keys, item_type, top_n):
    """转换为 API 输出结构"""
    out = []
    for it in items:
        out.append({
            "item_key": it["item_key"],
            "item_type": it.get("item_type", "sight"),
            "name": it.get("name", ""),
            "name_en": it.get("name_en", ""),
            "city_name": it.get("city_name", ""),
            "city_id": it.get("city_id", 0),
            "country_code": it.get("country_code", ""),
            "country_zh": it.get("country_zh", ""),
            "lat": it.get("lat", 0),
            "lng": it.get("lng", 0),
            "population": it.get("population", 0),
            "is_capital": it.get("is_capital", 0),
            "tags": it.get("tags", []),
            "score": it.get("score", 0),
            "favorited": it["item_key"] in my_keys,
        })
    return out[:top_n]
