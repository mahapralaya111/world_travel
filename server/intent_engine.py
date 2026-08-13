# -*- coding: utf-8 -*-
"""
AI 意图解析 + 确定性执行（第二阶段）

设计原则：
- 先定义"意图 JSON Schema"：用户的一切自然语言调整，先解析成结构化意图指令
- 意图指令由"确定性变换函数"执行（不依赖 LLM，结果可复现）
- LLM 只负责"自然语言 → 意图 JSON"这一步；若未配置 AI Key，
  则降级为规则解析器（关键词匹配），保证功能可用

意图 JSON Schema：
{
  "intent": "adjust_pace" | "adjust_budget" | "change_interests" | "add_day" |
            "remove_day"   | "swap_days"     | "move_activity"   | "add_activity" |
            "remove_activity" | "custom_note",
  "params": {...},        // 各意图对应的参数
  "raw": "用户的原始输入"   // 记录原始文本（用于展示/审计）
}
"""
import re
import plan_engine

# ---------------------------------------------------------------------------
# 意图 Schema 定义（供文档 / 校验 / 前端表单动态生成使用）
# ---------------------------------------------------------------------------
INTENT_SCHEMA = {
    "adjust_pace": {
        "description": "调整行程节奏",
        "params": {"pace": {"type": "enum", "values": ["relaxed", "normal", "intensive"],
                            "required": True, "label": "新节奏"}},
    },
    "adjust_budget": {
        "description": "调整总预算（按百分比增减）",
        "params": {"delta_pct": {"type": "number", "required": True, "label": "增减百分比（如 -20 / +10）"}},
    },
    "change_interests": {
        "description": "更改兴趣偏好（影响景点选取）",
        "params": {"interests": {"type": "list", "required": True, "label": "兴趣标签列表"}},
    },
    "add_day": {
        "description": "增加一天（自动追加到末尾）",
        "params": {},
    },
    "remove_day": {
        "description": "移除某一天",
        "params": {"day": {"type": "int", "required": True, "label": "要移除的天数序号"}},
    },
    "swap_days": {
        "description": "交换两天",
        "params": {"day_a": {"type": "int", "required": True, "label": "天 A"},
                   "day_b": {"type": "int", "required": True, "label": "天 B"}},
    },
    "move_activity": {
        "description": "把某一天的活动移动到另一天",
        "params": {"from_day": {"type": "int", "required": True, "label": "来源天"},
                   "activity_index": {"type": "int", "required": True, "label": "活动序号"},
                   "to_day": {"type": "int", "required": True, "label": "目标天"}},
    },
    "add_activity": {
        "description": "在指定天添加一条活动",
        "params": {"day": {"type": "int", "required": True, "label": "目标天"},
                   "name": {"type": "str", "required": True, "label": "活动名称"},
                   "type": {"type": "enum", "values": ["sight", "food", "transport", "free"],
                            "required": False, "label": "活动类型"}},
    },
    "remove_activity": {
        "description": "移除某天的某条活动",
        "params": {"day": {"type": "int", "required": True, "label": "目标天"},
                   "activity_index": {"type": "int", "required": True, "label": "活动序号"}},
    },
    "custom_note": {
        "description": "自定义备注（记录在行程 intents 中，不改变结构）",
        "params": {"note": {"type": "str", "required": True, "label": "备注内容"}},
    },
}

INTEREST_ALIAS = {
    "文化": "culture", "人文": "culture", "艺术": "culture", "博物馆": "culture", "culture": "culture",
    "历史": "history", "古迹": "history", "历史遗迹": "history", "history": "history",
    "自然": "nature", "风光": "nature", "户外": "nature", "山水": "nature", "nature": "nature",
    "美食": "food", "吃": "food", "餐饮": "food", "food": "food",
    "购物": "shopping", "买": "shopping", "逛街": "shopping", "shopping": "shopping",
    "城市": "city", "都市": "city", "地标": "city", "city": "city",
}

PACES = ["relaxed", "normal", "intensive"]


def validate_intent(intent):
    """校验意图指令，返回 (ok, msg)"""
    if not isinstance(intent, dict):
        return False, "intent 必须是对象"
    name = intent.get("intent")
    if name not in INTENT_SCHEMA:
        return False, "未知意图类型: {}".format(name)
    params = intent.get("params") or {}
    schema = INTENT_SCHEMA[name]["params"]
    for pkey, pconf in schema.items():
        if pconf.get("required") and (pkey not in params or params.get(pkey) in (None, "")):
            return False, "缺少参数: {}".format(pkey)
    return True, "ok"


# ---------------------------------------------------------------------------
# 确定性变换函数（核心：每个意图都对应一个纯函数式变换）
# ---------------------------------------------------------------------------
def _regen_day(plan, day_no):
    """按当前 meta 重新生成完整行程（add_day / 结构调整时复用）

    所有字段都用 .get + 默认值读取：分享导入或历史版本保存的 plan 可能缺字段，
    直接下标取值会 KeyError 导致接口 500。
    """
    meta = plan.get("meta") or {}
    destinations = plan.get("destinations") or []
    if not destinations:
        return plan  # 数据不完整时不重建，避免产出空行程
    try:
        days = max(1, int(meta.get("days") or 1))
        travelers = max(1, int(meta.get("travelers") or 2))
        total_budget = float(meta.get("total_budget") or 10000)
    except (TypeError, ValueError):
        return plan
    return plan_engine.generate_plan(
        destinations=destinations,
        days=days,
        travelers=travelers,
        total_budget=total_budget,
        interests=meta.get("interests") or ["city", "culture"],
        pace=meta.get("pace") or "normal",
        departure=meta.get("departure") or "上海",
        start_date=meta.get("start_date") or "",
        title=meta.get("title"),
        currency=meta.get("currency", "CNY"),
    )


def apply_adjust_pace(plan, params):
    pace = params.get("pace")
    if pace not in PACES:
        return plan, "节奏参数无效"
    plan["meta"]["pace"] = pace
    new_plan = _regen_day(plan, None)
    new_plan["intents"] = plan["intents"] + ["节奏调整为 {}".format(pace)]
    return new_plan, "ok"


def apply_adjust_budget(plan, params):
    try:
        delta = float(params.get("delta_pct", 0))
    except (TypeError, ValueError):
        return plan, "预算参数无效"
    if not -90 <= delta <= 500:
        return plan, "预算调整幅度过大（-90% ~ +500%）"
    new_budget = round(plan["meta"]["total_budget"] * (1 + delta / 100.0), 1)
    plan["meta"]["total_budget"] = new_budget
    new_plan = _regen_day(plan, None)
    new_plan["intents"] = plan["intents"] + ["总预算调整为 {} 元（{}%）".format(int(new_budget), delta)]
    return new_plan, "ok"


def apply_change_interests(plan, params):
    interests = params.get("interests") or []
    if isinstance(interests, str):
        interests = [x.strip() for x in re.split(r"[,，、\s]+", interests) if x.strip()]
    mapped = []
    for it in interests:
        mapped.append(INTEREST_ALIAS.get(str(it).lower(), str(it).lower()))
    if not mapped:
        return plan, "兴趣标签无效"
    plan["meta"]["interests"] = mapped
    new_plan = _regen_day(plan, None)
    new_plan["intents"] = plan["intents"] + ["兴趣偏好调整为 {}".format("、".join(mapped))]
    return new_plan, "ok"


def apply_add_day(plan, params):
    if plan["meta"]["days"] >= 60:
        return plan, "天数已达上限 60"
    plan["meta"]["days"] += 1
    new_plan = _regen_day(plan, None)
    new_plan["intents"] = plan["intents"] + ["增加一天行程"]
    return new_plan, "ok"


def apply_remove_day(plan, params):
    days = plan.get("days") or []
    days_count = (plan.get("meta") or {}).get("days") or len(days)
    if days_count <= 1:
        return plan, "至少保留 1 天"
    raw = params.get("day")
    if raw in (None, "", 0):
        day = days_count  # 未指定具体天数（如"减一天"）→ 默认移除最后一天
    else:
        try:
            day = int(raw)
        except (TypeError, ValueError):
            return plan, "天数序号无效"
    if not 1 <= day <= days_count:
        return plan, "天数序号越界"
    # 移除该天：直接从 days 列表删除
    new_days = [d for d in plan["days"] if d["day"] != day]
    if len(new_days) == len(plan["days"]):
        return plan, "未找到 Day {}".format(day)
    # 重新编号
    for i, d in enumerate(new_days):
        d["day"] = i + 1
    plan["days"] = new_days
    plan["meta"]["days"] = len(new_days)
    plan["intents"] = plan["intents"] + ["移除 Day {}".format(day)]
    return plan, "ok"


def apply_swap_days(plan, params):
    try:
        a, b = int(params.get("day_a", 0)), int(params.get("day_b", 0))
    except (TypeError, ValueError):
        return plan, "天数序号无效"
    days = plan["days"]
    if a == b or not 1 <= a <= len(days) or not 1 <= b <= len(days):
        return plan, "天数序号越界"
    ia, ib = a - 1, b - 1
    days[ia], days[ib] = days[ib], days[ia]
    days[ia]["day"], days[ib]["day"] = a, b
    plan["intents"] = plan["intents"] + ["交换 Day {} 与 Day {}".format(a, b)]
    return plan, "ok"


def apply_move_activity(plan, params):
    try:
        fd, ai, td = (int(params.get("from_day", 0)), int(params.get("activity_index", 0)),
                      int(params.get("to_day", 0)))
    except (TypeError, ValueError):
        return plan, "参数无效"
    days = plan["days"]
    if not 1 <= fd <= len(days) or not 1 <= td <= len(days):
        return plan, "天数序号越界"
    acts = days[fd - 1]["activities"]
    if not 0 <= ai < len(acts):
        return plan, "活动序号越界"
    act = acts.pop(ai)
    # 按时间插入目标天
    target = days[td - 1]["activities"]
    target.append(act)
    target.sort(key=lambda x: x.get("time", "00:00"))
    plan["intents"] = plan["intents"] + ["移动活动「{}」到 Day {}".format(act["name"], td)]
    return plan, "ok"


def apply_add_activity(plan, params):
    try:
        day = int(params.get("day", 0))
    except (TypeError, ValueError):
        return plan, "天数序号无效"
    name = (params.get("name") or "").strip()
    if not name:
        return plan, "活动名称不能为空"
    act_type = params.get("type") or "sight"
    if act_type not in ("sight", "food", "transport", "free"):
        act_type = "sight"
    days = plan["days"]
    if not 1 <= day <= len(days):
        return plan, "天数序号越界"
    loc = days[day - 1]["city"]
    new_act = {
        "time": "15:00", "type": act_type, "name": name,
        "location": loc, "lat": 0, "lng": 0, "duration_h": 1.5,
        "cost": 0, "transport": "步行", "note": "用户添加",
    }
    days[day - 1]["activities"].append(new_act)
    days[day - 1]["activities"].sort(key=lambda x: x.get("time", "00:00"))
    plan["intents"] = plan["intents"] + ["Day {} 添加活动「{}」".format(day, name)]
    return plan, "ok"


def apply_remove_activity(plan, params):
    try:
        day, ai = int(params.get("day", 0)), int(params.get("activity_index", 0))
    except (TypeError, ValueError):
        return plan, "参数无效"
    days = plan["days"]
    if not 1 <= day <= len(days):
        return plan, "天数序号越界"
    acts = days[day - 1]["activities"]
    if not 0 <= ai < len(acts):
        return plan, "活动序号越界"
    name = acts[ai]["name"]
    acts.pop(ai)
    plan["intents"] = plan["intents"] + ["移除 Day {} 活动「{}」".format(day, name)]
    return plan, "ok"


def apply_custom_note(plan, params):
    note = (params.get("note") or "").strip()
    if note:
        plan["intents"] = plan["intents"] + ["备注：{}".format(note)]
    return plan, "ok"


INTENT_HANDLERS = {
    "adjust_pace": apply_adjust_pace,
    "adjust_budget": apply_adjust_budget,
    "change_interests": apply_change_interests,
    "add_day": apply_add_day,
    "remove_day": apply_remove_day,
    "swap_days": apply_swap_days,
    "move_activity": apply_move_activity,
    "add_activity": apply_add_activity,
    "remove_activity": apply_remove_activity,
    "custom_note": apply_custom_note,
}


def _normalize_plan(plan):
    """补齐意图调整所需的结构字段（meta/days/intents、每天的 day/activities）。

    历史数据或分享导入的 plan 可能缺字段，直接下标取值会 KeyError → 接口 500。
    就地补齐（传进来的应是副本），返回 False 表示结构不可用。
    """
    if not isinstance(plan, dict):
        return False
    meta = plan.get("meta")
    days = plan.get("days")
    if not isinstance(meta, dict) or not isinstance(days, list):
        return False
    meta.setdefault("days", len(days))
    meta.setdefault("travelers", 2)
    meta.setdefault("total_budget", 0)
    meta.setdefault("interests", ["city", "culture"])
    meta.setdefault("pace", "normal")
    meta.setdefault("departure", "上海")
    meta.setdefault("start_date", "")
    meta.setdefault("title", None)
    if not isinstance(plan.get("intents"), list):
        plan["intents"] = []
    for i, d in enumerate(days):
        if not isinstance(d, dict):
            return False
        d.setdefault("day", i + 1)
        if not isinstance(d.get("activities"), list):
            d["activities"] = []
    return True


def apply_intent(plan, intent):
    """
    对行程执行一条意图指令。
    返回 (new_plan, msg)：
      - 成功：new_plan 为深拷贝后的新对象（不修改入参），msg == "ok"
      - 失败：原样返回入参 plan（调用方可用 `new_plan is plan` 判定失败）
    """
    import copy
    ok_flag, msg = validate_intent(intent)
    if not ok_flag:
        return plan, msg
    handler = INTENT_HANDLERS.get(intent["intent"])
    if not handler:
        return plan, "暂不支持的意图: {}".format(intent["intent"])
    new_plan = copy.deepcopy(plan)
    if not _normalize_plan(new_plan):
        return plan, "行程数据不完整（缺少 meta/days），无法调整"
    result_plan, msg = handler(new_plan, intent.get("params") or {})
    if msg != "ok":
        # 失败时返回原对象（而不是"未改动的副本"），保证调用方能识别出失败
        return plan, msg
    # 结构性重建（_regen_day）会丢掉 map_data（地图批注/路线），此处保留避免数据丢失
    if plan.get("map_data") and not result_plan.get("map_data"):
        result_plan["map_data"] = plan["map_data"]
    return result_plan, "ok"


def apply_intents(plan, intents):
    """顺序执行多条意图"""
    cur = plan
    msgs = []
    for it in intents:
        cur, msg = apply_intent(cur, it)
        msgs.append(msg)
    return cur, msgs


# ---------------------------------------------------------------------------
# 规则降级解析器（无 LLM 时用关键词理解自然语言）
# ---------------------------------------------------------------------------
def parse_intent_rules(text):
    """
    规则解析：从自然语言中提取意图。
    返回意图 dict 或 None（无法理解）。
    """
    if not text:
        return None
    t = text.strip().lower()

    # 节奏调整
    if any(k in t for k in ["轻松", "不要太累", "别太累", "慢", "relax"]):
        return {"intent": "adjust_pace", "params": {"pace": "relaxed"}, "raw": text}
    if any(k in t for k in ["紧凑", "更多景点", "密集", "intensive", "多安排"]):
        return {"intent": "adjust_pace", "params": {"pace": "intensive"}, "raw": text}

    # 预算调整
    m = re.search(r"(预算|花费|总预算|budget)(降低|减少|减|调低|调低)?(\d+)?%?", t)
    if m:
        # 检查百分比
        pct_m = re.search(r"[-+]?\d+", t.replace("降低", "-").replace("减少", "-")
                          .replace("增加", "+").replace("提高", "+"))
        if pct_m:
            try:
                delta = float(pct_m.group(0))
                return {"intent": "adjust_budget", "params": {"delta_pct": delta}, "raw": text}
            except (TypeError, ValueError):
                pass
    if any(k in t for k in ["预算降低", "少花", "便宜", "预算减少"]):
        return {"intent": "adjust_budget", "params": {"delta_pct": -20}, "raw": text}
    if any(k in t for k in ["预算增加", "多花", "预算提高"]):
        return {"intent": "adjust_budget", "params": {"delta_pct": 20}, "raw": text}

    # 兴趣调整
    if "兴趣" in t or "喜欢" in t or "偏好" in t:
        interests = []
        for alias, tag in INTEREST_ALIAS.items():
            if alias in t and tag not in interests:
                interests.append(tag)
        if interests:
            return {"intent": "change_interests", "params": {"interests": interests}, "raw": text}

    # 增加/减少天数
    if any(k in t for k in ["加一天", "多一天", "延长", "增加一天"]):
        return {"intent": "add_day", "params": {}, "raw": text}
    if any(k in t for k in ["减一天", "少一天", "缩短", "去掉一天", "删除一天"]):
        return {"intent": "remove_day", "params": {"day": 0}, "raw": text}

    # 无法理解 → None
    return None


def parse_intent(text, use_llm=True):
    """
    统一意图解析入口：
    - 若 use_llm 且已配置 LLM → 调用 LLM 解析
    - 否则 → 规则解析
    返回 (intent_or_None, provider_note)
    """
    if use_llm:
        try:
            import ai_planner
            if ai_planner.is_configured():
                intent = ai_planner.parse_intent_to_json(text)
                if intent and isinstance(intent, dict) and intent.get("intent"):
                    intent["raw"] = text
                    return intent, "llm"
        except Exception:
            pass  # LLM 解析失败则降级规则
    intent = parse_intent_rules(text)
    return intent, "rules"
