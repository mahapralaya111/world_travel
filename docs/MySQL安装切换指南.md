# MySQL 安装与切换指南（待手动安装部分）

> **本指南对应项目中的「可选功能」**：将数据库从默认的 SQLite 切换为 MySQL。
> 当前状态：Python 驱动 `pymysql` 已安装（v1.2.0），**仅缺 MySQL 数据库服务本体**，需你按下方步骤手动安装。
>
> ⚠️ 此部分为可选优化，不影响系统正常运行。安装完成前系统使用 SQLite（零配置）即可。

---

## 一、需要手动安装的内容

| 项目 | 说明 | 状态 |
|------|------|------|
| MySQL Community Server（8.x） | 数据库服务本体 | ❌ 待你手动安装 |
| PyMySQL 驱动 | Python 连接 MySQL 的驱动 | ✅ 已安装（v1.2.0） |

---

## 二、安装 MySQL 服务

### Windows（你的当前系统）

1. 下载安装包：https://dev.mysql.com/downloads/mysql/ ，选择 **Windows (x86, 64-bit), ZIP Archive** 或 MSI Installer。
   - MSI 安装器：一路下一步，安装时设置 `root` 用户密码（务必记住）。
   - ZIP 版：解压到 `C:\mysql`，将 `C:\mysql\bin` 加入系统 PATH。
2. 初始化并启动服务（ZIP 版需执行，MSI 版自动完成）：
   ```powershell
   cd C:\mysql\bin
   .\mysqld --initialize-insecure   # 生成 data 目录，root 空密码
   .\mysqld --install               # 注册为 Windows 服务
   net start mysql
   ```
3. 验证安装：
   ```powershell
   mysql -u root -p
   ```
   能进入 `mysql>` 提示符即安装成功。

### macOS

```bash
brew install mysql
brew services start mysql
```

### Linux (Debian/Ubuntu)

```bash
sudo apt update
sudo apt install mysql-server
sudo systemctl start mysql
```

---

## 三、创建项目数据库

进入 MySQL 控制台后执行：

```sql
CREATE DATABASE IF NOT EXISTS travel_planner CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

> 项目首次以 MySQL 模式启动时，`models.py` 会自动建表并导入 359 个城市数据，无需手动建表。

---

## 四、切换系统到 MySQL

### Windows（PowerShell）

```powershell
$env:DB_TYPE="mysql"
$env:MYSQL_HOST="localhost"
$env:MYSQL_PORT="3306"
$env:MYSQL_USER="root"
$env:MYSQL_PASSWORD="你的密码"
$env:MYSQL_DB="travel_planner"
python server/app.py
```

### macOS / Linux

```bash
export DB_TYPE=mysql
export MYSQL_HOST=localhost
export MYSQL_PORT=3306
export MYSQL_USER=root
export MYSQL_PASSWORD=你的密码
export MYSQL_DB=travel_planner
python server/app.py
```

---

## 五、验证切换成功

1. 启动后访问 http://127.0.0.1:5000
2. 注册一个新账号并添加一个批注
3. 在 MySQL 中确认数据已写入：
   ```sql
   USE travel_planner;
   SELECT * FROM users;
   SELECT * FROM annotations;
   ```
   能看到刚才注册的数据即说明切换成功。

---

## 六、切回 SQLite（如需恢复默认）

不设置 `DB_TYPE` 环境变量（或设为 `sqlite`）即可：

```powershell
# Windows
$env:DB_TYPE="sqlite"

# macOS / Linux
export DB_TYPE=sqlite
```

---

## 常见问题

**Q1：启动报错 `Access denied for user 'root'@'localhost'`**
- 密码错误。确认 `MYSQL_PASSWORD` 与安装时设置的 root 密码一致。

**Q2：启动报错 `Unknown database 'travel_planner'`**
- 未执行第三步的 `CREATE DATABASE`，先创建数据库再启动。

**Q3：MySQL 中文乱码**
- 数据库需使用 `utf8mb4` 字符集（第三步已指定）。

**Q4：不想用 MySQL 了**
- 直接按第六节切回 SQLite，SQLite 数据与 MySQL 数据互相独立。
