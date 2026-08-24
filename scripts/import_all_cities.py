# -*- coding: utf-8 -*-
"""
全量全球城市导入脚本
数据源: GeoNames cities15000 (https://download.geonames.org/export/dump/cities15000.zip)
  - 全球 1.5 万+ 主要城市 (人口 >= 15000)
  - 每条记录含: 英文名 / 中文名(从 alternatenames 提取) / 经纬度 / 国家代码
流程:
  1. 下载 cities15000.zip (缓存到 data/.geonames/)
  2. 解析并提取中英文名
  3. 与现有 data/cities.json 合并 (保留景点 sights / 首都标记)
  4. 写回 data/cities.json (全量)
  5. 增量导入数据库: UPDATE 现有行 + INSERT 新行 (不破坏已有收藏)
"""
import json
import os
import re
import sys
import urllib.request
import zipfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))          # 项目根
SERVER = os.path.join(BASE, "server")
DATA_DIR = os.path.join(SERVER, "data")
CITIES_JSON = os.path.join(DATA_DIR, "cities.json")
CACHE_DIR = os.path.join(DATA_DIR, ".geonames")
ZIP_PATH = os.path.join(CACHE_DIR, "cities15000.zip")
URL = "https://download.geonames.org/export/dump/cities15000.zip"

CJK = re.compile(r"[\u4e00-\u9fff]")

# =====================================================================
# 1. ISO 3166-1 alpha-2 国家代码 -> (中文名, 英文名)
# =====================================================================
ISO = {
    "AD": ("安道尔", "Andorra"), "AE": ("阿联酋", "United Arab Emirates"),
    "AF": ("阿富汗", "Afghanistan"), "AG": ("安提瓜和巴布达", "Antigua and Barbuda"),
    "AI": ("安圭拉", "Anguilla"), "AL": ("阿尔巴尼亚", "Albania"),
    "AM": ("亚美尼亚", "Armenia"), "AO": ("安哥拉", "Angola"),
    "AQ": ("南极洲", "Antarctica"), "AR": ("阿根廷", "Argentina"),
    "AS": ("美属萨摩亚", "American Samoa"), "AT": ("奥地利", "Austria"),
    "AU": ("澳大利亚", "Australia"), "AW": ("阿鲁巴", "Aruba"),
    "AX": ("奥兰群岛", "Aland Islands"), "AZ": ("阿塞拜疆", "Azerbaijan"),
    "BA": ("波黑", "Bosnia and Herzegovina"), "BB": ("巴巴多斯", "Barbados"),
    "BD": ("孟加拉国", "Bangladesh"), "BE": ("比利时", "Belgium"),
    "BF": ("布基纳法索", "Burkina Faso"), "BG": ("保加利亚", "Bulgaria"),
    "BH": ("巴林", "Bahrain"), "BI": ("布隆迪", "Burundi"),
    "BJ": ("贝宁", "Benin"), "BL": ("圣巴泰勒米", "Saint Barthelemy"),
    "BM": ("百慕大", "Bermuda"), "BN": ("文莱", "Brunei"),
    "BO": ("玻利维亚", "Bolivia"), "BQ": ("荷兰加勒比区", "Caribbean Netherlands"),
    "BR": ("巴西", "Brazil"), "BS": ("巴哈马", "Bahamas"),
    "BT": ("不丹", "Bhutan"), "BV": ("布韦岛", "Bouvet Island"),
    "BW": ("博茨瓦纳", "Botswana"), "BY": ("白俄罗斯", "Belarus"),
    "BZ": ("伯利兹", "Belize"), "CA": ("加拿大", "Canada"),
    "CC": ("科科斯群岛", "Cocos Islands"), "CD": ("刚果（金）", "DR Congo"),
    "CF": ("中非共和国", "Central African Republic"), "CG": ("刚果（布）", "Congo"),
    "CH": ("瑞士", "Switzerland"), "CI": ("科特迪瓦", "Ivory Coast"),
    "CK": ("库克群岛", "Cook Islands"), "CL": ("智利", "Chile"),
    "CM": ("喀麦隆", "Cameroon"), "CN": ("中国", "China"),
    "CO": ("哥伦比亚", "Colombia"), "CR": ("哥斯达黎加", "Costa Rica"),
    "CU": ("古巴", "Cuba"), "CV": ("佛得角", "Cape Verde"),
    "CW": ("库拉索", "Curacao"), "CX": ("圣诞岛", "Christmas Island"),
    "CY": ("塞浦路斯", "Cyprus"), "CZ": ("捷克", "Czechia"),
    "DE": ("德国", "Germany"), "DJ": ("吉布提", "Djibouti"),
    "DK": ("丹麦", "Denmark"), "DM": ("多米尼克", "Dominica"),
    "DO": ("多米尼加", "Dominican Republic"), "DZ": ("阿尔及利亚", "Algeria"),
    "EC": ("厄瓜多尔", "Ecuador"), "EE": ("爱沙尼亚", "Estonia"),
    "EG": ("埃及", "Egypt"), "EH": ("西撒哈拉", "Western Sahara"),
    "ER": ("厄立特里亚", "Eritrea"), "ES": ("西班牙", "Spain"),
    "ET": ("埃塞俄比亚", "Ethiopia"), "FI": ("芬兰", "Finland"),
    "FJ": ("斐济", "Fiji"), "FK": ("福克兰群岛", "Falkland Islands"),
    "FM": ("密克罗尼西亚", "Micronesia"), "FO": ("法罗群岛", "Faroe Islands"),
    "FR": ("法国", "France"), "GA": ("加蓬", "Gabon"),
    "GB": ("英国", "United Kingdom"), "GD": ("格林纳达", "Grenada"),
    "GE": ("格鲁吉亚", "Georgia"), "GF": ("法属圭亚那", "French Guiana"),
    "GG": ("根西岛", "Guernsey"), "GH": ("加纳", "Ghana"),
    "GI": ("直布罗陀", "Gibraltar"), "GL": ("格陵兰", "Greenland"),
    "GM": ("冈比亚", "Gambia"), "GN": ("几内亚", "Guinea"),
    "GP": ("瓜德罗普", "Guadeloupe"), "GQ": ("赤道几内亚", "Equatorial Guinea"),
    "GR": ("希腊", "Greece"), "GS": ("南乔治亚和南桑威奇群岛", "South Georgia"),
    "GT": ("危地马拉", "Guatemala"), "GU": ("关岛", "Guam"),
    "GW": ("几内亚比绍", "Guinea-Bissau"), "GY": ("圭亚那", "Guyana"),
    "HK": ("中国香港", "Hong Kong"), "HM": ("赫德岛和麦克唐纳群岛", "Heard Island"),
    "HN": ("洪都拉斯", "Honduras"), "HR": ("克罗地亚", "Croatia"),
    "HT": ("海地", "Haiti"), "HU": ("匈牙利", "Hungary"),
    "ID": ("印度尼西亚", "Indonesia"), "IE": ("爱尔兰", "Ireland"),
    "IL": ("以色列", "Israel"), "IM": ("马恩岛", "Isle of Man"),
    "IN": ("印度", "India"), "IO": ("英属印度洋领地", "British Indian Ocean Territory"),
    "IQ": ("伊拉克", "Iraq"), "IR": ("伊朗", "Iran"),
    "IS": ("冰岛", "Iceland"), "IT": ("意大利", "Italy"),
    "JE": ("泽西岛", "Jersey"), "JM": ("牙买加", "Jamaica"),
    "JO": ("约旦", "Jordan"), "JP": ("日本", "Japan"),
    "KE": ("肯尼亚", "Kenya"), "KG": ("吉尔吉斯斯坦", "Kyrgyzstan"),
    "KH": ("柬埔寨", "Cambodia"), "KI": ("基里巴斯", "Kiribati"),
    "KM": ("科摩罗", "Comoros"), "KN": ("圣基茨和尼维斯", "Saint Kitts and Nevis"),
    "KP": ("朝鲜", "North Korea"), "KR": ("韩国", "South Korea"),
    "KW": ("科威特", "Kuwait"), "KY": ("开曼群岛", "Cayman Islands"),
    "KZ": ("哈萨克斯坦", "Kazakhstan"), "LA": ("老挝", "Laos"),
    "LB": ("黎巴嫩", "Lebanon"), "LC": ("圣卢西亚", "Saint Lucia"),
    "LI": ("列支敦士登", "Liechtenstein"), "LK": ("斯里兰卡", "Sri Lanka"),
    "LR": ("利比里亚", "Liberia"), "LS": ("莱索托", "Lesotho"),
    "LT": ("立陶宛", "Lithuania"), "LU": ("卢森堡", "Luxembourg"),
    "LV": ("拉脱维亚", "Latvia"), "LY": ("利比亚", "Libya"),
    "MA": ("摩洛哥", "Morocco"), "MC": ("摩纳哥", "Monaco"),
    "MD": ("摩尔多瓦", "Moldova"), "ME": ("黑山", "Montenegro"),
    "MF": ("法属圣马丁", "Saint Martin"), "MG": ("马达加斯加", "Madagascar"),
    "MH": ("马绍尔群岛", "Marshall Islands"), "MK": ("北马其顿", "North Macedonia"),
    "ML": ("马里", "Mali"), "MM": ("缅甸", "Myanmar"),
    "MN": ("蒙古", "Mongolia"), "MO": ("中国澳门", "Macao"),
    "MP": ("北马里亚纳群岛", "Northern Mariana Islands"), "MQ": ("马提尼克", "Martinique"),
    "MR": ("毛里塔尼亚", "Mauritania"), "MS": ("蒙特塞拉特", "Montserrat"),
    "MT": ("马耳他", "Malta"), "MU": ("毛里求斯", "Mauritius"),
    "MV": ("马尔代夫", "Maldives"), "MW": ("马拉维", "Malawi"),
    "MX": ("墨西哥", "Mexico"), "MY": ("马来西亚", "Malaysia"),
    "MZ": ("莫桑比克", "Mozambique"), "NA": ("纳米比亚", "Namibia"),
    "NC": ("新喀里多尼亚", "New Caledonia"), "NE": ("尼日尔", "Niger"),
    "NF": ("诺福克岛", "Norfolk Island"), "NG": ("尼日利亚", "Nigeria"),
    "NI": ("尼加拉瓜", "Nicaragua"), "NL": ("荷兰", "Netherlands"),
    "NO": ("挪威", "Norway"), "NP": ("尼泊尔", "Nepal"),
    "NR": ("瑙鲁", "Nauru"), "NU": ("纽埃", "Niue"),
    "NZ": ("新西兰", "New Zealand"), "OM": ("阿曼", "Oman"),
    "PA": ("巴拿马", "Panama"), "PE": ("秘鲁", "Peru"),
    "PF": ("法属波利尼西亚", "French Polynesia"), "PG": ("巴布亚新几内亚", "Papua New Guinea"),
    "PH": ("菲律宾", "Philippines"), "PK": ("巴基斯坦", "Pakistan"),
    "PL": ("波兰", "Poland"), "PM": ("圣皮埃尔和密克隆", "Saint Pierre and Miquelon"),
    "PN": ("皮特凯恩群岛", "Pitcairn Islands"), "PR": ("波多黎各", "Puerto Rico"),
    "PS": ("巴勒斯坦", "Palestine"), "PT": ("葡萄牙", "Portugal"),
    "PW": ("帕劳", "Palau"), "PY": ("巴拉圭", "Paraguay"),
    "QA": ("卡塔尔", "Qatar"), "RE": ("留尼汪", "Reunion"),
    "RO": ("罗马尼亚", "Romania"), "RS": ("塞尔维亚", "Serbia"),
    "RU": ("俄罗斯", "Russia"), "RW": ("卢旺达", "Rwanda"),
    "SA": ("沙特阿拉伯", "Saudi Arabia"), "SB": ("所罗门群岛", "Solomon Islands"),
    "SC": ("塞舌尔", "Seychelles"), "SD": ("苏丹", "Sudan"),
    "SE": ("瑞典", "Sweden"), "SG": ("新加坡", "Singapore"),
    "SH": ("圣赫勒拿", "Saint Helena"), "SI": ("斯洛文尼亚", "Slovenia"),
    "SJ": ("斯瓦尔巴和扬马延", "Svalbard and Jan Mayen"), "SK": ("斯洛伐克", "Slovakia"),
    "SL": ("塞拉利昂", "Sierra Leone"), "SM": ("圣马力诺", "San Marino"),
    "SN": ("塞内加尔", "Senegal"), "SO": ("索马里", "Somalia"),
    "SR": ("苏里南", "Suriname"), "SS": ("南苏丹", "South Sudan"),
    "ST": ("圣多美和普林西比", "Sao Tome and Principe"), "SV": ("萨尔瓦多", "El Salvador"),
    "SX": ("荷属圣马丁", "Sint Maarten"), "SY": ("叙利亚", "Syria"),
    "SZ": ("斯威士兰", "Eswatini"), "TC": ("特克斯和凯科斯群岛", "Turks and Caicos Islands"),
    "TD": ("乍得", "Chad"), "TF": ("法属南部领地", "French Southern Territories"),
    "TG": ("多哥", "Togo"), "TH": ("泰国", "Thailand"),
    "TJ": ("塔吉克斯坦", "Tajikistan"), "TK": ("托克劳", "Tokelau"),
    "TL": ("东帝汶", "Timor-Leste"), "TM": ("土库曼斯坦", "Turkmenistan"),
    "TN": ("突尼斯", "Tunisia"), "TO": ("汤加", "Tonga"),
    "TR": ("土耳其", "Turkey"), "TT": ("特立尼达和多巴哥", "Trinidad and Tobago"),
    "TV": ("图瓦卢", "Tuvalu"), "TW": ("中国台湾", "Taiwan"),
    "TZ": ("坦桑尼亚", "Tanzania"), "UA": ("乌克兰", "Ukraine"),
    "UG": ("乌干达", "Uganda"), "UM": ("美国本土外小岛屿", "U.S. Minor Outlying Islands"),
    "US": ("美国", "United States"), "UY": ("乌拉圭", "Uruguay"),
    "UZ": ("乌兹别克斯坦", "Uzbekistan"), "VA": ("梵蒂冈", "Vatican"),
    "VC": ("圣文森特和格林纳丁斯", "Saint Vincent and the Grenadines"),
    "VE": ("委内瑞拉", "Venezuela"), "VG": ("英属维尔京群岛", "British Virgin Islands"),
    "VI": ("美属维尔京群岛", "U.S. Virgin Islands"), "VN": ("越南", "Vietnam"),
    "VU": ("瓦努阿图", "Vanuatu"), "WF": ("瓦利斯和富图纳", "Wallis and Futuna"),
    "WS": ("萨摩亚", "Samoa"), "XK": ("科索沃", "Kosovo"),
    "YE": ("也门", "Yemen"), "YT": ("马约特", "Mayotte"),
    "ZA": ("南非", "South Africa"), "ZM": ("赞比亚", "Zambia"),
    "ZW": ("津巴布韦", "Zimbabwe"),
}

# 简体/繁体常见字（用于优先选择简体名）
TRAD_ONLY = set("灣縣島區廈門臺東廣州馬來西亞裡爾蘭克福紐約洛杉磯東京橫浜神戸広島長崎函館韓國臺灣澳門臺北香港")
# 常见繁体字集合（简单扩充）
TRAD_CHARS = set("灣縣島門臺東廣馬來西亞裡紐約洛杉磯橫浜神戸広島長崎韓臺灣澳門臺北極後徑徑國勝實學術內外科時書萬園輪")


def extract_zh(altnames: str) -> str:
    """从 alternatenames 中提取中文名，优先简体"""
    if not altnames:
        return ""
    zh_names = [p.strip() for p in altnames.split(",") if CJK.search(p)]
    if not zh_names:
        return ""
    # 去重且按长度排序（短的通常是常用名）
    seen, unique = set(), []
    for n in zh_names:
        n = n.strip()
        if n and n not in seen:
            seen.add(n)
            unique.append(n)
    unique.sort(key=lambda s: (sum(1 for ch in s if ch in TRAD_CHARS), len(s)))
    return unique[0]


def download():
    """下载 GeoNames 数据（带缓存）"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    if os.path.exists(ZIP_PATH) and os.path.getsize(ZIP_PATH) > 3_000_000:
        print("[skip] 已存在缓存:", ZIP_PATH)
        return ZIP_PATH
    print("[download]", URL)
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0 (travel-planner)"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(ZIP_PATH, "wb") as f:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    print("[ok] 下载完成:", ZIP_PATH)
    return ZIP_PATH


def parse_cities():
    """解析 cities15000.txt -> list[dict]"""
    zip_path = download()
    result = []
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        txt = next((n for n in names if n.endswith(".txt")), None)
        if txt is None:
            raise RuntimeError("zip 内未找到 txt 数据文件: " + ",".join(names))
        raw = z.read(txt).decode("utf-8", errors="ignore")
    for line in raw.splitlines():
        if not line.strip():
            continue
        f = line.split("\t")
        if len(f) < 19:
            continue
        geonameid, name, asciiname, altnames = f[0], f[1], f[2], f[3]
        lat, lng = float(f[4]), float(f[5])
        country_code = f[8]
        feature_code = f[7]
        population = int(f[14]) if f[14].isdigit() else 0
        zh = extract_zh(altnames)
        result.append({
            "geonameid": geonameid,
            "name": name,           # 英文名
            "asciiname": asciiname,
            "zh": zh,
            "lat": lat, "lng": lng,
            "country_code": country_code,
            "feature_code": feature_code,
            "population": population,
        })
    return result


def load_existing():
    """读取现有 cities.json -> dict[(name), dict] 保留景点"""
    if not os.path.exists(CITIES_JSON):
        return {}, []
    with open(CITIES_JSON, encoding="utf-8") as f:
        arr = json.load(f)
    by_zh = {}
    by_en = {}
    for c in arr:
        n = (c.get("name") or "").strip()
        ne = (c.get("name_en") or "").strip().lower()
        if n:
            by_zh[n] = c
        if ne:
            by_en[ne] = c
    return by_zh, by_en, arr


def main():
    sys.path.insert(0, SERVER)
    from database import get_conn

    print("=" * 60)
    print("步骤1: 下载并解析 GeoNames 城市数据")
    cities = parse_cities()
    print(f"  GeoNames 城市: {len(cities)}")

    print("步骤2: 合并现有城市数据 (保留景点/首都标记)")
    by_zh, by_en, _ = load_existing()
    merged, seen = [], set()
    keep_count = 0
    for c in cities:
        zh = c["zh"]
        name = zh if zh else c["name"]       # 中文名优先做 name
        name_en = c["name"]
        # 查找现有数据合并
        old = by_zh.get(name) or (by_en.get(name_en.lower()) if name_en else None)
        sights = old.get("sights", []) if old else []
        is_capital = 1 if c["feature_code"] == "PPLC" else 0
        if old:
            is_capital = old.get("is_capital", is_capital)
            keep_count += 1
        code = c["country_code"]
        czh, cen = ISO.get(code, ("", code))
        # 兜底: 使用现有数据中的国家名
        if old and (old.get("country_zh") or old.get("country_en")):
            czh = old.get("country_zh") or czh
            cen = old.get("country_en") or cen
        if not czh:
            czh = name_en if not zh else ""
        key = (name, name_en, code, round(c["lat"], 4), round(c["lng"], 4))
        if key in seen:
            continue
        seen.add(key)
        merged.append({
            "country_code": code,
            "country_zh": czh,
            "country_en": cen,
            "name": name,
            "name_en": name_en,
            "name_local": c["asciiname"] if c["asciiname"] != name_en else "",
            "lat": round(c["lat"], 4),
            "lng": round(c["lng"], 4),
            "sights": sights,
            "is_capital": is_capital,
            "population": c["population"],
        })
    # 补: 现有城市若不在 GeoNames 中（如新加的日本城市），也要保留
    existing_kept = 0
    for old in load_existing()[2]:
        key = (old.get("name"), old.get("name_en"), old.get("country_code"), round(float(old.get("lat", 0)), 4), round(float(old.get("lng", 0)), 4))
        if key in seen:
            continue
        seen.add(key)
        old = dict(old)
        old.setdefault("population", 0)
        old.setdefault("is_capital", 0)
        merged.append(old)
        existing_kept += 1
    print(f"  保留现有景点城市: {keep_count}, 额外保留现有城市: {existing_kept}")
    print(f"  最终城市总数: {len(merged)}")

    print("步骤3: 写回 cities.json")
    merged.sort(key=lambda c: (c["country_code"], c["name"]))
    with open(CITIES_JSON, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=1)
    print(f"  [ok] {CITIES_JSON}")

    print("步骤4: 增量导入数据库 (UPDATE 现有 + INSERT 新)")
    conn = get_conn()
    try:
        rows = conn.execute("SELECT id, name, name_en FROM cities").fetchall()
        db_by_key = {}
        for r in rows:
            n, ne = r["name"], (r["name_en"] or "").lower()
            if n:
                db_by_key[n] = r["id"]
            if ne:
                db_by_key.setdefault(ne, r["id"])
        update_sql = ("UPDATE cities SET country_code=?,country_zh=?,country_en=?,name=?,"
                      "name_en=?,name_local=?,lat=?,lng=?,sights=?,is_capital=? WHERE id=?")
        insert_sql = ("INSERT INTO cities (country_code,country_zh,country_en,name,name_en,"
                      "name_local,lat,lng,sights,is_capital) VALUES (?,?,?,?,?,?,?,?,?,?)")
        upd, ins = [], []
        for c in merged:
            pid = db_by_key.get(c["name"]) or db_by_key.get((c["name_en"] or "").lower())
            vals = (c["country_code"], c["country_zh"], c["country_en"], c["name"],
                    c["name_en"], c["name_local"], c["lat"], c["lng"],
                    json.dumps(c.get("sights", []), ensure_ascii=False), c.get("is_capital", 0))
            if pid:
                upd.append(vals + (pid,))
            else:
                ins.append(vals)
        if upd:
            conn.executemany(update_sql, upd)
        if ins:
            conn.executemany(insert_sql, ins)
        conn.commit()
        total = conn.execute("SELECT COUNT(*) AS c FROM cities").fetchone()
        print(f"  UPDATE {len(upd)} 行, INSERT {len(ins)} 行")
        print(f"  数据库城市总数: {total['c']}")
    finally:
        conn.close()
    print("=" * 60)
    print("完成！")


if __name__ == "__main__":
    main()
