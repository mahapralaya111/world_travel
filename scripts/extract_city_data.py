# -*- coding: utf-8 -*-
"""
从 index.html 提取 CITY_DATA / CITY_NAMES / countryNameMap，
生成后端 server/data/cities.json（用于城市表入库与多语言搜索）。

用法: python scripts/extract_city_data.py
"""
import json
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_HTML = os.path.join(BASE, "index.html")
OUT_JSON = os.path.join(BASE, "server", "data", "cities.json")

# ISO 3166-1 numeric -> 英文国家名（覆盖主要旅游国家，缺失项可在列表中补充）
ISO_NUMERIC_TO_EN = {
    "004": "Afghanistan", "008": "Albania", "012": "Algeria", "020": "Andorra",
    "024": "Angola", "028": "Antigua and Barbuda", "032": "Argentina", "051": "Armenia",
    "036": "Australia", "040": "Austria", "031": "Azerbaijan", "044": "Bahamas",
    "048": "Bahrain", "050": "Bangladesh", "052": "Barbados", "112": "Belarus",
    "056": "Belgium", "084": "Belize", "204": "Benin", "064": "Bhutan",
    "068": "Bolivia", "070": "Bosnia and Herzegovina", "072": "Botswana",
    "076": "Brazil", "096": "Brunei", "100": "Bulgaria", "854": "Burkina Faso",
    "108": "Burundi", "116": "Cambodia", "120": "Cameroon", "124": "Canada",
    "132": "Cape Verde", "140": "Central African Republic", "148": "Chad",
    "152": "Chile", "156": "China", "170": "Colombia", "174": "Comoros",
    "178": "Congo", "180": "Democratic Republic of the Congo", "188": "Costa Rica",
    "384": "Ivory Coast", "191": "Croatia", "192": "Cuba", "196": "Cyprus",
    "203": "Czech Republic", "208": "Denmark", "262": "Djibouti", "212": "Dominica",
    "214": "Dominican Republic", "218": "Ecuador", "818": "Egypt", "222": "El Salvador",
    "226": "Equatorial Guinea", "232": "Eritrea", "233": "Estonia", "231": "Ethiopia",
    "242": "Fiji", "246": "Finland", "250": "France", "266": "Gabon",
    "270": "Gambia", "268": "Georgia", "276": "Germany", "288": "Ghana",
    "300": "Greece", "308": "Grenada", "320": "Guatemala", "324": "Guinea",
    "624": "Guinea-Bissau", "328": "Guyana", "332": "Haiti", "340": "Honduras",
    "348": "Hungary", "352": "Iceland", "356": "India", "360": "Indonesia",
    "364": "Iran", "368": "Iraq", "372": "Ireland", "376": "Israel",
    "380": "Italy", "388": "Jamaica", "392": "Japan", "400": "Jordan",
    "398": "Kazakhstan", "404": "Kenya", "296": "Kiribati", "408": "North Korea",
    "410": "South Korea", "414": "Kuwait", "417": "Kyrgyzstan", "418": "Laos",
    "428": "Latvia", "422": "Lebanon", "426": "Lesotho", "430": "Liberia",
    "434": "Libya", "438": "Liechtenstein", "440": "Lithuania", "442": "Luxembourg",
    "450": "Madagascar", "454": "Malawi", "458": "Malaysia", "462": "Maldives",
    "466": "Mali", "470": "Malta", "478": "Mauritania", "480": "Mauritius",
    "484": "Mexico", "583": "Micronesia", "498": "Moldova", "492": "Monaco",
    "496": "Mongolia", "499": "Montenegro", "504": "Morocco", "508": "Mozambique",
    "104": "Myanmar", "516": "Namibia", "520": "Nauru", "524": "Nepal",
    "528": "Netherlands", "554": "New Zealand", "558": "Nicaragua", "562": "Niger",
    "566": "Nigeria", "807": "North Macedonia", "578": "Norway", "512": "Oman",
    "586": "Pakistan", "591": "Panama", "598": "Papua New Guinea", "600": "Paraguay",
    "604": "Peru", "608": "Philippines", "616": "Poland", "620": "Portugal",
    "634": "Qatar", "642": "Romania", "643": "Russia", "646": "Rwanda",
    "659": "Saint Kitts and Nevis", "662": "Saint Lucia", "670": "Saint Vincent and the Grenadines",
    "882": "Samoa", "674": "San Marino", "678": "Sao Tome and Principe",
    "682": "Saudi Arabia", "686": "Senegal", "688": "Serbia", "690": "Seychelles",
    "694": "Sierra Leone", "702": "Singapore", "703": "Slovakia", "705": "Slovenia",
    "090": "Solomon Islands", "706": "Somalia", "710": "South Africa", "728": "South Sudan",
    "724": "Spain", "144": "Sri Lanka", "736": "Sudan", "740": "Suriname",
    "748": "Eswatini", "752": "Sweden", "756": "Switzerland", "760": "Syria",
    "762": "Tajikistan", "834": "Tanzania", "764": "Thailand", "626": "East Timor",
    "768": "Togo", "776": "Tonga", "780": "Trinidad and Tobago", "788": "Tunisia",
    "792": "Turkey", "795": "Turkmenistan", "798": "Tuvalu", "800": "Uganda",
    "804": "Ukraine", "784": "United Arab Emirates", "826": "United Kingdom",
    "840": "United States", "858": "Uruguay", "860": "Uzbekistan", "548": "Vanuatu",
    "862": "Venezuela", "704": "Vietnam", "887": "Yemen", "894": "Zambia",
    "716": "Zimbabwe", "275": "Palestine", "162": "Christmas Island",
    "260": "French Southern Territories", "540": "New Caledonia", "258": "French Polynesia",
    "584": "Marshall Islands", "850": "United States Virgin Islands",
    "630": "Puerto Rico", "254": "French Guiana", "446": "Macau", "344": "Hong Kong",
    "729": "Sudan", "158": "Taiwan", "336": "Vatican City", "585": "Palau",
    "732": "Western Sahara", "086": "British Indian Ocean Territory",
    "334": "Heard Island and McDonald Islands", "074": "Bouvet Island",
    "248": "Aland Islands", "831": "Guernsey", "832": "Jersey", "833": "Isle of Man",
    "652": "Saint Barthelemy", "663": "Saint Martin (French part)",
    "534": "Sint Maarten (Dutch part)", "531": "Curacao", "599": "Aruba",
    "528": "Netherlands", "852": "Hong Kong",
}


def parse_country_name_map(js_text):
    """解析 countryNameMap：英文名 -> 中文名"""
    m = re.search(r"const countryNameMap = \{(.*?)\};", js_text, re.S)
    if not m:
        return {}
    body = m.group(1)
    result = {}
    for k, v in re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', body):
        result[k] = v
    return result


def parse_city_names(js_text):
    """解析 CITY_NAMES：中文名 -> [英文名, 当地名...]"""
    m = re.search(r"const CITY_NAMES = \{(.*?)\n\s*\};", js_text, re.S)
    if not m:
        return {}
    body = m.group(1)
    result = {}
    # 匹配 '中文名': [...] 条目，注意城市名中可能含单引号（如 N'Djamena）
    for km, arr in re.findall(r"'([^']+)'\s*:\s*\[(.*?)\]", body, re.S):
        names = re.findall(r"['\"]([^'\"]+)['\"]", arr)
        result[km] = names
    return result


def parse_city_data(js_text):
    """
    解析 CITY_DATA:
      '156': { cities: [ {name:'北京', lat:.., lng:.., sights:[..]}, ... ] },
    返回 { code: [ {name, lat, lng, sights} ] }
    """
    m = re.search(r"const CITY_DATA = \{(.*?)\n\s*\};", js_text, re.S)
    if not m:
        return {}
    body = m.group(1)
    result = {}

    # 用括号深度法定位每个国家块 'code': { ... } 的边界
    starts = list(re.finditer(r"'(\d+)'\s*:\s*\{", body))
    for i, sm in enumerate(starts):
        code = sm.group(1)
        begin = body.index("{", sm.start())
        depth = 0
        j = begin
        while j < len(body):
            if body[j] == "{":
                depth += 1
            elif body[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        block = body[begin:j + 1]
        cities = []
        for city_m in re.finditer(
            r"\{\s*name:\s*'([^']+)'\s*,\s*lat:\s*([\d.-]+)\s*,\s*lng:\s*([\d.-]+)\s*,"
            r"\s*sights:\s*\[(.*?)\]\s*\}", block, re.S):
            name = city_m.group(1)
            lat = float(city_m.group(2))
            lng = float(city_m.group(3))
            sights = re.findall(r"'([^']+)'", city_m.group(4))
            cities.append({"name": name, "lat": lat, "lng": lng, "sights": sights})
        if cities:
            result[code] = cities
    return result


def main():
    with open(INDEX_HTML, "r", encoding="utf-8") as f:
        text = f.read()

    name_map = parse_country_name_map(text)      # 英文 -> 中文
    city_names = parse_city_names(text)          # 中文 -> [en, local]
    city_data = parse_city_data(text)            # 数字码 -> cities

    print(f"countryNameMap: {len(name_map)} 个国家")
    print(f"CITY_NAMES: {len(city_names)} 个城市名映射")
    print(f"CITY_DATA: {len(city_data)} 个国家的城市数据")

    # 汇总输出 cities.json
    cities_out = []
    missing_en = []
    for code, cities in city_data.items():
        en = ISO_NUMERIC_TO_EN.get(code, "")
        zh = name_map.get(en, "")
        if not en:
            missing_en.append(code)
        for c in cities:
            names = city_names.get(c["name"], [])
            cities_out.append({
                "country_code": code,
                "country_en": en,
                "country_zh": zh,
                "name": c["name"],
                "name_en": names[0] if len(names) > 0 else "",
                "name_local": names[1] if len(names) > 1 else "",
                "lat": c["lat"],
                "lng": c["lng"],
                "sights": c["sights"],
                "is_capital": 0,
            })

    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(cities_out, f, ensure_ascii=False, indent=1)
    print(f"OK: 已生成 {OUT_JSON}，共 {len(cities_out)} 个城市")
    if missing_en:
        print(f"WARN: 缺少英文国家名的代码: {missing_en}")


if __name__ == "__main__":
    main()
