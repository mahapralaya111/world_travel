# -*- coding: utf-8 -*-
"""
行程数据模型 + 确定性生成引擎（核心模块）

设计原则：
- 行程是结构化数据（日期、时间、景点、交通、预算），不是 AI 生成的散文
- 生成引擎使用确定性的规则算法：输入 目的地/天数/人数/预算/兴趣/节奏 → 输出 TripPlan
- 不依赖任何 LLM，可离线运行、可复现（同一输入永远得到同一结果）
- AI 只作为"意图解析"的入口（见 intent_engine.py），生成与调整均由本引擎完成

TripPlan JSON Schema（返回给前端的标准结构）：
{
  "meta": {
    "title": str,            // 行程标题
    "start_date": str,       // 开始日期 YYYY-MM-DD
    "days": int,             // 总天数
    "travelers": int,        // 人数
    "total_budget": float,   // 总预算（元）
    "currency": "CNY",
    "pace": "normal",        // relaxed | normal | intensive
    "interests": [str],      // 兴趣标签
    "departure": str,        // 出发城市
    "created_at": str
  },
  "destinations": [          // 目的地分配（多城市时按人口权重拆天）
    {"city": str, "country": str, "country_code": str, "lat": float, "lng": float,
     "days": int, "is_capital": int}
  ],
  "days": [                  // 每日行程（核心）
    {
      "day": int, "date": str, "city": str, "theme": str,
      "activities": [        // 按时间排序的活动
        {"time": "09:00", "type": "sight|food|transport|hotel|free",
         "name": str, "location": str, "lat": float, "lng": float,
         "duration_h": float, "cost": float, "transport": str, "note": str}
      ],
      "budget": {"food": 0, "transport": 0, "tickets": 0, "other": 0, "sum": 0},
      "tips": str
    }
  ],
  "budget_breakdown": {      // 整体预算拆分
    "transport": float, "hotel": float, "food": float,
    "tickets": float, "shopping": float, "other": float, "sum": float
  },
  "summary": str,
  "intents": [str]           // 已应用的调整记录
}
"""
import json
import re

# ---------------------------------------------------------------------------
# 常量与配置
# ---------------------------------------------------------------------------
PACE_CONFIG = {
    "relaxed": {"sights_per_day": 2, "hours_per_sight": 2.5, "rest_blocks": 1},
    "normal": {"sights_per_day": 3, "hours_per_sight": 2.0, "rest_blocks": 0},
    "intensive": {"sights_per_day": 4, "hours_per_sight": 1.5, "rest_blocks": 0},
}

INTEREST_KEYWORDS = {
    "culture": ["寺", "神社", "教堂", "宫殿", "庙", "古迹", "文化遗产", "博物馆", "美术馆", "艺术", "演出", "剧院"],
    "history": ["古城", "城堡", "遗迹", "历史", "纪念馆", "旧", "古迹", "城墙", "陵", "碑"],
    "nature": ["公园", "山", "湖", "海", "海滩", "温泉", "瀑布", "森林", "峡谷", "自然", "动物", "植物园", "岛"],
    "food": ["市场", "美食", "街", "餐", "料理", "海鲜", "小吃", "夜市", "酒吧", "烘焙"],
    "shopping": ["购物", "商业街", "百货", "免税", "奥特莱斯", "市场", "街"],
    "city": ["塔", "观景", "广场", "地标", "中心", "大道", "桥", "老城"],
}

BUDGET_RATIO = {  # 常规预算比例（总和 = 1.00）
    "transport": 0.35, "hotel": 0.22, "food": 0.18,
    "tickets": 0.12, "shopping": 0.08, "other": 0.05,
}

# ---------------------------------------------------------------------------
# 区域人均日消费参考（CNY，来源: ai_planner.py prompt + 常见旅行手册）
# 用于预算校验 + 动态调整行程密度
# ---------------------------------------------------------------------------
REGION_DAILY_BASE = {
    "domestic":  {"label": "中国大陆",  "low": 200, "mid": 400, "high": 600},
    "seasia":    {"label": "东南亚",    "low": 300, "mid": 500, "high": 800},
    "jpkr":      {"label": "日韩",      "low": 500, "mid": 800, "high": 1300},
    "europe":    {"label": "欧洲",      "low": 800, "mid": 1200, "high": 2000},
    "namerica":  {"label": "北美",      "low": 800, "mid": 1200, "high": 2000},
    "ausoc":     {"label": "澳新",      "low": 800, "mid": 1100, "high": 1800},
    "other":     {"label": "其他/未知", "low": 300, "mid": 600, "high": 1000},
}

# country_code → region 映射（ISO 3166-1 alpha-2）
_COUNTRY_REGION = {
    # 国内
    "CN": "domestic", "HK": "domestic", "MO": "domestic", "TW": "domestic",
    # 东南亚
    "TH": "seasia", "VN": "seasia", "ID": "seasia", "MY": "seasia", "SG": "seasia",
    "PH": "seasia", "LA": "seasia", "KH": "seasia", "MM": "seasia", "BN": "seasia",
    # 日韩
    "JP": "jpkr", "KR": "jpkr",
    # 欧洲
    "GB": "europe", "FR": "europe", "DE": "europe", "IT": "europe", "ES": "europe",
    "NL": "europe", "BE": "europe", "CH": "europe", "AT": "europe", "PT": "europe",
    "IE": "europe", "NO": "europe", "SE": "europe", "DK": "europe", "FI": "europe",
    "PL": "europe", "RU": "europe", "GR": "europe", "TR": "europe",
    # 北美
    "US": "namerica", "CA": "namerica", "MX": "namerica",
    # 澳新
    "AU": "ausoc", "NZ": "ausoc",
}


def _country_to_region(country_code):
    cc = (country_code or "").upper()
    return _COUNTRY_REGION.get(cc, "other")


def _budget_modes(person_per_day, region):
    """返回 (budget_level, suggestion)：low / mid / high
    person_per_day = total_budget / travelers / days"""
    base = REGION_DAILY_BASE.get(region, REGION_DAILY_BASE["other"])
    if person_per_day < base["low"]:
        return "low", base
    elif person_per_day < base["mid"]:
        return "mid", base
    else:
        return "high", base

THEME_LIBRARY = [
    "经典地标与城市漫步", "文化历史深度游", "自然风光与休闲", "美食与市井烟火",
    "博物馆与艺术之旅", "老城街巷探索", "购物与潮流打卡", "山海湖光放松日",
]


def _now_str():
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def _add_days(date_str, n):
    """日期加 n 天；日期非法（空串/格式错误/None）时返回空串，不抛异常中断整次生成"""
    import datetime
    try:
        d = datetime.datetime.strptime(str(date_str or "").strip(), "%Y-%m-%d")
    except (TypeError, ValueError):
        return ""
    return (d + datetime.timedelta(days=int(n))).strftime("%Y-%m-%d")


def _num(v, default=0):
    """安全转数字：空值/非法值回退默认，避免 None 参与运算或写进 JSON"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if f == f else default  # 过滤 NaN


# ---------------------------------------------------------------------------
# 数据模型工具
# ---------------------------------------------------------------------------
def empty_plan():
    """返回空的 TripPlan 骨架"""
    return {
        "meta": {
            "title": "", "start_date": "", "days": 0, "travelers": 1,
            "total_budget": 0, "currency": "CNY", "pace": "normal",
            "interests": [], "departure": "", "created_at": _now_str(),
        },
        "destinations": [],
        "days": [],
        "budget_breakdown": {},
        "summary": "",
        "intents": [],
    }


def validate_plan(plan):
    """校验 TripPlan 结构完整性，返回 (ok, msg)"""
    if not isinstance(plan, dict):
        return False, "plan 必须是对象"
    meta = plan.get("meta") or {}
    if not meta.get("days"):
        return False, "缺少行程天数"
    if not isinstance(plan.get("destinations"), list) or not plan["destinations"]:
        return False, "缺少目的地"
    if not isinstance(plan.get("days"), list) or not plan["days"]:
        return False, "缺少每日行程"
    return True, "ok"


# ---------------------------------------------------------------------------
# 兴趣匹配
# ---------------------------------------------------------------------------
def _score_sight(name, interests):
    """根据兴趣关键词给景点打分，返回 (score, matched_tag)"""
    score = 0
    tag = ""
    for interest in interests or []:
        for kw in INTEREST_KEYWORDS.get(interest, []):
            if kw and kw in name:
                score += 2
                tag = interest
    # 通用热度分：名称包含常见词
    if any(k in name for k in ["博物馆", "塔", "宫", "寺", "公园"]):
        score += 1
    return score, tag


def _classify_sights(sights, interests):
    """按兴趣把景点列表分成 推荐/普通 两档"""
    scored = []
    for s in sights:
        sc, tag = _score_sight(s, interests)
        scored.append((sc, s, tag))
    scored.sort(key=lambda x: -x[0])
    return scored


# ---------------------------------------------------------------------------
# 目的地拆天
# ---------------------------------------------------------------------------
def _split_days(destinations, total_days):
    """
    将总天数按城市人口权重拆到各城市，返回每个城市分配的天数列表。
    destinations: [{city, country, country_code, lat, lng, population, is_capital}]
    """
    n = len(destinations)
    if n <= 0:
        return []
    if n == 1:
        return [total_days]
    pops = [max(int(d.get("population") or 0), 1) for d in destinations]
    total_pop = sum(pops)
    days = [max(1, int(total_days * p / total_pop)) for p in pops]
    # 修正：保证总和等于总天数
    diff = total_days - sum(days)
    if diff > 0:
        # 剩余天数给人口最多/首都的城市
        order = sorted(range(n), key=lambda i: (-pops[i], -destinations[i].get("is_capital", 0)))
        for i in range(diff):
            days[order[i % n]] += 1
    elif diff < 0:
        # 天数过多，从人口最少的城市扣（保底 1 天）
        order = sorted(range(n), key=lambda i: pops[i])
        idx = 0
        while diff < 0:
            i = order[idx % n]
            if days[i] > 1:
                days[i] -= 1
                diff += 1
            idx += 1
            if idx > n * 5:
                break
    return days


# ---------------------------------------------------------------------------
# 每日行程生成
# ---------------------------------------------------------------------------
def _city_name(city_row):
    return city_row.get("city") or city_row.get("name") or ""


def _build_day(day_no, date_str, city_row, sights_selected, pace_cfg, daily_budget):
    """构造一天的行程结构"""
    activities = []
    cur_hour = 9.0  # 上午 9 点开始
    city_name = _city_name(city_row)
    lat = _num(city_row.get("lat"))
    lng = _num(city_row.get("lng"))

    # 出发/酒店 锚点（简化表示）
    activities.append({
        "time": "08:30", "type": "hotel", "name": "酒店出发",
        "location": city_name, "lat": lat, "lng": lng,
        "duration_h": 0.3, "cost": 0, "transport": "步行/打车", "note": "整理随身物品，确认当天交通",
    })

    lunch_cost = daily_budget * 0.35
    tickets_cost = 0.0
    transport_cost = daily_budget * 0.18

    lunch_done = False
    for i, (sc, name, tag) in enumerate(sights_selected):
        t = "{:02d}:00".format(min(int(cur_hour), 23))  # 夹紧到 23 点，避免出现 24:00 这类非法时间
        duration = pace_cfg["hours_per_sight"]
        # 简单估算门票
        est_cost = 40 if any(k in name for k in ["博物馆", "美术馆", "城堡", "演出"]) else 20
        note = ""
        if tag == "culture":
            note = "人文体验，建议预留拍照与讲解时间"
        elif tag == "nature":
            note = "户外活动，注意防晒与舒适着装"
        elif tag == "food":
            note = "当地特色，可顺路解决餐饮"
        elif tag == "shopping":
            note = "购物时间灵活，注意免税额度"
        activities.append({
            "time": t, "type": "sight", "name": name,
            "location": city_name, "lat": lat, "lng": lng,
            "duration_h": duration, "cost": round(est_cost, 1),
            "transport": "地铁/公交" if i > 0 else "步行",
            "note": note,
        })
        tickets_cost += est_cost
        cur_hour += duration

        # 上午 11 点后插入午餐（固定 12:00，午餐后 13:00 继续行程）
        if not lunch_done and cur_hour >= 11.0:
            activities.append({
                "time": "12:00", "type": "food", "name": "午餐 · 当地特色餐厅",
                "location": city_name, "lat": lat, "lng": lng,
                "duration_h": 1.0, "cost": round(lunch_cost, 1),
                "transport": "步行", "note": "根据口味选择，人均消费已计入预算",
            })
            cur_hour = 13.0
            lunch_done = True

    # 傍晚交通返回酒店
    if cur_hour < 18.5:
        activities.append({
            "time": "{:02d}:30".format(min(int(cur_hour), 23)), "type": "transport",
            "name": "返回酒店 / 晚间自由活动",
            "location": city_name, "lat": lat, "lng": lng,
            "duration_h": 1.0, "cost": round(transport_cost, 1),
            "transport": "地铁/公交/打车", "note": "可提前规划次日出发路线",
        })
        cur_hour += 1.0

    # 夜间自由活动
    activities.append({
        "time": "20:00", "type": "free", "name": "夜间自由活动",
        "location": city_name, "lat": lat, "lng": lng,
        "duration_h": 2.0, "cost": round(daily_budget * 0.12, 1),
        "transport": "步行", "note": "夜景、夜宵或休息，按精力自选",
    })

    # 预算汇总（除酒店外单日预算 = 城市预算份额 / 城市天数）
    food_b = round(daily_budget * 0.35, 1)
    trans_b = round(transport_cost, 1)
    tickets_b = round(tickets_cost, 1)
    other_b = round(daily_budget - food_b - trans_b - tickets_b, 1)

    theme = THEME_LIBRARY[day_no % len(THEME_LIBRARY)]
    tips = "今日节奏：{}；建议穿舒适鞋，提前查询景点开放时间与预约要求。".format(
        "轻松" if pace_cfg["sights_per_day"] <= 2 else ("紧凑" if pace_cfg["sights_per_day"] >= 4 else "适中"))

    return {
        "day": day_no,
        "date": date_str,
        "city": city_name,
        "theme": theme,
        "activities": activities,
        "budget": {"food": food_b, "transport": trans_b, "tickets": tickets_b, "other": other_b,
                   "sum": round(food_b + trans_b + tickets_b + other_b, 1)},
        "tips": tips,
    }


# ---------------------------------------------------------------------------
# 主生成入口
# ---------------------------------------------------------------------------
def generate_plan(destinations, days=7, travelers=2, total_budget=10000,
                  interests=None, pace="normal", departure="上海",
                  start_date=None, title=None, currency="CNY"):
    """
    确定性生成行程。

    参数：
        destinations: list[dict]，每个元素至少含 {city, country, country_code, lat, lng,
                      population, is_capital}。可由城市路由/前端传入。
        days: 总天数
        travelers: 人数
        total_budget: 总预算（元）
        interests: list[str]，兴趣标签
        pace: relaxed / normal / intensive
        departure: 出发城市
        start_date: 开始日期 YYYY-MM-DD（缺省用今天）
        title: 行程标题（缺省自动生成）
    """
    import datetime

    # 入参兜底：调用方可能传字符串/None（如前端表单），这里统一转成安全数值
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 7
    try:
        travelers = int(travelers or 1)
    except (TypeError, ValueError):
        travelers = 1
    try:
        total_budget = float(total_budget or 0)
    except (TypeError, ValueError):
        total_budget = 0.0
    if total_budget != total_budget or total_budget < 0:  # NaN / 负数
        total_budget = 0.0

    if not destinations:
        return empty_plan()
    if days < 1:
        days = 1
    if days > 60:
        days = 60
    if not interests or not isinstance(interests, list):
        interests = ["city", "culture"]
    if pace not in PACE_CONFIG:
        pace = "normal"
    start_date = start_date or datetime.date.today().strftime("%Y-%m-%d")

    # 1. 目的地拆天
    day_split = _split_days(destinations, days)

    # --- 预算校验：计算人均日花费，决定行程密度 ---
    travelers = max(travelers, 1)
    person_per_day = total_budget / travelers / days if (total_budget and days) else 0
    # 主要区域 = 第一个目的地（简化：多区域取第一个）
    main_region = _country_to_region(destinations[0].get("country_code") if destinations else "")
    budget_level, region_base = _budget_modes(person_per_day, main_region)

    # 预算影响 sight_count：low 降级 1 档；high 保持用户选择；mid 不变
    # （用户传 pace=intensive 但预算 low 时，不给他排 4 个景点/天）
    pace_cfg = PACE_CONFIG[pace]
    sight_count = pace_cfg["sights_per_day"]
    if budget_level == "low":
        # 降级一档
        sight_count = max(1, sight_count - 1)
    elif budget_level == "high" and pace == "intensive":
        # 预算充足时允许紧凑节奏不变，不额外加景点
        pass
    # 预算极低时进一步限制
    if person_per_day > 0 and person_per_day < region_base["low"] * 0.5:
        sight_count = 1

    # 2. 每日行程
    plan = empty_plan()
    plan_days = []

    # 预算：城市间按天数占比分配（酒店预算按比例单列，这里把餐饮/门票/市内交通并入 daily）
    day_budget_base = total_budget * (BUDGET_RATIO["food"] + BUDGET_RATIO["tickets"]
                                      + BUDGET_RATIO["transport"] * 0.5 + BUDGET_RATIO["other"])
    # 除以 travelers：每天预算应按人均算

    date_cursor = start_date
    day_no = 1
    for city_idx, city_row in enumerate(destinations):
        # 取该城市景点并按兴趣排序
        sights = [s for s in (city_row.get("sights") or []) if isinstance(s, str)]
        scored = _classify_sights(sights, interests)
        pool = [x[1] for x in scored] or (sights or ["城市地标与老城漫步", "城市博物馆", "中央公园"])

        city_days = day_split[city_idx]
        # 人均每天预算 = 城市总预算份额 / 城市天数 / 人数（¥1 保底）
        per_day_budget = max(day_budget_base / max(city_days, 1) / travelers, 1)

        # 每天从景点池中轮转选取（不重复）
        start_idx = 0
        for d in range(city_days):
            selected = []
            for k in range(sight_count):
                pick = pool[(start_idx + k) % len(pool)]
                if pick not in [x[1] for x in selected]:
                    selected.append((0, pick, ""))
                else:
                    # 去重：顺延取下一个
                    j = 1
                    while pick in [x[1] for x in selected] and j < len(pool):
                        pick = pool[(start_idx + k + j) % len(pool)]
                        j += 1
                    if pick not in [x[1] for x in selected]:
                        selected.append((0, pick, ""))
            start_idx += sight_count

            day = _build_day(day_no, date_cursor, city_row, selected, pace_cfg,
                             per_day_budget)
            plan_days.append(day)
            date_cursor = _add_days(date_cursor, 1)
            day_no += 1

    plan["days"] = plan_days

    # 3. 目的地
    # 除展示所需字段外，额外保留 sights / population：
    # 意图调整（如"加一天/改节奏"）会基于 meta+destinations 重新生成行程，
    # 没有这两个字段就会丢掉原景点列表并改变城市天数分配。
    plan["destinations"] = [
        {"city": d.get("city", ""), "country": d.get("country", ""),
         "country_code": d.get("country_code", ""), "lat": _num(d.get("lat")), "lng": _num(d.get("lng")),
         "days": day_split[i], "is_capital": int(_num(d.get("is_capital"))),
         "population": int(_num(d.get("population"))),
         "sights": [s for s in (d.get("sights") or []) if isinstance(s, str)]}
        for i, d in enumerate(destinations)
    ]

    # 4. 预算拆分
    transport_b = round(total_budget * BUDGET_RATIO["transport"], 1)
    hotel_b = round(total_budget * BUDGET_RATIO["hotel"], 1)
    food_b = round(total_budget * BUDGET_RATIO["food"], 1)
    tickets_b = round(total_budget * BUDGET_RATIO["tickets"], 1)
    shopping_b = round(total_budget * BUDGET_RATIO["shopping"], 1)
    other_b = round(total_budget * BUDGET_RATIO["other"], 1)
    plan["budget_breakdown"] = {
        "transport": transport_b, "hotel": hotel_b, "food": food_b,
        "tickets": tickets_b, "shopping": shopping_b, "other": other_b,
        "sum": round(transport_b + hotel_b + food_b + tickets_b + shopping_b + other_b, 1),
    }

    # 5. meta / summary（含预算校验提示）
    city_names = "、".join(d["city"] for d in plan["destinations"])
    title = title or "{} {}天行程规划".format(city_names, days)
    plan["meta"].update({
        "title": title,
        "start_date": start_date,
        "days": days,
        "travelers": travelers,
        "total_budget": round(float(total_budget), 1),
        "currency": currency,
        "pace": pace,
        "interests": interests,
        "departure": departure,
        "created_at": _now_str(),
    })

    # --- 预算提示段 ---
    budget_tip_parts = []
    if person_per_day > 0:
        region_label = region_base["label"]
        if budget_level == "low":
            suggested = int(region_base["mid"] * travelers * days)
            budget_tip_parts.append(
                "⚠️ 预算偏紧：按{}消费水平，建议人均每天 ≥{} 元（当前仅 {:.0f} 元/人/天）。"
                "系统已自动减少每日景点密度。预算总额建议提高到约 {} 元。".format(
                    region_label, region_base["mid"], person_per_day, suggested))
        elif budget_level == "mid":
            budget_tip_parts.append(
                "💰 预算适中：按{}消费水平，人均每天约 {:.0f} 元，可保证性价比行程。".format(
                    region_label, person_per_day))
        else:  # high
            budget_tip_parts.append(
                "✨ 预算充足：按{}消费水平，人均每天 {:.0f} 元，可考虑更优质住宿/升级体验。".format(
                    region_label, person_per_day))

    base_summary = (
        "{}出发，行程 {} 天，途经 {}。总预算 {} 元，人均每天约 {:.0f} 元，"
        "其中大交通 {} 元、住宿 {} 元、餐饮 {} 元。整体节奏：{}。推荐兴趣：{}。"
    ).format(
        departure, days, city_names, int(total_budget),
        person_per_day,
        int(transport_b), int(hotel_b), int(food_b),
        {"relaxed": "轻松", "normal": "适中", "intensive": "紧凑"}.get(pace, "适中"),
        "、".join(interests),
    )
    if budget_tip_parts:
        plan["summary"] = base_summary + "\n\n" + " ".join(budget_tip_parts)
    else:
        plan["summary"] = base_summary

    return plan


def plan_to_schedule_text(plan):
    """把 TripPlan 转成可读的纯文本（供 AI 意图解析/离线页 fallback 使用）"""
    lines = ["# " + plan["meta"]["title"], plan.get("summary", ""), ""]
    for day in plan["days"]:
        lines.append("## Day {}-{}".format(day["day"], day["date"]))
        lines.append("城市：{} | 主题：{}".format(day["city"], day["theme"]))
        for act in day["activities"]:
            lines.append("  {}  {} · {}".format(act["time"], act["name"], act["type"]))
        lines.append("")
    return "\n".join(lines)


def plan_to_markdown(plan):
    """把 TripPlan 转成 Markdown（供离线页渲染/导出）"""
    md = ["# " + plan["meta"]["title"], "", plan.get("summary", ""), "",
          "## 目的地", ""]
    for d in plan["destinations"]:
        md.append("- {}（{}）：{} 天".format(d["city"], d.get("country", ""), d["days"]))
    md += ["", "## 每日行程", ""]
    for day in plan["days"]:
        md.append("### Day {}-{} · {} · {}".format(day["day"], day["date"], day["city"], day["theme"]))
        md.append("")
        md.append("| 时间 | 活动 | 类型 | 交通 | 预估费用 |")
        md.append("|------|------|------|------|---------|")
        for act in day["activities"]:
            md.append("| {} | {} | {} | {} | {} |".format(
                act["time"], act["name"], act["type"], act.get("transport", ""), act["cost"]))
        md.append("")
        md.append("> {}".format(day["tips"]))
        md.append("")
    md += ["## 预算拆分", ""]
    bd = plan.get("budget_breakdown", {})
    for k, v in bd.items():
        if k != "sum":
            md.append("- {}：{} 元".format({"transport": "大交通", "hotel": "住宿", "food": "餐饮",
                                           "tickets": "门票", "shopping": "购物", "other": "其他"}.get(k, k), v))
    return "\n".join(md)
