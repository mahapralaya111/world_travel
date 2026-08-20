# -*- coding: utf-8 -*-
"""兴趣点详情路由：境内用高德 POI，境外用 OSM Overpass（双地图方案）
- /api/poi/nearby?lat=&lng=&radius=&limit=  查询某坐标周边带名称的景点/餐饮/酒店等
- /api/poi/detail?lat=&lng=&name=           按名称+坐标精确匹配单个 POI 的详情
中国境内（AMAP_WEB_KEY 配置了且坐标在中国地理范围 18-54N/73-135E）走高德 REST，
其余走 OSM Overpass（ODbL 开放许可，免费合法）
"""
import json
import re
import time
import urllib.request
import urllib.parse

from flask import Blueprint, jsonify, request

from config import Config

poi_bp = Blueprint("poi", __name__, url_prefix="/api/poi")

# ---------- 引擎选择 ----------
def _is_cn(lat, lng):
    """粗判：中国地理范围（经纬度矩形）+ 配置了 Web服务 Key"""
    if not Config.AMAP_WEB_KEY:
        return False
    return 18 <= lat <= 54 and 73 <= lng <= 135


# ---------- 高德分支 ----------
_AMAP_POI_URL = "https://restapi.amap.com/v3/place"


def _amap_nearby(lat, lng, radius, limit):
    # 高德 location 是 lng,lat，关键词模糊 = 全部类别（不指定 type 时返回全部）
    qs = urllib.parse.urlencode({
        "key": Config.AMAP_WEB_KEY,
        "location": "%.6f,%.6f" % (lng, lat),
        "radius": radius,
        "extensions": "all",
        "offset": limit,
        "page": 1,
        "sort": "distance",
    })
    url = _AMAP_POI_URL + "/around?" + qs
    with urllib.request.urlopen(url, timeout=10) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    if raw.get("status") != "1":
        raise RuntimeError("高德 POI 错误: %s / %s" % (raw.get("info"), raw.get("infocode")))
    return [_amap_to_poi(p) for p in raw.get("pois", [])]


def _amap_detail(lat, lng, name, radius=1500):
    # 先用名称关键词搜索，搜不到再走 around 兜底
    try:
        qs = urllib.parse.urlencode({
            "key": Config.AMAP_WEB_KEY,
            "keywords": name,
            "location": "%.6f,%.6f" % (lng, lat),
            "radius": radius,
            "extensions": "all",
            "offset": 10,
            "page": 1,
            "sort": "weight",
        })
        with urllib.request.urlopen(_AMAP_POI_URL + "/around?" + qs, timeout=10) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        items = raw.get("pois", []) if raw.get("status") == "1" else []
        if items:
            # 优先名字完全一致，否则按顺序第一个（weight 排序）
            same = [i for i in items if name == i.get("name") or name in i.get("name")]
            return _amap_to_poi(same[0] if same else items[0])
    except Exception:
        pass
    # 兜底：附近 POI 里找名字包含的
    near = _amap_nearby(lat, lng, min(radius, 800), 15)
    same = [p for p in near if name in p["name"]]
    if same:
        return same[0]
    return near[0] if near else None


def _amap_to_poi(a):
    # 高德 poid 是字符串 id，location "lng,lat"
    lng_s, lat_s = (a.get("location") or ",").split(",", 1) if a.get("location") else (None, None)
    detail = {}
    for src_key, out_key in [("tel", "phone"), ("website", "website"), ("opentime_tip", "opening_hours"),
                             ("rating", "rating"), ("cost", "cost"), ("adcode", "adcode"),
                             ("pname", "province"), ("cityname", "city"), ("adname", "district"),
                             ("business_area", "business_area"), ("type", "type")]:
        v = a.get(src_key)
        if v:
            detail[out_key] = v
    for tag in a.get("business_area_tag") or []:
        if tag and isinstance(tag, dict) and tag.get("tag_name") and tag.get("type"):
            detail.setdefault("tag_" + tag["type"], tag["tag_name"])
    photos = a.get("photos") or []
    if photos:
        detail["photo"] = photos[0].get("url", "")
    kind = a.get("type", "poi").split(";", 1)[0] if isinstance(a.get("type"), str) else "poi"
    return {
        "osm_id": "amap/" + a.get("id", ""),
        "name": a.get("name", ""),
        "name_en": a.get("name_en", "") or "",
        "kind": kind,
        "lat": float(lat_s) if lat_s else None,
        "lng": float(lng_s) if lng_s else None,
        "detail": detail,
    }


# ---------- OSM Overpass 分支 ----------

# Overpass 免费服务镜像，依次尝试（mail.ru 在国内网络最稳定）
_OVERPASS = [
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

# 关注的 OSM 标签（返回给前端展示的详情字段）
_INTERESTING = ["opening_hours", "phone", "contact:phone", "website", "contact:website",
                "wikipedia", "wikidata", "cuisine", "stars", "wheelchair", "fee",
                "operator", "brand", "internet_access", "outdoor_seating"]

# 内存缓存：{(lat,lng,radius): (时间戳, POI 列表)}，1 小时过期
_cache = {}
_TTL = 60 * 60
_CACHE_MAX = 400  # 条目上限，防止长时间运行内存无限增长


def _prune_cache():
    """超上限时先清过期项，仍超则整体清空（POI 可随时重新查询）"""
    if len(_cache) <= _CACHE_MAX:
        return
    now = time.time()
    for k, v in list(_cache.items()):
        if now - v[0] >= _TTL:
            _cache.pop(k, None)
    if len(_cache) > _CACHE_MAX:
        _cache.clear()


def _osm_name_tags():
    """查询条件：有名字的 景点/历史/餐饮/住宿/购物/休闲 兴趣点"""
    return (
        'nwr(around:{r},{lat},{lng})["name"]["tourism"];'
        'nwr(around:{r},{lat},{lng})["name"]["historic"];'
        'nwr(around:{r},{lat},{lng})["name"]["amenity"~"^(restaurant|cafe|fast_food|bar|pub|bank|pharmacy|marketplace)$"];'
    )


def _overpass_query(lat, lng, radius):
    q = "[out:json][timeout:25];(" + _osm_name_tags().format(r=radius, lat=lat, lng=lng) + ");out center tags 80;"
    return _overpass_exec(q)


def _overpass_query_by_name(lat, lng, radius, name):
    """按名称服务端过滤，避免热门区域结果被截断"""
    esc = re.escape(name).replace("\\ ", " ")
    q = ('[out:json][timeout:25];(nwr(around:%d,%s,%s)["name"~"%s",i];);out center tags 8;'
         % (radius, lat, lng, esc))
    return _overpass_exec(q)


def _overpass_exec(q):
    data = urllib.parse.urlencode({"data": q}).encode("utf-8")
    last_err = None
    for ep in _OVERPASS:
        try:
            req = urllib.request.Request(ep, data=data,
                                         headers={"User-Agent": "travel-planner/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # 单个镜像失败换下一个
            last_err = e
    raise RuntimeError("Overpass 服务不可用: %s" % last_err)


def _pick_lat_lng(el):
    if "lat" in el and "lon" in el:
        return el.get("lat"), el.get("lon")
    c = el.get("center") or {}
    return c.get("lat"), c.get("lon")


def _shape(elements, name_filter=None):
    """把 Overpass elements 转成前端友好的列表，name_filter 非空时按名称相似过滤"""
    out = []
    for el in elements or []:
        tags = el.get("tags") or {}
        name = tags.get("name") or tags.get("name:en") or tags.get("name:zh")
        if not name:
            continue
        if name_filter and name_filter.lower() not in (name or "").lower() \
                and name.lower() not in (name_filter or "").lower():
            continue
        lat, lng = _pick_lat_lng(el)
        if lat is None:
            continue
        detail = {t: tags[t] for t in _INTERESTING if t in tags}
        kind = tags.get("tourism") or tags.get("historic") or tags.get("amenity") or "poi"
        out.append({
            "osm_id": "%s/%s" % (el.get("type"), el.get("id")),
            "name": name,
            "name_en": tags.get("name:en", ""),
            "kind": kind,
            "lat": lat,
            "lng": lng,
            "detail": detail,
        })
    return out


@poi_bp.get("/nearby")
def nearby():
    lat = request.args.get("lat", type=float)
    lng = request.args.get("lng", type=float)
    # ?radius=abc 时 type=int 返回 None，需要兜底，否则 max(None, 100) 抛异常 → 500
    radius = request.args.get("radius", 800, type=int) or 800
    radius = min(max(radius, 100), 3000)
    limit = request.args.get("limit", 20, type=int) or 20
    limit = min(max(limit, 1), 50)
    if lat is None or lng is None:
        return jsonify({"code": 1, "msg": "缺少 lat/lng 参数", "data": None}), 400

    cn = _is_cn(lat, lng)
    key = ("amap" if cn else "osm", round(lat, 3), round(lng, 3), radius)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _TTL:
        pois = hit[1]
    else:
        try:
            if cn:
                pois = _amap_nearby(lat, lng, radius, limit + 20)[:limit + 20]
            else:
                raw = _overpass_query(lat, lng, radius)
                pois = _shape(raw.get("elements"))
        except Exception as e:
            return jsonify({"code": 2, "msg": str(e), "data": None}), 502
        _cache[key] = (time.time(), pois)
        _prune_cache()

    return jsonify({"code": 0, "msg": "ok", "data": {
        "center": {"lat": lat, "lng": lng},
        "provider": "amap" if cn else "osm",
        "count": len(pois[:limit]),
        "pois": pois[:limit],
    }})


@poi_bp.get("/detail")
def detail():
    lat = request.args.get("lat", type=float)
    lng = request.args.get("lng", type=float)
    name = (request.args.get("name") or "").strip()
    if not name or lat is None or lng is None:
        return jsonify({"code": 1, "msg": "缺少 name/lat/lng 参数", "data": None}), 400

    cn = _is_cn(lat, lng)
    key = (("amap" if cn else "osm"), "byname", round(lat, 3), round(lng, 3), name.lower())
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return jsonify({"code": 0, "msg": "ok", "data": hit[1]})

    if cn:
        # ----------- 高德 -----------
        try:
            best = _amap_detail(lat, lng, name)
        except Exception as e:
            return jsonify({"code": 2, "msg": str(e), "data": None}), 502
        if best:
            _cache[key] = (time.time(), best)
            _prune_cache()
            return jsonify({"code": 0, "msg": "ok", "data": best})
        return jsonify({"code": 404, "msg": "高德 POI 未匹配到该名称", "data": None}), 404

    # ----------- 境外 OSM -----------
    def _finish(pois, prefer_name=None):
        if not pois:
            return None

        def _score(p):
            exact = 0
            if prefer_name:
                pn = prefer_name.lower()
                n = p["name"].lower()
                ne = (p.get("name_en") or "").lower()
                if n == pn or ne == pn:
                    exact = -2  # 名字完全一致最优先
                elif n.startswith(pn):
                    exact = -1
            d = (p["lat"] - lat) ** 2 + (p["lng"] - lng) ** 2
            return (exact, len(p["name"]), d)

        best = min(pois, key=_score)
        _cache[key] = (time.time(), best)
        _prune_cache()
        return best

    # 1) 服务端按名字过滤（含 name/name:en/name:zh 键），半径放宽到 1.5km
    try:
        esc = re.escape(name).replace("\\ ", " ")
        q = ('[out:json][timeout:25];'
             '(nwr(around:1500,%s,%s)[~"^name(:en|:zh)?$"~"%s",i];);out center tags 15;'
             % (lat, lng, esc))
        best = _finish(_shape(_overpass_exec(q).get("elements")), prefer_name=name)
        if best:
            return jsonify({"code": 0, "msg": "ok", "data": best})
    except Exception as e:
        print("[poi/detail] 名称查询失败，转周边兜底: %s" % e)

    # 2) 兜底：周边查询结果里名称包含匹配 + 距离最近
    try:
        raw = _overpass_query(lat, lng, 1200)
        elements = _shape(raw.get("elements"), name_filter=name)
        # 若名称匹配不到（如中文标题对应日文 OSM name），直接返回最近的 POI，避免 404
        if not elements:
            elements = _shape(raw.get("elements"))
        best = _finish(elements, prefer_name=name)
        if best:
            return jsonify({"code": 0, "msg": "ok", "data": best})
    except Exception as e:
        return jsonify({"code": 2, "msg": str(e), "data": None}), 502
    return jsonify({"code": 404, "msg": "未找到该地点的 OSM 详情", "data": None}), 404
