# Finplot

Finplot 是运行在本机的只读账单分析观测站。它读取 SQLite 账本，提供按时间范围、方向、性质、标签和分类逐层查看的统计图表；不会执行数据库迁移，也不会写入、删除或修改配置的真实账本。

当前稳定版本为 **v0.1.0**，完整发布历史见 [CHANGELOG.md](CHANGELOG.md)。Git、升级和回退操作见 [GIT-OPERATIONS.md](GIT-OPERATIONS.md)。

## 目录与运行入口

Finplot 采用稳定版 v1 与开发版 v2 分离的目录约定：

- 稳定版 v1：`/Users/x/.slock/agents/c131e49e-2176-40c0-9513-5bf482ab810c/billing-observatory`
  - Git 分支：`main`
  - 真实账本只读实例：端口 `8766`
  - 原有合成演示实例仍使用端口 `8765`
- 开发版 v2：`/Users/x/Finplot-dev`
  - Git 分支：`develop`
  - 默认使用合成数据库和独立端口 `8775` 验证；当前未启动开发实例
- Git 远端：`git@github.com:xusz12/Finplot.git`
- 首个正式版本：`v0.1.0`

`8765` 是稳定目录现有的合成演示服务，不是 v2 入口；启动或停止 v2 前必须重新核对端口和进程归属，不得误停现有服务。`8775` 仅是 v2 的默认启动端口约定，未启动不代表已有运行实例。

## 安装与启动

在目标目录创建虚拟环境并安装依赖：

```sh
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
```

### 稳定版真实账本只读实例

仅在确认目标数据库路径和启动配置后运行：

```sh
LEDGER_DB=/absolute/path/to/ledger.sqlite3 \
  .venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8766
```

浏览器打开 `http://127.0.0.1:8766/`。终端保持运行；使用 `Ctrl-C` 停止实例。Finplot 不修改 `LEDGER_DB` 指向的数据库。

### 合成数据库演示或开发验证

测试数据库只能使用隔离的合成文件：

```sh
python3 tests/make_fixture.py /tmp/finplot-demo.sqlite3
LEDGER_DB=/tmp/finplot-demo.sqlite3 \
  .venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8775
```

开发版 v2 使用 `/Users/x/Finplot-dev` 和 `develop` 分支；启动前确认 `8775` 未被占用。不要把真实账本复制到开发目录或测试目录。

## 功能范围

当前版本支持：

- Asia/Shanghai 时区下的日、月、季度、半年、年、全部和自选时间范围；
- 收入/支出方向、交易性质、活动标签的组合筛选，标签支持任意/全部语义；
- 总览卡片、投资小计、日收入与支出趋势、分类排名和分类钻取；
- 分类组 → 分类 → 交易明细的逐层查看，以及稳定的“加载更多”分页；
- 分析接口的上一周期、去年同期、自选对比和无对比模式；
- 日、月、季度、半年、年粒度的趋势、累计趋势、12 个月概览和月度支出日历；
- 数据版本检测与自动刷新：页面每两秒轮询 `/api/version`，仅在数据指纹改变时重新加载；
- 所有金额字段以十进制字符串表达整数分，平均值和日均值同时提供精确的分子/分母；
- 本地资源、同源访问和只读 API，不依赖 CDN 或第三方分析服务。

页面采用本地 JavaScript/CSS 资源。桌面端提供固定导航、分组筛选、四张财务摘要卡、投资摘要条、日收入/支出图表、分类钻取和交易表；窄屏时导航移到内容上方，卡片使用双列布局，长序列与宽表格在面板内滚动。

## API 与数据边界

- `GET /api/dashboard`：基础账单总览和分页明细；`cursor` 用于下一页，数据版本变化返回 `409`。
- `GET /api/analytics`：v1.1 聚合分析；支持与总览相同的业务筛选，以及 `compare`、`period_mode`、`grain` 和 `top_n` 参数。
- `GET /api/version`：返回数据集版本指纹，用于检测刷新。
- `/docs`：本地 API 文档。

接口对所有 `*_cents` 字段返回十进制字符串；过期的 `version` 或 `if_version` 返回 `409`，无效组合返回 `422`。响应使用 `Cache-Control: no-store`；服务限制 loopback Host、同源 Origin 和同源 CSP。请求日志不记录查询参数或交易数据。

真实账本只允许只读查询。测试、刷新竞争和接口验证必须使用隔离的临时 SQLite 数据库；不得执行迁移、写入、删除、重置真实数据库，也不得将真实账本、密钥、个人配置或敏感证据提交到 Git。

源码与运行数据分离：源码、测试夹具和文档进入 Git；真实账本、合成运行数据库、虚拟环境、密钥、个人配置和敏感证据不进入 Git。提交或升级前应检查当前工作区和整个 Git 历史。

## 验证

运行 API 单元测试：

```sh
python3 -m unittest discover -s tests -v
```

运行隔离且可复现的合成性能检查：

```sh
.venv/bin/python tests/benchmark.py
```

浏览器端 20 次渲染、并发刷新和响应式证据保存在 `reports/`。页面折叠的诊断面板显示初始加载、筛选、钻取、自动刷新次数和刷新 epoch；这些是浏览器渲染完成测量，不等同于 API 单独耗时。Chrome 合成器绘制时间不由本地无障碍测试工具提供，仍属于未单独验证项。

## 发布与升级

`main` 只接收已冻结、经用户验收的精确提交。功能或逻辑变更按项目流程先完成自验和审核；Git 管理员在操作前 fresh 检查 HEAD、远端、候选文件、工作区和用户已有修改，只提交批准范围，commit 信息使用简体中文。发布、稳定目录升级和运行重启是三个分别记录的动作。

升级前必须记录旧提交和启动配置，确认稳定目录 clean、没有分叉、目标提交已验收，再执行 `git fetch origin main` 和 `git pull --ff-only origin main`。升级后按原配置重启 `8766`，完成页面、API 和只读数据冒烟检查。

如果升级失败，停止新实例并保留失败证据，使用升级前记录的提交和启动配置恢复；不得用 reset 覆盖用户改动，不得修改真实数据库。详细命令和回退边界见 [GIT-OPERATIONS.md](GIT-OPERATIONS.md)。
