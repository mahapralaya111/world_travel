# -*- coding: utf-8 -*-
"""
离线包生成器（第三阶段）
========================
把一份 TripPlan 序列化为"单文件 HTML 离线包"：
- 数据序列化：行程 JSON 以 <script type="application/json"> 内嵌
- 单文件模板：样式 + 渲染脚本全部内联，双击即可离线查看（无需服务器）
- 二维码：生成"离线包内容"的二维码（用标准库 zlib+base64 压缩编码，
  避免引入第三方二维码依赖；前端可用 qrcode.js 渲染为图片）

输出：一个自包含的 HTML 字符串
"""
import base64
import html as html_mod
import json
import zlib

# 手机离线页单文件模板
# 模板变量（用 %()s 占位）：
#   TITLE  行程标题
#   DATA   JSON 数据（<script type="application/json"> 中内嵌）
OFFLINE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>%(TITLE)s</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
         background: #0f172a; color: #e2e8f0; min-height: 100vh; }
  .topbar { display: flex; align-items: center; justify-content: space-between;
            padding: 14px 18px; background: rgba(255,255,255,0.05);
            border-bottom: 1px solid rgba(255,255,255,0.08); position: sticky; top: 0;
            backdrop-filter: blur(8px); z-index: 10; }
  .topbar .title { font-size: 16px; font-weight: 700; color: #fff; }
  .topbar .offline-tag { font-size: 11px; padding: 3px 10px; border-radius: 20px;
                          background: rgba(123,216,143,0.15); color: #7bd88f; }
  .container { max-width: 720px; margin: 0 auto; padding: 18px 16px 60px; }
  .summary { background: linear-gradient(135deg, rgba(74,158,255,0.12), rgba(156,39,176,0.10));
             border: 1px solid rgba(74,158,255,0.25); border-radius: 12px; padding: 14px;
             font-size: 13px; line-height: 1.7; margin-bottom: 14px; }
  .dest-list { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; }
  .dest-chip { background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.12);
               padding: 6px 12px; border-radius: 20px; font-size: 12px; color: #cbd5e1; }
  .dest-chip b { color: #fff; }
  .day-card { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.09);
              border-radius: 14px; padding: 16px; margin-bottom: 14px; }
  .day-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
  .day-badge { background: linear-gradient(135deg, #4a9eff, #2563eb); color: #fff;
               font-size: 13px; font-weight: 700; padding: 4px 12px; border-radius: 8px; }
  .day-city { font-size: 12px; color: #94a3b8; }
  .day-theme { font-size: 12px; color: #4a9eff; margin-bottom: 10px; }
  .act { display: flex; gap: 10px; padding: 8px 0; border-bottom: 1px dashed rgba(255,255,255,0.08); }
  .act:last-child { border-bottom: none; }
  .act-time { width: 48px; flex-shrink: 0; font-size: 12px; color: #94a3b8; padding-top: 2px; }
  .act-body { flex: 1; }
  .act-name { font-size: 14px; color: #fff; font-weight: 600; }
  .act-meta { font-size: 11px; color: #64748b; margin-top: 3px; }
  .act-cost { font-size: 12px; color: #fbbf24; margin-top: 3px; }
  .day-tips { font-size: 12px; color: #7bd88f; background: rgba(123,216,143,0.08);
              border-radius: 8px; padding: 8px 10px; margin-top: 10px; }
  .banner { background: rgba(74,158,255,0.1); border: 1px dashed rgba(74,158,255,0.4);
            border-radius: 12px; padding: 14px; text-align: center; margin-bottom: 14px; }
  .banner .qr-wrap { display: none; }
  .banner.show-qr .qr-wrap { display: block; margin-top: 10px; }
  .banner button { background: linear-gradient(135deg, #4a9eff, #2563eb); color: #fff;
                   border: none; padding: 9px 18px; border-radius: 10px; font-size: 13px; cursor: pointer; }
  .map-card { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.09);
              border-radius: 14px; padding: 16px; margin-bottom: 14px; }
  .map-card h4 { font-size: 14px; margin-bottom: 10px; }
  .map-box { width: 100%%; height: auto; display: block; background: rgba(15,23,42,0.65);
             border: 1px solid rgba(255,255,255,0.1); border-radius: 10px; }
  .md-item { display: flex; gap: 10px; padding: 8px 0;
             border-bottom: 1px dashed rgba(255,255,255,0.08); font-size: 13px; }
  .md-item:last-child { border-bottom: none; }
  .md-dot { width: 10px; height: 10px; border-radius: 50%%; flex-shrink: 0; margin-top: 5px; }
  .md-line { width: 22px; height: 4px; border-radius: 2px; flex-shrink: 0; margin-top: 8px; }
  .md-body { flex: 1; min-width: 0; }
  .md-name { color: #fff; font-weight: 600; }
  .md-sub { color: #64748b; font-size: 11px; margin-top: 2px; word-break: break-all; }
  .footer { text-align: center; color: #475569; font-size: 11px; margin-top: 22px; }
</style>
</head>
<body>
<div class="topbar">
  <div class="title">%(TITLE)s</div>
  <span class="offline-tag">离线包</span>
</div>
<div class="container" id="app"></div>
<div class="footer">国际旅游规划助手 · 离线行程</div>

<script type="application/json" id="trip-data">
%(DATA)s
</script>
<script>
(function() {
  var raw = document.getElementById('trip-data').textContent;
  var plan = JSON.parse(raw);
  var app = document.getElementById('app');
  var esc = function(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function(c) {
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); };
  var h = [];

  // 摘要
  if (plan.summary) h.push('<div class="summary">' + esc(plan.summary) + '</div>');

  // 目的地
  if (plan.destinations && plan.destinations.length) {
    h.push('<div class="dest-list">');
    plan.destinations.forEach(function(d) {
      h.push('<div class="dest-chip"><b>' + esc(d.city) + '</b> · ' + esc(d.country || '') +
             ' · ' + d.days + ' 天</div>');
    });
    h.push('</div>');
  }

  // 每日行程
  (plan.days || []).forEach(function(day) {
    h.push('<div class="day-card">');
    h.push('<div class="day-head"><span class="day-badge">Day ' + day.day + '</span>' +
           '<span class="day-city">' + esc(day.date) + ' · ' + esc(day.city) + '</span></div>');
    if (day.theme) h.push('<div class="day-theme">主题：' + esc(day.theme) + '</div>');
    (day.activities || []).forEach(function(a) {
      var typeLabel = {sight:'景点', food:'美食', transport:'交通', hotel:'住宿', free:'自由'}[a.type] || '活动';
      h.push('<div class="act">');
      h.push('<div class="act-time">' + esc(a.time) + '</div>');
      h.push('<div class="act-body"><div class="act-name">' + esc(a.name) + '</div>');
      h.push('<div class="act-meta">' + typeLabel + (a.transport ? ' · ' + esc(a.transport) : '') + '</div>');
      if (a.cost) h.push('<div class="act-cost">约 ¥' + esc(a.cost) + '</div>');
      h.push('</div></div>');
    });
    if (day.tips) h.push('<div class="day-tips">💡 ' + esc(day.tips) + '</div>');
    h.push('</div>');
  });

  // 地图标注（路线 + 批注）
  var md = plan.map_data || {};
  var rts = (md.routes || []).filter(function(r) { return r && (r.points || []).length >= 2; });
  var anns = (md.annotations || []).filter(function(a) { return a && (a.lat || a.lng); });
  if (rts.length || anns.length) {
    h.push('<div class="map-card"><h4>🗺️ 地图标注</h4>' +
           '<svg id="mapSvg" class="map-box" viewBox="0 0 640 380" preserveAspectRatio="xMidYMid meet"></svg>');
    if (rts.length) {
      h.push('<div style="margin-top:10px">');
      rts.forEach(function(r) {
        h.push('<div class="md-item"><span class="md-line" style="background:' + esc(r.color || '#4a9eff') + '"></span>' +
               '<div class="md-body"><div class="md-name">' + esc(r.name || '未命名路线') + '</div>' +
               '<div class="md-sub">' + esc(r.country_name || '') +
               (r.distance ? (r.country_name ? ' · ' : '') + '约 ' + esc(r.distance) + ' km' : '') +
               ' · ' + r.points.length + ' 个点</div></div></div>');
      });
      h.push('</div>');
    }
    if (anns.length) {
      h.push('<div style="margin-top:10px">');
      anns.forEach(function(a) {
        h.push('<div class="md-item"><span class="md-dot" style="background:' + esc(a.color || '#ff5252') + '"></span>' +
               '<div class="md-body"><div class="md-name">' + esc(a.title) + '</div>' +
               (a.content ? '<div class="md-sub">' + esc(a.content) + '</div>' : '') + '</div></div>');
      });
      h.push('</div>');
    }
    h.push('</div>');
  }

  app.innerHTML = h.join('');

  // 绘制简易标注示意图（等距圆柱投影，纯 SVG，离线可用）
  if (rts.length || anns.length) {
    // 跨 180° 经线的路线先做经度解卷，避免横穿整幅图
    rts.forEach(function(r) {
      var prev = null;
      r.points = r.points.map(function(p) {
        var lng = +p.lng;
        if (prev != null) {
          while (lng - prev > 180) lng -= 360;
          while (lng - prev < -180) lng += 360;
        }
        prev = lng;
        return { lat: +p.lat, lng: lng };
      });
    });
    var svg = document.getElementById('mapSvg');
    var pts = [];
    anns.forEach(function(a) { pts.push([+a.lat, +a.lng]); });
    rts.forEach(function(r) { r.points.forEach(function(p) { pts.push([p.lat, p.lng]); }); });
    if (svg && pts.length) {
      var W = 640, H = 380, pad = 34;
      var lat0 = pts[0][0], lng0 = pts[0][1];
      var minLat = lat0, maxLat = lat0, minLng = lng0, maxLng = lng0;
      pts.forEach(function(p) {
        if (p[0] < minLat) minLat = p[0]; if (p[0] > maxLat) maxLat = p[0];
        if (p[1] < minLng) minLng = p[1]; if (p[1] > maxLng) maxLng = p[1];
      });
      var kx = Math.cos((minLat + maxLat) / 2 * Math.PI / 180) || 0.0001;
      var xs = [], ys = [];
      pts.forEach(function(p) { xs.push((p[1] - lng0) * kx); ys.push(lat0 - p[0]); });
      var minX = Math.min.apply(null, xs), maxX = Math.max.apply(null, xs);
      var minY = Math.min.apply(null, ys), maxY = Math.max.apply(null, ys);
      var s = Math.min((W - 2 * pad) / ((maxX - minX) || 0.0001), (H - 2 * pad) / ((maxY - minY) || 0.0001));
      var ox = (W - (maxX - minX) * s) / 2, oy = (H - (maxY - minY) * s) / 2;
      var project = function(lat, lng) {
        return [ox + ((lng - lng0) * kx - minX) * s, oy + ((lat0 - lat) - minY) * s];
      };
      var sh = [];
      rts.forEach(function(r) {
        var d = r.points.map(function(p, i) {
          var q = project(p.lat, p.lng);
          return (i ? 'L' : 'M') + q[0].toFixed(1) + ' ' + q[1].toFixed(1);
        }).join(' ');
        sh.push('<path d="' + d + '" fill="none" stroke="' + esc(r.color || '#4a9eff') +
                '" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" opacity="0.9"/>');
        var q0 = project(r.points[0].lat, r.points[0].lng);
        sh.push('<circle cx="' + q0[0].toFixed(1) + '" cy="' + q0[1].toFixed(1) + '" r="3.5" fill="' +
                esc(r.color || '#4a9eff') + '" stroke="#fff" stroke-width="1.2"/>');
      });
      anns.forEach(function(a) {
        var q = project(+a.lat, +a.lng);
        sh.push('<circle cx="' + q[0].toFixed(1) + '" cy="' + q[1].toFixed(1) + '" r="5" fill="' +
                esc(a.color || '#ff5252') + '" stroke="#fff" stroke-width="1.5"/>');
        if (a.title) sh.push('<text x="' + q[0].toFixed(1) + '" y="' + (q[1] - 9).toFixed(1) +
                '" text-anchor="middle" font-size="11" fill="#e2e8f0">' + esc(a.title) + '</text>');
      });
      svg.innerHTML = sh.join('');
    }
  }
})();
</script>
</body>
</html>
"""


def serialize_plan(plan):
    """行程数据序列化：JSON → raw deflate 压缩 → urlsafe base64

    为什么用 raw deflate (wbits=-15) 而不是标准 zlib：
      浏览器原生 DecompressionStream('deflate-raw') 直接支持 raw deflate，
      但不支持 RFC 1950 zlib 格式（需要手动跳过 2 字节 header + 4 字节 Adler checksum）。
      统一用 raw deflate，前端零字节切割风险。
    """
    data = json.dumps(plan, ensure_ascii=False).encode("utf-8")
    # zlib.compress 默认输出 zlib 格式（有 header+checksum）
    # 用 compressobj(wbits=-15) 直接输出 raw deflate
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    raw_deflate = co.compress(data) + co.flush()
    return base64.urlsafe_b64encode(raw_deflate).decode("ascii")


_MAX_INFLATE_BYTES = 8 * 1024 * 1024  # 解压后 JSON 上限（8MB），防御超大 payload


def deserialize_plan(payload):
    """反序列化（兼容 raw deflate 和旧 zlib 格式）

    - 自动补齐 base64 padding（前端有时会去掉 = ）
    - 限制解压后的最大体积，避免恶意超大 payload 打爆内存
    - 只接受 JSON 对象，非法输入抛 ValueError（由路由转成友好提示）
    """
    if not payload or not isinstance(payload, str):
        raise ValueError("缺少 payload")
    raw_b64 = payload.strip()
    pad = len(raw_b64) % 4
    if pad:
        raw_b64 += "=" * (4 - pad)
    try:
        raw = base64.urlsafe_b64decode(raw_b64.encode("ascii"))
    except Exception as e:
        raise ValueError("base64 解码失败: %s" % e)

    last_err = None
    for wbits in (-15, 15):  # -15: raw deflate（新格式）；15: 旧 zlib 格式
        try:
            out = zlib.decompressobj(wbits).decompress(raw, _MAX_INFLATE_BYTES + 1)
        except Exception as e:
            last_err = e
            continue
        if len(out) > _MAX_INFLATE_BYTES:
            raise ValueError("离线数据体积超出上限")
        try:
            plan = json.loads(out.decode("utf-8"))
        except Exception as e:
            last_err = e
            continue
        if not isinstance(plan, dict):
            raise ValueError("离线数据格式不正确（期望对象）")
        return plan
    raise ValueError("离线数据解析失败: %s" % last_err)


def build_offline_html(plan):
    """把 TripPlan 打包成单文件 HTML 离线包"""
    # 把 </ 转义成 <\/（JSON 合法转义）：防止行程标题/批注里出现 </script> 破坏页面结构
    data_json = json.dumps(plan, ensure_ascii=False).replace("</", "<\\/")
    title = (plan.get("meta") or {}).get("title") or "我的行程"
    return OFFLINE_TEMPLATE % {
        "TITLE": html_mod.escape(str(title)),
        "DATA": data_json,
    }


# QR Code Byte 模式容量表（纠错级别 L = 最大容量）
# 来源 ISO/IEC 18004:2015 Table 7
# 我们用最高版本 v40 作为物理上限，避免生成扫不出的大码
QR_V40_L_MAX_BYTES = 2850  # v40-L Byte 模式上限（1 char = 1 byte for ASCII base64）


def qr_payload(plan):
    """返回二维码承载文本（供前端 qrcode.js 渲染）

    始终返回完整 payload（含 map_data），不再静默剥离。
    如果超过 QR Code v40-L 物理上限 (2850 bytes)，前端 QRCode.js 会抛异常，
    由调用方（build_offline）通过 qr_oversized 标志提示用户「二维码可能无法识别」。
    """
    return "trip:" + serialize_plan(plan)


def qr_check_oversized(plan):
    """检查完整 payload 是否超过二维码 v40-L 物理上限"""
    return len(qr_payload(plan)) > QR_V40_L_MAX_BYTES


def page_payload(plan):
    """
    返回手机离线页（/pages/offline.html#...）承载文本。
    hash 链接没有二维码的长度限制，始终带上 map_data（地图批注/路线）。
    """
    return "trip:" + serialize_plan(plan)
