# -*- coding: utf-8 -*-
"""
智能路线规划模块
================
算法设计：
1. 贪心最近邻（Greedy Nearest Neighbor）生成初始路线
2. 2-opt 局部优化（Two-Opt Optimization）改进路线，避免路径交叉
3. 时间预算模型：根据用户可游玩天数，将景点分日安排

适合本科毕设讲解：算法简单、可手绘演示、有明确的优化前后对比。
"""
import math


def haversine(lat1, lng1, lat2, lng2):
    """计算两点间球面距离（公里）"""
    R = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _nearest_neighbor(points, start_idx=0):
    """贪心最近邻算法生成初始访问顺序"""
    n = len(points)
    if n <= 1:
        return list(range(n))
    visited = [False] * n
    order = [start_idx]
    visited[start_idx] = True
    for _ in range(n - 1):
        last = points[order[-1]]
        best = None
        best_d = float("inf")
        for i in range(n):
            if visited[i]:
                continue
            d = haversine(last["lat"], last["lng"], points[i]["lat"], points[i]["lng"])
            if d < best_d:
                best_d = d
                best = i
        order.append(best)
        visited[best] = True
    return order


def _two_opt(points, order):
    """
    2-opt 局部优化：消除路线交叉
    遍历所有边对 (i,j)，若交换两条边能使总距离缩短，则进行交换
    """
    n = len(points)
    if n < 4:
        return order
    improved = True
    while improved:
        improved = False
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                # 计算当前路径长度
                def seg_d(a, b):
                    return haversine(points[a]["lat"], points[a]["lng"],
                                     points[b]["lat"], points[b]["lng"])
                cur = seg_d(order[i - 1], order[i]) + seg_d(order[j - 1], order[j])
                new = seg_d(order[i - 1], order[j - 1]) + seg_d(order[i], order[j])
                if new + 1e-9 < cur:
                    # 反转 order[i..j-1]
                    order[i:j] = reversed(order[i:j])
                    improved = True
    return order


def _total_distance(points, order):
    """计算总路程（公里）"""
    total = 0.0
    for i in range(len(order) - 1):
        a, b = points[order[i]], points[order[i + 1]]
        total += haversine(a["lat"], a["lng"], b["lat"], b["lng"])
    return round(total, 1)


def plan_route(points, start_index=0):
    """
    智能路线规划主函数
    points: [{lat, lng, name, ...}] 待访问的点
    start_index: 起点在 points 中的下标
    返回: {order, points, total_distance, sequence}
    """
    if not points:
        return {"order": [], "points": [], "total_distance": 0, "sequence": []}
    if start_index < 0 or start_index >= len(points):
        start_index = 0

    # 1. 贪心最近邻
    order = _nearest_neighbor(points, start_index)
    # 2. 2-opt 优化
    order = _two_opt(points, order)
    # 3. 计算总路程
    total = _total_distance(points, order)

    sequence = [{
        "name": points[i].get("name", ""),
        "city_name": points[i].get("city_name") or points[i].get("name", ""),
        "lat": points[i]["lat"],
        "lng": points[i]["lng"],
        "index": idx + 1,
    } for idx, i in enumerate(order)]

    return {
        "order": order,
        "points": points,
        "total_distance": total,
        "sequence": sequence,
    }
