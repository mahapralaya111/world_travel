# -*- coding: utf-8 -*-
"""
AI 智能出行规划 - 大模型调用封装
================================
通过 OpenAI 兼容的 Chat Completions 接口调用大模型，
支持 DeepSeek / 通义千问 / 智谱 GLM / OpenAI 等厂商（在 config 中切换）。

未配置 API Key 时，模块会返回友好提示，不影响其他功能运行。

依赖：Python 标准库 urllib（无需安装第三方 SDK）
"""
import json
import os
import re
import urllib.request
import urllib.error

from config import Config

# ---------------------------------------------------------------------------
# 厂商预置配置（base_url 均为 OpenAI 兼容接口根地址，不含 /v1）
# ---------------------------------------------------------------------------
PROVIDERS = {
    # ============ 本地部署（零成本、离线） ============
    "ollama": {
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:7b",
        "chat_path": "/chat/completions",
        "need_key": False,
    },

    # ============ 云端 API（需要 Key） ============
    # 默认推荐：DeepSeek，国内可访问、中文强、价格低（兼容 OpenAI 格式）
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "chat_path": "/chat/completions",
        "need_key": True,
    },
    # 阿里通义千问（DashScope 兼容模式）
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "chat_path": "/chat/completions",
        "need_key": True,
    },
    # 智谱 GLM（glm-4-flash 免费额度）
    "glm": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "chat_path": "/chat/completions",
        "need_key": True,
    },
    # 标准 OpenAI
    "openai": {
        "base_url": "https://api.openai.com",
        "model": "gpt-4o-mini",
        "chat_path": "/v1/chat/completions",
        "need_key": True,
    },
}


def _resolve_provider():
    """解析当前生效的厂商配置"""
    name = (Config.AI_PROVIDER or "ollama").lower()
    prov = dict(PROVIDERS.get(name, PROVIDERS["ollama"]))
    # 允许环境变量覆盖 base_url / model（无需改代码）
    if Config.AI_BASE_URL:
        prov["base_url"] = Config.AI_BASE_URL.rstrip("/")
    if Config.AI_MODEL:
        prov["model"] = Config.AI_MODEL
    return name, prov


def is_configured() -> bool:
    """是否已配置大模型（本地 ollama 默认可用；云端需要 API Key）"""
    name = (Config.AI_PROVIDER or "ollama").lower()
    prov = PROVIDERS.get(name, PROVIDERS["ollama"])
    if not prov.get("need_key", False):
        return True
    key = (Config.AI_API_KEY or "").strip()
    return bool(key)


def _chat_once(messages, temperature=0.7, max_tokens=4096, timeout=180):
    """调用一次 Chat Completions，返回模型回复文本"""
    name, prov = _resolve_provider()
    need_key = prov.get("need_key", False)
    key = (Config.AI_API_KEY or "").strip()

    if need_key and not key:
        raise RuntimeError("未配置大模型 API Key")

    url = prov["base_url"] + prov["chat_path"]
    payload = {
        "model": prov["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    # 部分厂商支持 JSON 输出约束（本地小模型可能不支持，跳过以免报错）
    try:
        if name in ("deepseek", "openai", "qwen", "glm"):
            payload["response_format"] = {"type": "json_object"}
    except Exception:
        pass

    headers = {"Content-Type": "application/json"}
    if need_key and key:
        headers["Authorization"] = "Bearer " + key

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")[:300]
        raise RuntimeError(f"大模型接口错误 HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"无法连接大模型服务 {prov['base_url']}（provider={name}）。"
            f"若使用本地 Ollama，请先执行 'ollama serve' 启动服务。错误: {e.reason}"
        ) from e

    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("大模型返回格式异常: " + json.dumps(body, ensure_ascii=False)[:300])


def _extract_json(text: str):
    """从模型输出中稳健提取 JSON 对象（兼容 ```json 围栏、前后多余说明、多余逗号）"""
    text = (text or "").strip()
    # 去掉代码块围栏
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        # 容错重试：去掉对象/数组结尾多余逗号（模型常见输出）
        cleaned = re.sub(r",\s*([}\]])", r"\1", text)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            raise RuntimeError("大模型返回内容不是合法 JSON: %s" % e)


# ---------------------------------------------------------------------------
# 业务入口：生成出行规划
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "你是一位资深的全球旅行规划专家，擅长根据用户的出行时间、游玩天数、预算与偏好，"
    "推荐合适的目的地、交通方式和住宿方案。你的建议要真实、可执行、贴合当地实际情况，"
    "注意淡旺季、签证、汇率和性价比。所有金额均以人民币(CNY)为单位。"
    "你只输出 JSON，不要输出任何解释性文字。"
    "重要原则：行程中所有景点不可重复出现；所有金额必须符合当地真实物价，绝不为凑预算而给出脱离实际的价格。"
)

USER_TEMPLATE = """请根据以下信息为用户生成一份完整的出行规划：

【用户需求】
- 出行时间：{month}（{season}）
- 游玩天数：{days} 天
- 总预算：{budget} 元（人民币）
- 出行人数：{travelers} 人
- 出发城市：{departure}（请据此给出合理的交通建议与大致时长/价格）
- 旅行偏好：{preferences}

【输出要求】
请严格输出如下 JSON 结构（不要带 markdown 代码块）：
{{
  "summary": "一句话总结这份规划",
  "destinations": [
    {{
      "name": "目的地城市名（中文）",
      "country": "国家（中文）",
      "days": 建议停留天数,
      "budget_share": "占总预算比例，如 40%",
      "why": "推荐理由（结合出行月份/预算/偏好，1-2 句）",
      "season_tip": "该月份出行的气候/旺季提醒"
    }}
  ],
  "transport": {{
    "way": "整体推荐交通方式",
    "details": ["具体建议1", "具体建议2", "具体建议3"],
    "cost_estimate": "往返交通费用估算",
    "tips": "交通小贴士（如提前购票、当地交通卡）"
  }},
  "accommodation": {{
    "style": "推荐的住宿类型与区域",
    "details": ["住宿建议1", "住宿建议2"],
    "cost_estimate": "每晚预算估算",
    "tips": "住宿小贴士"
  }},
  "itinerary": [
    {{"day": 1, "title": "Day 1 主题", "places": ["地点/活动1", "地点/活动2", "地点/活动3"]}}
  ],
  "food": {{"special": "当地必尝美食", "cost_estimate": "每日餐饮预算", "tips": "美食小贴士"}},
  "budget_breakdown": {{"交通": "xx元", "住宿": "xx元", "餐饮": "xx元", "门票/活动": "xx元", "其他": "xx元"}},
  "tips": ["实用提醒1（签证/货币/语言等）", "实用提醒2", "实用提醒3"]
}}

【硬性约束 - 必须严格遵守】
1. 目的地给出 {days} 天合理可行的方案，destinations 中各城市 days 之和必须等于 {days}。
2. 单个城市停留天数一般不超过 4 天。若总天数 ≥ 7 天，必须推荐多城市组合（如日本可组合 东京+大阪+京都+奈良；不可让单一城市承担 5 天以上），避免景点枯竭后被迫重复。
3. ⚠️ 景点不重复：itinerary 中所有景点/活动在整个行程中只能出现一次（同一景点名不能在 Day1 和 Day3 重复出现）。
   若某城市景点不足以覆盖分配天数，必须采用以下方式之一扩展，绝不可简单重复同一景点：
   - 推荐该城市周边小镇/近郊一日游（例：东京→箱根/日光/镰仓/富士山河口湖；巴黎→凡尔赛/枫丹白露/吉维尼；伦敦→牛津/剑桥/巴斯）
   - 安排购物街/特色街区漫步/自由活动（例：银座、新宿、原宿、涩谷分别算不同行程）
   - 列出该城市更多冷门但真实的景点（博物馆、公园、神社、市场等）
4. 预算拆分 budget_breakdown 各项目合计应约等于总预算 {budget} 元。
5. 结合 {month} 月份的当地气候与旅游淡旺季给出真实建议。
6. 所有金额单位为人民币（CNY）。

【价格底线 - 不可低于当地真实物价】
所有费用估算必须基于当地实际物价，绝不可低于以下底线（按每人计算）：
- 餐饮：便利店便当/快餐最低 15 元/顿；普通正餐最低 30 元/顿；特色正餐 60+ 元/顿
- 住宿：青旅床位最低 80 元/晚；经济型酒店最低 200 元/晚；标准酒店最低 400 元/晚
- 市内交通：地铁/公交最低 5 元/次；出租车起步价最低 10 元
- 景点门票：参考实际票价（例：东京晴空塔组合券约 200 元；东京迪士尼 600+ 元；京都清水寺 30 元；卢浮宫 100 元）
- 跨城市交通：新干线/高铁按实际票价（例：东京-大阪新干线 700+ 元；欧洲城际列车 200-500 元）

【预算不足的处理】
如果总预算 {budget} 元明显不足以覆盖 {days} 天的合理花费（参考每人每天基础花费：日本 500-800 元，欧洲 800-1200 元，东南亚 300-500 元，国内 200-400 元），请：
1. 在 summary 中明确提示"⚠️ 当前预算偏紧，建议提高至约 XXX 元/人以获得更优体验"
2. budget_breakdown 仍按上述真实底线分配（即使总和超过 {budget} 也必须给真实数字）
3. 在 tips 中说明节省方案（如住青旅、便利店用餐、买周游券等）
绝不可为了凑预算而给出 3 毛、5 毛这种明显脱离实际的价格；宁可在 summary 中提示预算不足，也不能编造低价。"""


INTENT_SYSTEM_PROMPT = (
    "你是一个行程调整指令解析器。用户会给出对已有旅行行程的自然语言调整要求，"
    "你必须把它解析成一条结构化的意图指令 JSON，只输出 JSON，不要输出解释。"
    "可选意图类型及参数如下：\n"
    "- adjust_pace: 调整节奏。params.pace ∈ {relaxed, normal, intensive}。如「第三天别太累」→ {intent:'adjust_pace', params:{pace:'relaxed'}}\n"
    "- adjust_budget: 调整总预算。params.delta_pct 为百分比数字，降低用负数，如「预算降低20%」→ {intent:'adjust_budget', params:{delta_pct:-20}}\n"
    "- change_interests: 更改兴趣。params.interests 为标签数组，可选: culture, history, nature, food, shopping, city\n"
    "- add_day: 增加一天行程。params 为空\n"
    "- remove_day: 移除某一天。params.day 为天数序号（1 开始）\n"
    "- swap_days: 交换两天。params.day_a, params.day_b 为天数序号\n"
    "- move_activity: 移动活动。params.from_day, params.activity_index(0开始), params.to_day\n"
    "- add_activity: 添加活动。params.day, params.name, params.type ∈ {sight,food,transport,free}\n"
    "- remove_activity: 移除活动。params.day, params.activity_index(0开始)\n"
    "如果用户的意图不明确或无法映射到以上类型，输出 {\"intent\":\"custom_note\",\"params\":{\"note\":\"原始文字\"}}。"
)


def parse_intent_to_json(user_text):
    """
    将用户自然语言调整要求解析为结构化意图 JSON。
    未配置 Key 时抛出 RuntimeError。
    """
    if not is_configured():
        raise RuntimeError("未配置大模型 API Key")
    messages = [
        {"role": "system", "content": INTENT_SYSTEM_PROMPT},
        {"role": "user", "content": str(user_text)[:800]},
    ]
    text = _chat_once(messages, temperature=0.2, max_tokens=500)
    data = _extract_json(text)
    if not isinstance(data, dict) or "intent" not in data:
        raise RuntimeError("意图解析失败：未返回有效 intent")
    return data


def _normalize_place(name):
    """归一化景点名用于去重比较（去空格/括号注释/前缀编号，转小写）"""
    s = str(name or "").strip()
    # 去掉前缀编号如 "1. " "1、"
    import re
    s = re.sub(r"^[\d]+[.、\)）]?\s*", "", s)
    # 去掉括号及内部注释
    s = re.sub(r"[\(（][^\)）]*[\)）]", "", s)
    return s.lower().replace(" ", "").replace("　", "")


def _dedupe_itinerary(data):
    """
    后处理：对 itinerary 中跨天重复的景点去重。
    同一景点只保留首次出现，后续重复项替换为"自由活动/周边探索"占位。
    返回 (data, removed_count)。
    """
    if not isinstance(data, dict):
        return data, 0
    itin = data.get("itinerary")
    if not isinstance(itin, list) or not itin:
        return data, 0
    seen = set()
    removed = 0
    for entry in itin:
        if not isinstance(entry, dict):
            continue
        places = entry.get("places")
        if not isinstance(places, list):
            continue
        new_places = []
        for p in places:
            key = _normalize_place(p)
            if not key:
                new_places.append(p)
                continue
            if key in seen:
                removed += 1
                # 替换为占位，避免空缺
                new_places.append("自由活动/周边探索（替换重复景点）")
            else:
                seen.add(key)
                new_places.append(p)
        entry["places"] = new_places
    return data, removed


def _check_city_overstay(data, days):
    """
    后处理：检查是否有城市停留天数 > 4 且总天数 >= 7。
    返回提示字符串（若无问题返回 None）。
    """
    if not isinstance(data, dict):
        return None
    dests = data.get("destinations")
    if not isinstance(dests, list):
        return None
    over = []
    for d in dests:
        if not isinstance(d, dict):
            continue
        try:
            dd = int(d.get("days", 0))
        except (TypeError, ValueError):
            continue
        if dd > 4 and days >= 7:
            over.append(f"{d.get('name', '?')}停留{dd}天")
    if over:
        return "⚠️ 行程提醒：" + "、".join(over) + "，建议分散至周边城市避免景点重复"
    return None


def generate_travel_plan(days=7, budget=10000, month="6月", travelers=2,
                         departure="上海", preferences=""):
    """
    生成出行规划（结构化 JSON）
    返回: (data_dict, provider_name)
    未配置 Key 时抛出 RuntimeError("未配置大模型 API Key")
    """
    if not is_configured():
        raise RuntimeError("未配置大模型 API Key")

    season = "冬季" if month in ("12月", "1月", "2月") else \
             "春季" if month in ("3月", "4月", "5月") else \
             "夏季" if month in ("6月", "7月", "8月") else "秋季"
    pref = preferences.strip() or "无特别偏好，希望获得均衡的经典行程"
    month_t = str(month) + "月" if str(month).isdigit() else str(month)

    user_msg = USER_TEMPLATE.format(
        month=month_t, season=season, days=days, budget=budget,
        travelers=travelers, departure=departure, preferences=pref,
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    text = _chat_once(messages)
    data = _extract_json(text)

    # 简单校验
    if "destinations" not in data:
        raise RuntimeError("大模型未返回有效目的地数据")

    # 后处理：去重景点（即使 prompt 已约束，LLM 仍可能偶尔重复，作为安全网）
    data, removed = _dedupe_itinerary(data)
    if removed:
        tips = data.get("tips")
        if isinstance(tips, list):
            tips.append(f"已自动去除 {removed} 处重复景点并替换为自由活动时段。")

    # 后处理：城市停留过多提示
    over_tip = _check_city_overstay(data, days)
    if over_tip:
        summary = str(data.get("summary", ""))
        data["summary"] = (summary + " " + over_tip).strip()

    return data
