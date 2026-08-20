# -*- coding: utf-8 -*-
"""标志性景点路由：返回精选的全球著名地标数据"""
import json
import os

from flask import Blueprint, jsonify, request

from config import Config

landmarks_bp = Blueprint("landmarks", __name__, url_prefix="/api/landmarks")

_cache = {"ts": 0.0, "data": None}


def _load_landmarks():
    if _cache["data"] is not None:
        return _cache["data"]
    path = os.path.join(Config.DATA_DIR, "landmarks.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            _cache["data"] = json.load(f)
    except Exception:
        _cache["data"] = []
    return _cache["data"]


def ok(data=None, msg="success"):
    return jsonify({"code": 0, "msg": msg, "data": data})


@landmarks_bp.route("", methods=["GET"])
def list_landmarks():
    """
    获取标志性景点，支持过滤：
      - country_code=CN   :  按国家码过滤
      - bounds=sw_lat,sw_lng,ne_lat,ne_lng :  按视野范围过滤
      - category=历史文化  :  按分类过滤
      - limit=200         :  限制返回数量
    """
    data = _load_landmarks()

    cc = request.args.get("country_code", "").strip().upper()
    bounds = request.args.get("bounds", "").strip()
    cat = request.args.get("category", "").strip()
    limit = request.args.get("limit", 500, type=int) or 500
    limit = min(max(limit, 1), 1000)

    if cc:
        data = [x for x in data if x.get("country_code", "").upper() == cc]

    if bounds:
        try:
            parts = [float(p) for p in bounds.split(",")]
            if len(parts) == 4:
                sw_lat, sw_lng, ne_lat, ne_lng = parts
                data = [x for x in data
                        if sw_lat <= x.get("lat", 0) <= ne_lat
                        and sw_lng <= x.get("lng", 0) <= ne_lng]
        except (ValueError, TypeError):
            pass

    if cat:
        data = [x for x in data if cat in x.get("category", "")]

    data = data[:limit]
    return ok(data)


@landmarks_bp.route("/categories", methods=["GET"])
def list_categories():
    """返回景点分类统计"""
    data = _load_landmarks()
    cats = {}
    for x in data:
        c = x.get("category", "其他")
        cats[c] = cats.get(c, 0) + 1
    items = sorted(cats.items(), key=lambda x: -x[1])
    return ok([{"category": k, "count": v} for k, v in items])


@landmarks_bp.route("/stats", methods=["GET"])
def stats():
    """总体统计"""
    data = _load_landmarks()
    countries = set(x.get("country_code", "") for x in data if x.get("country_code"))
    cats = set(x.get("category", "") for x in data if x.get("category"))
    return ok({
        "total": len(data),
        "countries": len(countries),
        "categories": sorted(cats),
    })
