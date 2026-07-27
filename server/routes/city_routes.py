# -*- coding: utf-8 -*-
"""城市路由：多语言搜索 + 城市列表"""
import json
import os
import time

from flask import Blueprint, jsonify, request

from config import Config
from database import query_all, query_one
from utils import search_score

city_bp = Blueprint("city", __name__, url_prefix="/api/cities")

# ---------------------------------------------------------------------------
# 区域别名：一级行政区/海岛/地区俗称 -> 区域内城市关键词（中/繁/英任一命中即返回该城市）
# 解决 "北海道/冲绳/巴厘岛" 等区域名搜不到的问题（城市表里只有具体城市，没有区域本身）
# ---------------------------------------------------------------------------
_REGION_GROUPS = [
    (["北海道", "hokkaido"],
     ["札幌", "函馆", "函館", "旭川", "小樽", "钏路", "釧路", "带广", "帯広", "室兰", "室蘭", "苫小牧", "北见", "北見",
      "sapporo", "otaru", "hakodate", "asahikawa", "kushiro", "tomakomai"]),
    (["冲绳", "沖繩", "沖縄", "okinawa"],
     ["那霸", "那覇", "石垣", "名护", "名護", "宜野湾", "浦添", "丝满", "糸満",
      "naha", "ishigaki", "okinawa"]),
    (["关东", "関東", "kanto"],
     ["东京", "東京", "横滨", "橫濱", "川崎", "千叶", "千葉", "镰仓", "鎌倉", "埼玉",
      "tokyo", "yokohama", "kawasaki", "kamakura"]),
    (["关西", "関西", "kansai"],
     ["大阪", "京都", "奈良", "神户", "神戶", "和歌山", "姬路", "姫路",
      "osaka", "kyoto", "nara", "kobe", "himeji"]),
    (["济州", "濟州", "jeju"],
     ["济州", "濟州", "西归浦", "西歸浦", "jeju", "seogwipo"]),
    (["巴厘岛", "巴厘", "峇里島", "峇里", "巴里岛", "bali"],
     ["登巴萨", "登巴薩", "丹帕沙", "丹帕薩", "乌布", "烏布", "库塔", "庫塔",
      "denpasar", "ubud", "kuta"]),
    (["普吉岛", "普吉", "布吉島", "布吉", "phuket"],
     ["普吉", "布吉", "芭东", "芭東", "phuket", "patong"]),
    (["苏梅岛", "蘇梅島", "samui"],
     ["苏梅", "蘇梅", "samui"]),
    (["塞班岛", "塞班", "saipan"],
     ["塞班", "saipan"]),
    (["夏威夷", "hawaii"],
     ["檀香山", "火奴鲁鲁", "火奴魯魯", "希洛", "茂宜", "honolulu", "hilo", "kahului"]),
    (["加州", "加利福尼亚", "加利福尼亞", "california"],
     ["洛杉矶", "洛杉磯", "旧金山", "舊金山", "圣地亚哥", "聖地亞哥", "圣何塞", "聖何塞", "萨克拉门托", "长滩", "長灘",
      "los angeles", "san francisco", "san diego", "san jose", "oakland", "sacramento", "long beach"]),
    (["西西里", "sicily", "sicilia"],
     ["巴勒莫", "卡塔尼亚", "卡塔尼亞", "墨西拿", "messina", "palermo", "catania"]),
    (["长滩岛", "長灘島", "boracay"],
     ["卡利博", "kalibo", "caticlan", "boracay"]),
    (["富士山", "mt fuji", "fuji"],
     ["富士", "富士宮", "富士吉田", "御殿场", "御殿場", "富士河口湖", "fuji", "fujinomiya", "gotemba"]),
    # 著名街区/商圈 -> 所属城市（城市表中无此名且景点数据未覆盖时的兜底）
    (["下北泽", "下北澤", "shimokitazawa", "银座", "銀座", "ginza", "原宿", "harajuku",
      "表参道", "表參道", "omotesando", "六本木", "roppongi", "台场", "台場", "odaiba"],
     ["东京", "東京", "tokyo"]),
    (["心斋桥", "心齋橋", "shinsaibashi", "道顿堀", "道頓堀", "dotonbori", "难波", "難波", "namba"],
     ["大阪", "osaka"]),
]


def _region_keywords(ql: str):
    """查询词命中区域别名时，返回该区域的城市关键词列表；未命中返回 None"""
    if not ql:
        return None
    for keys, kws in _REGION_GROUPS:
        for k in keys:
            if k in ql:
                return kws
    return None


def _is_latin(s: str) -> bool:
    return all(ord(c) < 128 for c in s)


def _region_hit(kws, name_l: str, alias_l: str) -> bool:
    """
    区域关键词命中判断（严格匹配，避免短关键词子串误伤）：
    - 中文关键词：城市中文名以前缀开头（如 库塔 不会误伤 库库塔）
    - 英文关键词：整词精确匹配；>=5 字母才允许整词前缀（如 kuta 不会误伤 kutaisi）
    - 含空格的英文关键词（如 los angeles）：直接子串匹配 alias 字段
    """
    for k in kws:
        if _is_latin(k):
            if " " in k:
                if k in alias_l:
                    return True
            else:
                for w in alias_l.replace("-", " ").replace(".", " ").split():
                    if w == k or (len(k) >= 5 and w.startswith(k)):
                        return True
        else:
            if name_l.startswith(k):
                return True
    return False

# ---------------------------------------------------------------------------
# 城市内存缓存（解决 3.4 万城市全表查询 + 逐行打分的性能瓶颈）
# 启动时/过期后一次性加载并预计算拼音，之后搜索走内存，速度提升数十倍
# ---------------------------------------------------------------------------
_CACHE = {"ts": 0.0, "items": None}
CACHE_TTL = 120  # 秒


def _load_city_cache():
    """加载/刷新城市缓存，返回 [(row, name_lower, alias_lower, alias_raw, name_py)]"""
    now = time.time()
    cache = _CACHE
    if cache["items"] is not None and now - cache["ts"] < CACHE_TTL:
        return cache["items"]

    rows = query_all(
        "SELECT id, country_code, country_zh, country_en, name, name_en, name_local, lat, lng, sights, "
        "is_capital, population "
        "FROM cities", ())

    # 检测拼音支持（缓存中预计算所有城市拼音，避免搜索时重复转换）
    has_py = False
    try:
        from pypinyin import lazy_pinyin  # noqa: F401
        has_py = True
    except Exception:
        pass

    items = []
    if has_py:
        from pypinyin import lazy_pinyin
        for r in rows:
            name = r["name"] or ""
            alias_raw = " | ".join(filter(None, [r["name_en"], r["name_local"], r["country_zh"], r["country_en"]]))
            items.append((r, name.lower(), alias_raw.lower(), alias_raw,
                          "".join(lazy_pinyin(name)).lower(), _sights_lower(r)))
    else:
        for r in rows:
            name = r["name"] or ""
            alias_raw = " | ".join(filter(None, [r["name_en"], r["name_local"], r["country_zh"], r["country_en"]]))
            items.append((r, name.lower(), alias_raw.lower(), alias_raw, "", _sights_lower(r)))

    cache["ts"] = now
    cache["items"] = items
    return items


def _sights_lower(row) -> str:
    """把城市景点 JSON 数组展平为小写字符串（供景点名搜索，如 富士山 -> 富士市）"""
    raw = row.get("sights")
    if not raw:
        return ""
    try:
        sl = json.loads(raw)
        if isinstance(sl, list):
            return " | ".join(str(s) for s in sl).lower()
        return str(sl).lower()
    except Exception:
        return str(raw).lower()


def ok(data=None, msg="success"):
    return jsonify({"code": 0, "msg": msg, "data": data})


def _parse_sights(row):
    row = dict(row)
    try:
        row["sights"] = json.loads(row.get("sights") or "[]")
    except Exception:
        row["sights"] = []
    return row


@city_bp.route("", methods=["GET"])
def list_cities():
    """获取全部城市（可只返回基础字段）"""
    # ?limit=abc 时 type=int 返回 None，需兜底为默认值，避免 int(None) 崩成 500
    limit = request.args.get("limit", 500, type=int) or 500
    limit = min(max(limit, 1), 5000)
    rows = query_all("SELECT * FROM cities ORDER BY country_code, id LIMIT ?", (limit,))
    return ok([_parse_sights(r) for r in rows])


@city_bp.route("/search", methods=["GET"])
def search_cities():
    """
    多语言模糊搜索：兼容中文 / 英文 / 拼音 / 当地文字
    两级筛选：先内存粗筛（子串/前缀/拼音），再精打分排序
    额外支持：
    - 区域别名展开：北海道/冲绳/巴厘岛等区域名 -> 命中区域内城市（得分 45，按人口排序）
    - 景点名匹配：富士山等景点名 -> 命中所在城市（得分 35）
    返回按匹配度排序的城市列表，支持 include_sights=1 附带景点
    """
    q = (request.args.get("q") or "").strip()[:100]  # 过长查询词直接截断，避免无意义全表扫描
    include_sights = request.args.get("include_sights", "0") == "1"
    limit = request.args.get("limit", 20, type=int) or 20
    limit = min(max(limit, 1), 100)
    if not q:
        return ok([])

    ql = q.lower()
    # 用户输入转拼音（用于拼音粗筛）
    py_q = ""
    try:
        from pypinyin import lazy_pinyin
        py_q = "".join(lazy_pinyin(q)).lower()
    except Exception:
        pass

    items = _load_city_cache()
    region_kws = _region_keywords(ql)
    candidates = []
    for r, name_l, alias_l, alias_raw, name_py, sights_l in items:
        # 粗筛：中文/英文/别名子串 或 拼音匹配（含拼音 vs 英文名，如搜"上海"命中 Shanghai）
        if (ql in name_l or ql in alias_l
                or (py_q and len(py_q) >= 2 and (py_q in name_py or py_q in alias_l))):
            score = search_score(r["name"], alias_raw, q, name_py)
            if score <= 0:
                continue
        elif region_kws and _region_hit(region_kws, name_l, alias_l):
            # 区域别名命中（如搜"北海道"返回札幌/函馆等），固定分，靠人口排序展示
            score = 45
        elif sights_l and ql in sights_l:
            # 景点名命中（如搜"富士山"返回拥有该景点的城市）
            score = 35
        else:
            continue
        candidates.append((score, r))
    # 同分时首都优先、人口多的优先（如"伦敦"应优先英国伦敦而非加拿大同名城市）
    candidates.sort(key=lambda x: (-x[0], -x[1].get("is_capital", 0), -(x[1].get("population") or 0)))

    result = []
    for score, r in candidates[:limit]:
        item = _parse_sights(r)
        item["score"] = score
        if not include_sights:
            item.pop("sights", None)
        result.append(item)
    return ok(result)


@city_bp.route("/region-aliases", methods=["GET"])
def region_aliases():
    """区域别名表（供前端本地搜索框做同样的区域展开，保持两处搜索行为一致）"""
    return ok([{"keys": list(keys), "cities": list(kws)} for keys, kws in _REGION_GROUPS])


@city_bp.route("/countries", methods=["GET"])
def list_countries():
    """获取城市数据中涉及的国家列表"""
    rows = query_all(
        "SELECT DISTINCT country_code, country_zh, country_en FROM cities ORDER BY country_zh")
    return ok(rows)


@city_bp.route("/country-codes", methods=["GET"])
def countries_list():
    """ISO 数字码 -> 两位国家码映射表，供前端将 GeoJSON 国家 id 映射为数据库 country_code"""
    cc_file = os.path.join(Config.DATA_DIR, "country_codes.json")
    try:
        with open(cc_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            data = []
    except Exception as e:
        # 文件缺失/损坏时不要让前端拿到 None，返回空列表并打印原因便于排查
        print("[city] 国家码表读取失败: %s (%s)" % (cc_file, e))
        data = []
    return ok(data)


@city_bp.route("/<path:country_code>", methods=["GET"])
def cities_by_country(country_code):
    """获取某国家下的所有城市（首都优先，按人口降序，便于前端优先展示大城市）"""
    code = (country_code or "").strip().upper()[:8]
    if not code:
        return ok([])
    rows = query_all(
        "SELECT * FROM cities WHERE country_code = ? "
        "ORDER BY is_capital DESC, population DESC, id", (code,))
    return ok([_parse_sights(r) for r in rows])
