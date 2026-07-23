# -*- coding: utf-8 -*-
"""通用工具函数"""
import datetime as _dt
import math
import re


def haversine(lat1, lng1, lat2, lng2):
    """计算两点间球面距离（公里）；坐标缺失或非法时返回 0，不抛异常"""
    try:
        lat1, lng1, lat2, lng2 = float(lat1), float(lng1), float(lat2), float(lng2)
    except (TypeError, ValueError):
        return 0.0
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * r * math.asin(math.sqrt(a)), 1)


def geo_point(p):
    """从 dict / 对象中取出合法的 (lat, lng)；不合法返回 None"""
    if not isinstance(p, dict):
        return None
    try:
        lat, lng = float(p.get("lat")), float(p.get("lng"))
    except (TypeError, ValueError):
        return None
    if lat != lat or lng != lng:  # NaN
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return lat, lng


def route_distance(points):
    """根据坐标点列表计算路线总里程（自动忽略缺少/非法经纬度的点）"""
    total = 0.0
    prev = None
    for p in (points or []):
        cur = geo_point(p)
        if cur is None:
            continue
        if prev is not None:
            total += haversine(prev[0], prev[1], cur[0], cur[1])
        prev = cur
    return round(total, 1)


def _levenshtein(a: str, b: str) -> int:
    """计算两字符串编辑距离（ Wagner-Fischer ）"""
    m, n = len(a), len(b)
    if m == 0:
        return n
    if n == 0:
        return m
    prev = list(range(n + 1))
    curr = [0] * (n + 1)
    for i in range(1, m + 1):
        curr[0] = i
        ca = a[i - 1]
        for j in range(1, n + 1):
            cost = 0 if ca == b[j - 1] else 1
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev, curr = curr, prev
    return prev[n]


def _jaccard(a: str, b: str) -> float:
    """字符集合 Jaccard 相似度（0~1）"""
    sa, sb = set(a), set(b)
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def search_score(name: str, alias: str, query: str, name_pinyin: str = ""):
    """
    计算查询词与城市名的匹配分数（字段级模糊评分，支持拼音二次匹配）
    - alias 按 | 拆分为独立字段（name_en / name_local / country_zh / country_en），
      避免长名称整体参与前缀/子串匹配导致误伤（如 "Londonderry County Borough" 压过 "London"）
    - 字段精确等于 +120，字段前缀 +60，字段子串 +40
    - 编辑距离 <=1（短词）+25，单词开头匹配 +10，完整单词 +10
    - Jaccard 相似度额外加分（最高 +15）
    - 拼音二次匹配：查询词转拼音后同样参与字段匹配，解决简称/拼音搜英文名
      （如 name="沪" 时搜 "上海"/"shanghai" 仍能命中 name_en="Shanghai"）
    - name_pinyin: 预计算的城市名拼音（可选，避免重复转换提升性能）
    """
    q = query.strip().lower()
    if not q:
        return 0
    py_q = ""
    try:
        from pypinyin import lazy_pinyin
        py_q = "".join(lazy_pinyin(q)).lower()
    except Exception:
        pass
    queries = [q]
    if py_q and py_q != q:
        queries.append(py_q)

    fields = [name] + [f.strip() for f in alias.split("|") if f.strip()]
    # 拼音匹配：优先使用外部预计算的拼音
    if name_pinyin:
        fields.append(name_pinyin)
    else:
        try:
            from pypinyin import lazy_pinyin
            py = "".join(lazy_pinyin(name))
            if py:
                fields.append(py)
        except Exception:
            pass
    # 去重（name 与 name_en / 拼音可能重复，避免同一文本重复计分）
    # 归一化：小写 + 去重音（如 Ménilmontant 与 Menilmontant 视为同一文本）
    def _norm(s):
        try:
            import unicodedata
            s = unicodedata.normalize("NFKD", s)
            s = "".join(ch for ch in s if not unicodedata.combining(ch))
        except Exception:
            pass
        return s.strip().lower()

    seen = set()
    uniq = []
    for f in fields:
        k = _norm(f)
        if k and k not in seen:
            seen.add(k)
            uniq.append(f)
    fields = uniq

    score = 0
    for text in fields:
        t = text.strip().lower()
        if not t:
            continue
        base = 0   # if 链得分（原文 + 拼音，取最高）
        word_s = 0  # 单词级得分（原文 + 拼音，取最高）
        for qq in queries:
            if t == qq:
                b = 120
            elif t.startswith(qq):
                b = 60
            elif qq in t:
                b = 40
            else:
                b = 0
                # 编辑距离（容错拼写错误 / 缺字）
                if len(qq) <= 5:
                    dist = _levenshtein(qq, t[: len(qq) + 2])
                    if dist == 0:
                        b = 120
                    elif dist == 1:
                        b = 25
                # Jaccard 字符重叠度
                jac = _jaccard(qq, t)
                if jac >= 0.5:
                    b = max(b, int(jac * 15))
            base = max(base, b)
            # 单词级匹配（英文名称中的独立单词）
            for word in t.replace("-", " ").split():
                if len(qq) >= 2:
                    if word == qq:
                        word_s = max(word_s, 10)
                    elif word.startswith(qq):
                        word_s = max(word_s, 5)
        score += base + word_s
    return score


def is_valid_username(username):
    return bool(re.match(r"^[a-zA-Z0-9_\u4e00-\u9fa5]{2,20}$", username))


def is_valid_password(password):
    return len(password) >= 6


def to_mysql_datetime(v):
    """
    把各种输入（ISO 字符串 / datetime 对象 / 空）转成 MySQL DATETIME 接受的
    '%Y-%m-%d %H:%M:%S' 字符串，解析失败时返回 None（由 DB 使用 CURRENT_TIMESTAMP
    或 NULL，取决于列 DEFAULT）。
    """
    if v is None:
        return None
    if isinstance(v, (_dt.datetime, _dt.date)):
        if isinstance(v, _dt.date) and not isinstance(v, _dt.datetime):
            v = _dt.datetime(v.year, v.month, v.day)
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, bytes):
        try:
            v = v.decode("utf-8")
        except Exception:
            return None
    s = str(v).strip()
    if not s:
        return None
    # 去掉末尾 Z / 毫秒保留 6 位内
    s = s.replace("Z", "").replace("T", " ")
    # 截断毫秒 / 微秒到 6 位（Python 支持）
    try:
        # form: 2026-09-01 10:51:33
        if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", s):
            return s
        # 带微秒
        if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+$", s):
            # Python %f 解析 0..6 位小数
            head, frac = s.split(".", 1)
            frac = (frac + "000000")[:6]
            return _dt.datetime.strptime(head + "." + frac, "%Y-%m-%d %H:%M:%S.%f") \
                .strftime("%Y-%m-%d %H:%M:%S")
        # 带时区偏移 如 +08:00 → 去掉
        s2 = re.sub(r"[+\-]\d{2}:?\d{2}$", "", s).strip()
        if s2 != s:
            return to_mysql_datetime(s2)
        # 尝试常见形式
        for fmt in (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y/%m/%d %H:%M:%S",
            "%Y-%m-%d",
            "%Y/%m/%d",
        ):
            try:
                return _dt.datetime.strptime(s2, fmt).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
    except Exception:
        pass
    return None

