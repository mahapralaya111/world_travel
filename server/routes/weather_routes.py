# -*- coding: utf-8 -*-
"""天气路由：境内走高德，境外走 Open-Meteo（免费无 Key）
- /api/weather?lat=&lng=&days=7  按经纬度返回未来 N 天预报
"""
import json
import time
import urllib.request
import urllib.parse

from flask import Blueprint, jsonify, request

from config import Config

weather_bp = Blueprint("weather", __name__, url_prefix="/api/weather")

# WMO 天气代码 -> (中文, emoji)
_WMO = {
    0: ("晴", "☀️"), 1: ("基本晴", "🌤️"), 2: ("多云", "⛅"), 3: ("阴", "☁️"),
    45: ("雾", "🌫️"), 48: ("雾凇", "🌫️"),
    51: ("小毛毛雨", "🌦️"), 53: ("毛毛雨", "🌦️"), 55: ("大毛毛雨", "🌧️"),
    56: ("冻毛毛雨", "🌧️"), 57: ("强冻毛毛雨", "🌧️"),
    61: ("小雨", "🌦️"), 63: ("中雨", "🌧️"), 65: ("大雨", "🌧️"),
    66: ("冻雨", "🌧️"), 67: ("强冻雨", "🌧️"),
    71: ("小雪", "🌨️"), 73: ("中雪", "🌨️"), 75: ("大雪", "❄️"), 77: ("雪粒", "❄️"),
    80: ("阵雨", "🌦️"), 81: ("中阵雨", "🌧️"), 82: ("强阵雨", "⛈️"),
    85: ("小阵雪", "🌨️"), 86: ("大阵雪", "❄️"),
    95: ("雷阵雨", "⛈️"), 96: ("雷阵雨伴冰雹", "⛈️"), 99: ("强雷暴冰雹", "⛈️"),
}

# 高德天气文字 -> emoji
_AMAP_EMOJI = {
    "晴": "☀️", "多云": "⛅", "阴": "☁️", "少云": "🌤️", "晴间多云": "🌤️",
    "阵雨": "🌦️", "雷阵雨": "⛈️", "雷阵雨伴有冰雹": "⛈️",
    "小雨": "🌦️", "中雨": "🌧️", "大雨": "🌧️", "暴雨": "🌧️", "大暴雨": "🌧️", "特大暴雨": "🌧️",
    "小到中雨": "🌧️", "中到大雨": "🌧️", "大到暴雨": "🌧️", "暴雨到大暴雨": "🌧️", "大暴雨到特大暴雨": "🌧️",
    "冻雨": "🌧️", "雨夹雪": "🌧️", "阵雪": "🌨️",
    "小雪": "🌨️", "中雪": "🌨️", "大雪": "❄️", "暴雪": "❄️", "小到中雪": "🌨️", "中到大雪": "🌨️", "大到暴雪": "❄️",
    "浮尘": "🌫️", "扬沙": "🌫️", "沙尘暴": "🌫️", "强沙尘暴": "🌫️",
    "雾": "🌫️", "霾": "🌫️",
}

_cache = {}
_TTL = 30 * 60
_CACHE_MAX = 500  # 缓存条目上限，防止长时间运行内存无限增长


def _prune_cache():
    """超过上限时先清过期项，仍超上限则整体清空（天气数据可随时重取）"""
    if len(_cache) <= _CACHE_MAX:
        return
    now = time.time()
    for k, v in list(_cache.items()):
        if now - v[0] >= _TTL:
            _cache.pop(k, None)
    if len(_cache) > _CACHE_MAX:
        _cache.clear()


# ---------- 工具 ----------
def _is_cn(lat, lng):
    """中国地理范围矩形（避开日本琉球/北海道误判）+ 配置了 Web 服务 Key。
    中国真正最东端：黑龙江与乌苏里江汇合 134.46°E，但该经度东
    北偏北(>48°N)；日本四国/本州在 130-142°E，31-45°N，两者重叠。
    所以对 30~50°N 区间把东边界收紧到 130°E（足够覆盖东部沪苏浙闽
    及台湾 120-122°E）；>50°N 仍允许到 135°E，保证黑瞎子岛在中国侧。"""
    if not Config.AMAP_WEB_KEY:
        return False
    if not (17.5 <= lat <= 54):
        return False
    if lat >= 50:
        return 73 <= lng <= 135
    return 73 <= lng <= 130.5


def _wmo_desc(code):
    """WMO 代码 -> 描述；代码非法/缺失时回退为"未知"，不抛异常"""
    try:
        code = int(code or 0)
    except (TypeError, ValueError):
        code = 0
    return _WMO.get(code, ("未知", "🌡️"))


def _amap_emoji(text):
    if not text:
        return "🌡️"
    return _AMAP_EMOJI.get(text, "🌡️")


def _amap_regeo(lat, lng):
    """高德逆地理：坐标 -> adcode（失败回退 110000 北京）"""
    qs = urllib.parse.urlencode({
        "key": Config.AMAP_WEB_KEY,
        "location": "%.6f,%.6f" % (lng, lat),
    })
    url = "https://restapi.amap.com/v3/geocode/regeo?" + qs
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return "110000", ""
    if raw.get("status") != "1":
        return "110000", ""
    regeo = raw.get("regeocode") or {}
    addr = regeo.get("addressComponent") or {}
    adcode = addr.get("adcode") or "110000"
    if not adcode:
        adcode = "110000"
    return adcode, regeo.get("formatted_address", "")


# ---------- 高德天气 ----------
def _amap_weather(lat, lng, days):
    adcode, _ = _amap_regeo(lat, lng)
    ext = "all" if days > 1 else "base"  # 1天实时 / 多天预报
    qs = urllib.parse.urlencode({
        "key": Config.AMAP_WEB_KEY,
        "city": adcode,
        "extensions": ext,
    })
    url = "https://restapi.amap.com/v3/weather/weatherInfo?" + qs
    with urllib.request.urlopen(url, timeout=10) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    if raw.get("status") != "1":
        raise RuntimeError("高德天气错误: %s / %s" % (raw.get("info"), raw.get("infocode")))

    today_raw = (raw.get("lives") or [None])[0]
    if ext == "base":
        daily = []
        if today_raw:
            daily.append({
                "date": (today_raw.get("reporttime") or "")[:10],
                "weather": today_raw.get("weather", "") or "",
                "emoji": _amap_emoji(today_raw.get("weather", "")),
                "temp_max": float(today_raw.get("temperature") or 0),
                "temp_min": float(today_raw.get("temperature") or 0),
                "precip_prob": None,
                "_live": True,
            })
    else:
        daily = []
        forecasts = raw.get("forecasts") or []
        first = forecasts[0] if forecasts else {}
        casts = first.get("casts") or []
        # 第 0 天(今天)的实时数据优先用 lives 的温度，更准
        live_temp = None
        if today_raw and today_raw.get("temperature") not in (None, ""):
            try:
                live_temp = float(today_raw.get("temperature"))
            except (TypeError, ValueError):
                live_temp = None
        for i, c in enumerate((casts or [])[:days]):
            try:
                tmax = float(c.get("daytemp") or 0)
            except (TypeError, ValueError):
                tmax = 0
            try:
                tmin = float(c.get("nighttemp") or 0)
            except (TypeError, ValueError):
                tmin = 0
            # 用白天的天气（如果是今天且有实时天气，优先用 live 的天气描述和温度）
            if i == 0 and today_raw:
                w = today_raw.get("weather") or c.get("dayweather") or ""
                if live_temp is not None:
                    tmax = max(tmax, live_temp)
            else:
                w = c.get("dayweather") or ""
            daily.append({
                "date": c.get("date") or "",
                "weather": w,
                "emoji": _amap_emoji(w),
                "temp_max": tmax,
                "temp_min": tmin,
                "precip_prob": None,
                "wind_dir": c.get("daywind", ""),
                "wind_power": c.get("daypower", ""),
            })
    return {"location": {"lat": lat, "lng": lng, "adcode": adcode}, "daily": daily}


# ---------- Open-Meteo 天气 ----------
def _om_weather(lat, lng, days):
    qs = urllib.parse.urlencode({
        "latitude": lat,
        "longitude": lng,
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone": "auto",
        "forecast_days": days,
    })
    url = "https://api.open-meteo.com/v1/forecast?" + qs
    req = urllib.request.Request(url, headers={"User-Agent": "travel-planner/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    daily = raw.get("daily", {})
    dates = daily.get("time", [])
    wc = daily.get("weather_code") or []
    tmax = daily.get("temperature_2m_max") or []
    tmin = daily.get("temperature_2m_min") or []
    pmax = daily.get("precipitation_probability_max") or []

    def _get(arr, i):
        if arr is None or i >= len(arr):
            return None
        return arr[i]

    return {
        "location": {"lat": lat, "lng": lng, "timezone": raw.get("timezone", "")},
        "daily": [
            {
                "date": d,
                "weather": _wmo_desc(_get(wc, i))[0],
                "emoji": _wmo_desc(_get(wc, i))[1],
                "temp_max": _get(tmax, i),
                "temp_min": _get(tmin, i),
                "precip_prob": _get(pmax, i),
            }
            for i, d in enumerate(dates)
        ],
    }


# ---------- Handler ----------
@weather_bp.get("")
def get_weather():
    lat = request.args.get("lat", type=float)
    lng = request.args.get("lng", type=float)
    # ?days=abc 时 type=int 返回 None，必须兜底，否则 max(None, 1) 会抛异常 → 500
    days = request.args.get("days", 7, type=int)
    if not isinstance(days, int):
        days = 7
    days = min(max(days, 1), 16)
    if lat is None or lng is None:
        return jsonify({"code": 1, "msg": "缺少 lat/lng 参数", "data": None}), 400

    cn = _is_cn(lat, lng)
    key = (("amap" if cn else "om"), round(lat, 2), round(lng, 2), days)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return jsonify({"code": 0, "msg": "ok", "data": hit[1]})

    try:
        if cn:
            data = _amap_weather(lat, lng, days)
            # 高德预报最多返回 4 天 (casts)，当用户请求 5~16 天不足时，
            # 用 Open-Meteo 补齐后续天数，保证 UI 渲染一致的逐日数量。
            if len(data.get("daily") or []) < days:
                try:
                    extra = _om_weather(lat, lng, days)
                    exist = {(d.get("date") or "") for d in data["daily"] if d.get("date")}
                    for d in extra.get("daily") or []:
                        if len(data["daily"]) >= days:
                            break
                        if d.get("date") and d["date"] not in exist:
                            data["daily"].append(d)
                            exist.add(d["date"])
                except Exception:
                    # 补齐失败不影响主结果（毕竟已经拿到了 4 天高德）
                    pass
            data["provider"] = "amap"
        else:
            data = _om_weather(lat, lng, days)
            data["provider"] = "open-meteo"
    except Exception as e:
        return jsonify({"code": 2, "msg": "天气服务请求失败: %s" % e, "data": None}), 502

    _cache[key] = (time.time(), data)
    _prune_cache()
    return jsonify({"code": 0, "msg": "ok", "data": data})

