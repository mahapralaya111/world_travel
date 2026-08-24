# 🌍 国际旅游规划助手（International Travel Planning Assistant）

> 计算机专业毕业设计 —— 前后端分离的交互式世界地图旅游规划系统

基于 Leaflet 交互式世界地图，融合**用户认证、多行程管理、批注标注、路线绘制、多语言城市搜索、数据统计看板、游记分享**的一站式旅游规划平台。

---

## ✨ 功能特性

| 模块 | 说明 |
|------|------|
| 🗺️ 交互式世界地图 | 点击国家查看/规划，国家高亮显示已规划状态 |
| 🔐 用户系统 | 注册 / 登录 / JWT 认证 / 个人资料修改 |
| 🗂️ 多行程管理 | 一个账号多个行程，每个行程独立批注与路线 |
| 📌 地图批注 | 在地图上打点添加笔记（标题 + 内容） |
| 🗺️ 路线绘制 | 点击绘制路线，自动计算里程，多颜色区分 |
| 🔍 多语言城市搜索 | 兼容中文 / 英文 / 当地文字，模糊匹配 + 推荐 |
| 📊 数据统计看板 | 个人统计 + 全局统计（行程/批注/路线/国家覆盖） |
| 📖 游记分享 | 发布公开游记，浏览计数，游记广场 |
| ☁️ 云端同步 | 登录后数据自动同步到数据库，多设备共享 |

---

## 🏗️ 技术架构

```
┌─────────────────────────────────────────────┐
│                 前端 (HTML/CSS/JS)            │
│  index.html (地图)  pages/ (登录/行程/看板/游记) │
└──────────────┬──────────────────────────────┘
               │  RESTful API + JWT (fetch)
┌──────────────▼──────────────────────────────┐
│              后端 (Python Flask)              │
│  app.py → 路由 → 业务逻辑 → 数据访问层         │
└──────────────┬──────────────────────────────┘
               │  数据库访问层 (database.py)
┌──────────────▼──────────────────────────────┐
│     数据库 SQLite（可切换 MySQL）              │
│  users / trips / annotations / routes /      │
│  notes / cities                             │
└─────────────────────────────────────────────┘
```

### 数据库表设计

| 表 | 说明 | 核心字段 |
|----|------|---------|
| `users` | 用户表 | username, password_hash, nickname, email |
| `trips` | 行程表 | user_id, name, description, start_date, end_date, status |
| `annotations` | 批注表 | trip_id, country_id, client_id, title, content, lat, lng |
| `routes` | 路线表 | trip_id, country_id, client_id, name, points(JSON), distance |
| `notes` | 游记表 | user_id, trip_id, title, content, is_public, view_count |
| `favorites` | 收藏表 | user_id, item_key, item_type, city_name, sight_name, lat, lng |
| `cities` | 城市表 | country_code, name, name_en, name_local, lat, lng, sights, is_capital, population |

### 核心接口一览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/register` | 用户注册 |
| POST | `/api/auth/login` | 用户登录（返回 JWT） |
| GET | `/api/auth/me` | 当前用户信息 |
| GET/POST | `/api/trips` | 行程列表 / 创建行程 |
| PUT/DELETE | `/api/trips/<id>` | 更新 / 删除行程 |
| POST | `/api/trips/<id>/sync` | 整国数据同步（批注+路线） |
| GET | `/api/cities/countries` | 国家列表（含已规划状态） |
| GET | `/api/cities/country-codes` | ISO 国家码映射（GeoJSON 数字码 ↔ 两位码） |
| GET | `/api/cities/<country_code>` | 该国全部城市（首都/人口优先排序） |
| GET | `/api/cities/search?q=` | 全量城市多语言搜索（中文/英文/拼音/当地文字） |
| GET/POST | `/api/favorites` | 收藏列表 / 收藏城市或景点 |
| GET | `/api/recommend` | 智能推荐（景点/城市，冷启动按知名城市） |
| POST | `/api/plan/route` | 智能路线规划（收藏或 points） |
| GET/POST | `/api/notes` | 游记列表 / 发布 |
| GET | `/api/ai/status` / POST `/api/ai/plan` | AI 行程规划助手 |
| GET | `/api/stats/mine` | 个人统计 |
| GET | `/api/stats/global` | 全局统计 |

---

## 🚀 快速开始

### 环境要求
- Python 3.9+
- 无需安装 Node.js
- 无需安装数据库（SQLite 内置）

### 安装依赖
```bash
pip install -r requirements.txt
```
> 依赖：Flask / Werkzeug（Web 框架）、pypinyin（城市拼音搜索）、pymysql（可选，切换 MySQL 时使用）。
> JWT 为自研实现（标准库 HMAC-SHA256）。

### 启动系统
```bash
# Windows
start.bat

# macOS / Linux
bash start.sh

# 或手动
python server/app.py
```

浏览器访问 **http://127.0.0.1:5000**

### 快速体验
1. 打开首页 → 点击地图上的国家（如中国）
2. 点击"添加批注" → 在地图上打点 → 输入标题保存
3. 点击"绘制路线" → 地图上点击多个点 → 双击完成
4. 未登录数据保存在本机；点击侧边栏"注册"后自动云端同步

---

## 📂 项目结构

```
AAAworldtravel/
├── index.html              # 主页面（交互式地图）
├── pages/
│   ├── login.html          # 登录/注册页
│   ├── trips.html          # 行程管理页
│   ├── dashboard.html      # 统计看板页
│   └── notes.html          # 游记广场页
├── static/
│   └── js/
│       └── api.js          # 前端 API 客户端（统一封装）
├── server/
│   ├── app.py              # Flask 应用入口（含前端托管）
│   ├── config.py           # 全局配置（数据库/密钥）
│   ├── database.py         # 数据库访问层（SQLite/MySQL）
│   ├── models.py           # 数据模型（建表 + 城市数据导入）
│   ├── auth.py             # JWT 认证模块（自研实现）
│   ├── utils.py            # 工具函数（距离计算/模糊搜索评分）
│   ├── routes/
│   │   ├── auth_routes.py  # 认证路由
│   │   ├── trip_routes.py  # 行程/批注/路线路由
│   │   ├── city_routes.py  # 城市搜索路由
│   │   ├── note_routes.py  # 游记路由
│   │   └── stats_routes.py # 统计路由
│   └── data/
│       ├── cities.json     # 城市基础数据（34150 个城市，GeoNames 全量）
│       └── country_codes.json  # ISO 国家码映射（数字码 ↔ 两位码）
├── scripts/
│   ├── rebuild_cities.py   # 清洗重建城市数据（合并重复/首都标记）
│   ├── build_country_codes.py  # 生成国家码映射
│   ├── fix_city_names.py   # 修正城市中文显示名
│   ├── fix_country_codes.py   # 修正国家码归属
│   ├── fix_numeric_codes.py   # 数字码 -> 两位码
│   ├── backfill_population.py # 人口数据回填
│   └── import_all_cities.py   # 城市数据导入
├── docs/
│   └── MySQL安装切换指南.md  # MySQL 手动安装与切换说明（可选功能）
├── start.bat / start.sh    # 一键启动脚本
└── README.md
```

---

## 🔐 安全设计

- **密码安全**：Werkzeug `generate_password_hash` 加盐哈希存储，绝不存明文
- **JWT 认证**：自研 HS256 签名 Token，7 天有效期，接口鉴权中间件统一校验
- **数据隔离**：所有行程/批注/路线均校验 `user_id` 归属，越权操作返回 403
- **输入校验**：用户名格式、密码长度、内容长度均有后端校验
- **SQL 注入防护**：全部使用参数化查询

---

## 💡 答辩要点（设计亮点）

1. **前后端分离架构**：Flask 提供 RESTful API，原生 JS 异步交互，前端页面由后端统一托管
2. **数据库设计规范化**：6 张表，外键约束 + 级联删除，行程-批注-路线业务链路完整
3. **数据同步策略**：以"行程+国家"为粒度的全量同步算法，`client_id` 保证前端 ID 稳定
4. **多语言搜索**：模糊评分算法（精确/包含/前缀/分词四级匹配），兼容中英当地文字
5. **JWT 自研实现**：标准库完成签名/校验全流程，可完整讲解原理
6. **可扩展性**：数据访问层抽象，SQLite ↔ MySQL 一键切换

---

## 🔄 切换 MySQL（可选，待手动安装）

> 📌 **该功能为可选增强，需要你手动安装 MySQL 数据库服务**（`pymysql` 驱动已随项目装好）。
> 完整安装与切换步骤见：**[docs/MySQL安装切换指南.md](docs/MySQL安装切换指南.md)**

简要流程：

```bash
# 1.（待手动）安装 MySQL Community Server 8.x，并创建数据库
mysql -u root -p -e "CREATE DATABASE travel_planner CHARACTER SET utf8mb4;"

# 2. 设置环境变量后启动（Windows 用 set，macOS/Linux 用 export）
set DB_TYPE=mysql
set MYSQL_HOST=localhost
set MYSQL_USER=root
set MYSQL_PASSWORD=your_password
set MYSQL_DB=travel_planner
python server/app.py
```

> 说明：MySQL 表结构与 SQLite 完全一致，建表由 `models.py` 自动执行；安装完成前系统使用内置 SQLite，功能不受影响。

---

## 📝 其他说明

- 地图底图使用 CartoDB Positron（免费商用），Leaflet / TopoJSON 通过多 CDN 容灾加载
- 城市数据（34150 个）来自 GeoNames 全球城市数据库（人口≥1.5万），含中英文名、首都标记、人口与景点数据，启动时自动导入数据库
- 未登录状态（游客模式）仍可使用全部地图功能，数据存于浏览器 localStorage
- AI 行程规划需在 `server/config.py` 中配置 `AI_API_KEY`（DeepSeek），未配置时面板会友好提示
