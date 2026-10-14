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
| `trip_plans` | AI 行程方案表 | trip_id(唯一), plan_data(JSON), created_at |
| `share_tokens` | 分享令牌表 | token, owner_id, trip_id, scope, snapshot(JSON), expire_at |
| `sync_state` | 同步版本表（乐观锁） | trip_id, country_id(联合主键), rev, last_client_id |

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
> 依赖：Flask / Werkzeug（Web 框架）、waitress（生产级 WSGI 服务器）、pypinyin（城市拼音搜索）、pymysql（切换 MySQL 时使用）。
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
│   ├── app.py              # Flask 应用入口（含前端托管 / waitress 生产服务器）
│   ├── config.py           # 全局配置（数据库/密钥/AI 并发参数）
│   ├── database.py         # 数据库访问层（SQLite/MySQL，WAL/连接复用）
│   ├── models.py           # 数据模型（建表 + 索引 + 城市数据导入）
│   ├── auth.py             # JWT 认证模块（自研实现）
│   ├── ai_planner.py       # AI 行程规划（大模型调用）
│   ├── ai_gate.py          # 【新增】AI 并发闸门 + 结果缓存 + 请求去重
│   ├── rate_limit.py       # 【新增】接口限流（防刷）
│   ├── utils.py            # 工具函数（距离计算/模糊搜索评分）
│   ├── routes/
│   │   ├── auth_routes.py  # 认证路由
│   │   ├── trip_routes.py  # 行程/批注/路线路由（含乐观锁同步）
│   │   ├── city_routes.py  # 城市搜索路由
│   │   ├── note_routes.py  # 游记路由
│   │   └── stats_routes.py # 统计路由
│   └── data/
│       ├── cities.json     # 城市基础数据（本地生成，不入库；见下方"城市数据"说明）
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

## 🚀 生产部署与多用户并发

项目默认已针对"多用户同时使用"做了处理：

| 关注点 | 方案 |
|--------|------|
| Web 服务器 | 启动时优先使用 **waitress**（生产级多线程 WSGI，`threads=8`）；未安装则退回 Flask 开发服务器并提示安装 |
| 大模型并发 | `server/ai_gate.py` 用信号量限制同时调用模型的数量，超出排队；相同入参命中缓存；并发相同请求自动合并（去重） |
| 大模型超时/重试 | 单次超时 `AI_TIMEOUT`（默认 180s），网络类错误指数退避重试；4xx 不重试 |
| 接口防刷 | `server/rate_limit.py` 滑动窗口限流（AI 规划：5 分钟 / IP 10 次） |
| SQLite 并发 | 开启 WAL + `busy_timeout=15s`，读写不互相阻塞，避免 `database is locked` |
| MySQL 并发 | 默认自动提交（避免长事务读旧快照）+ 建连/读写超时 + `ping` 重连 + 线程本地连接复用 |
| 多端同步 | `sync_state` 乐观锁：多标签页/多设备同时改同一国家时，冲突方返回 409 并自动重载云端数据，杜绝"后写覆盖先写" |
| 查询性能 | 启动时幂等补齐索引（annotations/routes/trips/cities/notes/favorites） |

### 环境变量（可选，均有合理默认值）

```bash
# AI 并发治理（本地 Ollama 建议保持 1~2；云端 API 可放宽）
set AI_MAX_CONCURRENT=2      # 同时调用模型的最大请求数
set AI_QUEUE_TIMEOUT=30      # 并发满时排队等待上限（秒）
set AI_CACHE_TTL=1800        # 相同入参结果缓存（秒），0 表示关闭
set AI_TIMEOUT=180           # 单次模型调用超时（秒）

# 部署收紧
set HOST=0.0.0.0             # 对外提供服务（会自动关闭 Flask 调试器）
set ALLOWED_ORIGINS=https://your-domain.com   # 收紧跨域（默认 *）
```

### 数据库密码安全

> ⚠️ **不要把 MySQL 密码写进 `start.bat`**（该文件会提交到 GitHub，会泄露）。
> 推荐做法：设置系统环境变量 `MYSQL_PASSWORD`，或创建 `server/.env.local`（已被 `.gitignore` 忽略）：
> ```
> MYSQL_PASSWORD=你的真实密码
> ```

### 城市数据（首次部署必看）

城市数据 `server/data/cities.json` 体积较大（约 7.4MB），**不纳入 Git 仓库**。首次部署若城市表为空，启动日志会提示：

```bash
python scripts/import_all_cities.py   # 从 GeoNames 下载并生成 cities.json + 导入数据库
```

---

## 📝 其他说明

- 地图底图使用 CartoDB Positron（免费商用），Leaflet / TopoJSON 通过多 CDN 容灾加载
- 城市数据来自 GeoNames 全球城市数据库（人口≥1.5万），含中英文名、首都标记、人口与景点数据；首次需运行 `scripts/import_all_cities.py` 生成并导入（见上方说明）
- 未登录状态（游客模式）仍可使用全部地图功能，数据存于浏览器 localStorage
- AI 行程规划默认使用本地 Ollama；也可在 `server/config.py` 通过环境变量切换 DeepSeek / 通义千问 / 智谱，未配置时面板会友好提示
