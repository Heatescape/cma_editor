# CMA Editor

[English](README.md) &nbsp;·&nbsp; **[中文](README.zh-CN.md)**

> Windows 本地桌面应用,**重新生成 Cotality CMA(房产市场比较分析)报告**中的可编辑页面 —— 完整保留原报告的不可编辑部分,同时把封面、地图和可比房源详情页换成全新生成、可自由编辑的版本。

---

## 目录

- [概述](#概述)
- [功能](#功能)
- [快速开始](#快速开始)
- [使用步骤](#使用步骤)
- [架构](#架构)
- [API 参考](#api-参考)
- [打包分发](#打包分发)
- [故障排查](#故障排查)
- [依赖](#依赖)

---

## 概述

地产中介在使用 Cotality CMA 报告时经常需要:替换可比房源、更新封面图、调整中介信息后才能呈交客户。CMA Editor 把这套流程自动化:

1. 上传原始 Cotality PDF
2. 编辑可比成交 / 在售房源(或从 realestate.com.au 拉取最新数据)
3. 点击 **生成** —— 几秒后输出一份精致的 PDF

所有未明确重新生成的页面(封面信、人口统计、学校信息、市场趋势、免责声明)**逐字节完整保留**,跟原 PDF 一致。

```
原始 Cotality PDF
        │
        ▼
┌───────────────────────────────────────────────────┐
│  CMA Editor                                       │
│                                                   │
│  封面页        ← 重生成(新封面图)                  │
│  封面信        ← 中介签名更新                       │
│  你的房源      ← 保留 + 抹掉冗余前言                │
│  地图          ← 全新 Google 静态地图              │
│  成交卡片      ← 全新格式化详情页                   │
│  在售卡片      ← 全新格式化详情页                   │
│  尾页          ← 原样保留                          │
└───────────────────────────────────────────────────┘
        │
        ▼
 最终 CMA PDF(可呈交客户)
```

---

## 功能

### 数据提取

- **自动解析 Cotality PDF** —— 上传后自动填好:目标房源地址、中介姓名、机构、邮箱、报告日期、所有可比成交 / 在售房源、卡片照片
- **基于视觉位置的图片匹配** —— 用 PyMuPDF 的位置信息把卡片照片分配到正确房源,而不是按 pypdf 返回的 XObject 引用顺序(后者会错位)
- **支持公寓 / 单元** —— 兼容没有土地面积的房源;ha 自动转 m²;弹性正则适配 Cotality 各种图标 + 文字格式

### REA 网站抓取

- **基于 Playwright 的爬虫**,用 Patchright 绕过 Cloudflare / Akamai / Kasada 反机器人检测
- **ArgonautExchange JSON** —— 直读 REA 页面里嵌的完整 Apollo cache,数据准确度最高
- **多层兜底** —— JSON-LD 结构化数据、aria-label 模式、type/value 属性数组、HTML 正则;非标准房源也能正确抽出 床/卫/车 数据
- **每个房源 5 张去重照片** —— 下载最多 20 个候选 URL,按 MD5 哈希去重,保留前 5 张唯一的;UI 中可以点选切换
- **抓取字段:** 地址、区、州、邮编、坐标、卧室、卫浴、车位、土地面积、建筑面积、价格、房型、建造年份、挂牌日期、成交日期、市场天数、标题、机构名、封面图 + 图册
- **429 限流处理** —— REA 返回 429 时自动 15 秒 + 45 秒重试;每页随机延迟(3-7s)、跨区随机延迟(4-9s)降低被限频概率
- **持久 Chrome profile** —— 把会话 cookie 保存到 `~/.cma_rea_profile`,爬虫复用已登录 REA 状态;比临时浏览器更不易被反爬识别

### 地图

- **Google 静态地图集成** —— 每个可比页面带一张以目标房源为中心的新地图
- **自定义水滴标记** —— 数字标号,3× 超采样后 LANCZOS 缩放,输出锐利;白色描边保证任何背景下可读
- **智能重叠解决** —— 两阶段算法:旋转分散(轨道角求解)+ 力导向头部分离;无论几个共址房源都不用手动调
- **三种地图页** —— 总览(全部)/ 仅成交 / 仅在售;每页可单独开关

### PDF 生成

- **ReportLab 渲染** —— 每页 5 个可比卡片,紧凑布局含图标条(床/卫/车/地/建)、元数据网格、缩略图、距目标房源距离
- **SSE 进度流** —— 单卡级实时进度 + ETA(如 "渲染可比成交 (3/7)... 剩余 ~14s")
- **正确页面拼接** —— pypdf 把新页面 merge 进原 PDF;原中介签名块用白色矩形遮盖;"你的房源"页面 Cotality 前言被干净替换

### UI / 工作流

- **连续编号** —— 成交 1-N,在售 N+1-M;删除立即重排所有编号(包括另一个 tab 里的);Undo 恢复原编号
- **拖拽排序** —— 地图标记编号跟随列表顺序
- **批量操作** —— 一键拉取所有 REA、批量地理编码、一键显示/隐藏 床/卫/车/地/建
- **自动找可比** —— 按可配置的 床/卫/车/地/建 范围过滤器自动搜 REA,候选 URL 列出来供 review
- **可折叠区块** —— 点 Step 4 / 5 标题折叠/展开,聚焦其他步骤时屏幕清爽
- **5 秒撤销** —— 软删除带倒计时,过期才永久删除
- **离线优先** —— 只有抓取、地理编码、地图请求需要联网
- **自动关机** —— 浏览器 tab 关闭 30 秒后服务器自动退出,无后台残留

### 安装与分发

- **零配置启动** —— `start.bat` 首次运行自动建 venv、装包、下载 Chromium
- **自动更新依赖** —— 每次启动哈希 `requirements.txt`,内容变了自动 pip install(发布新版本时无缝)
- **桌面快捷方式** —— `create_shortcut.bat` 创建带自定义图标的快捷方式
- **一键打包** —— `build_release.bat` 输出独立的 `CMA-Editor.zip` 可分发

---

## 快速开始

### 系统要求

- **Windows 10 或更新版本**
- **Python 3.10+** —— 从 [python.org](https://www.python.org/downloads/) 下载
  - ✅ 安装时勾选 **"Add Python to PATH"**
- **~500 MB 可用磁盘**(venv + Chromium)
- **Google Maps API Key** —— 用于地图和地理编码([免费申请](https://console.cloud.google.com/))

### 首次运行

1. 解压 `CMA-Editor.zip`(或 git clone)到任意目录,如 `C:\CMA-Editor`
2. 双击 **`start.bat`**
3. 首次启动安装所有依赖 + 下载 Chromium —— **2-5 分钟**,有进度提示
4. 浏览器自动打开 `http://localhost:8000`

后续启动**隐藏控制台窗口**,5 秒内打开浏览器。

### 可选:桌面快捷方式

双击 **`create_shortcut.bat`** 一次创建桌面快捷方式 **CMA Editor**,以后从这里启动不用打开项目目录。

---

## 使用步骤

应用左侧侧边栏 **6 步流程** 引导操作。

### 第 1 步 — 上传原始 PDF

上传 Cotality 生成的 CMA PDF,自动提取并填好:

| 字段 | 用于 |
|---|---|
| 目标房源地址 | 第 3 步 |
| 目标房源 床/卫/车/地/建 | 第 3 步 |
| 中介姓名、机构、邮箱、报告日期 | 第 6 步 |
| 所有可比成交(地址、价格、特征、照片) | 第 4 步 |
| 所有可比在售(地址、价格、特征、照片) | 第 5 步 |

可选:上传**封面替换图**(封面页大照片)。也可以在第 3 步换。

### 第 2 步 — Google Maps API Key

需要用来:
- 渲染可比位置地图(Maps Static API)
- "全部地理编码" + 单行地理编码按钮(Geocoding API)

**免费申请:** [console.cloud.google.com](https://console.cloud.google.com/) → 启用 **Maps Static API** 和 **Geocoding API**

API Key 保存在浏览器 `localStorage`,**每台设备只用输一次**。

### 第 3 步 — 目标房源

| 字段 | 说明 |
|---|---|
| **封面图** | 封面页大照片;拖拽 / 点击上传 |
| **地址** | 完整街道地址(将出现在封面) |
| **经纬度** | 用于地图居中和距离计算;点 **⊕ 自动填坐标** 自动地理编码 |
| **房型** | House / Unit / Townhouse / Land |
| **床 / 卫 / 车** | 显示在封面 |
| **土地面积** | m² |
| **建筑面积** | m²(可选) |

### 第 4 步 — 可比成交

#### 添加可比

| 方式 | 操作 |
|---|---|
| **从 PDF** | 上传后自动填 |
| **从 REA** | 点 **+ 来自 realestate.com.au**,粘 URL,点 **拉取** |
| **手动** | 点 **+ 手动添加**,直接填字段 |

#### 批量操作

| 按钮 | 效果 |
|---|---|
| **全部从 REA 拉取** | 把所有有 URL 的行依次抓取,实时更新 |
| **自动找可比** | 用下方过滤器自动搜 REA,候选 URL 列出来供你 review |
| **全部地理编码** | 把所有未地理编码的地址批量发给 Google |
| **全部显示: ✓🛏 ✓🛁 ✓🚗 ✓⬚ ✓⬚** | 一键开关所有行的 床/卫/车/地/建 显示 |

#### 自动找可比过滤器

下方的 **自动找过滤器** 控制 REA 搜索范围:

| 过滤器 | 说明 |
|---|---|
| **🛏 床数 min - max** | 卧室数范围;默认按目标房源预填 |
| **🛁 卫数 min - max** | 浴室数范围;默认按目标房源预填 |
| **🚗 车位 min - max** | 车位范围;默认按目标房源预填 |
| **土地 m² min - max** | 土地面积范围(仅 House / Land) |
| **建筑 m² min - max** | 建筑面积范围(仅 House) |
| **额外区** | 在目标房源所在区之外额外搜索的区 |
| **距离 km 半径** | 限制结果在距目标房源 N km 内 |

所有过滤器都可选 —— 取消勾选即禁用。在结果面板 review 候选 URL 后点 **导入选中** 加入列表。

#### 单行控制

- **照片条** —— 最多 5 张缩略图;点任意一张设为卡片照片;或自己上传
- **重新拉取** —— 重新从 REA 抓,覆盖所有字段
- **X 按钮** —— 软删除带 5 秒 **撤销** 窗口;后续编号立即更新
- **拖拽手柄** —— 重排,编号 + 地图标记实时更新

#### 编号

成交和在售共用 **一个连续序列**:7 个成交(1-7),在售从 8 开始。删除 / 重排自动重新编号。

### 第 5 步 — 可比在售

布局跟第 4 步一样。在售特有字段:

| 字段 | 说明 |
|---|---|
| **挂牌日期** | ISO 日期 YYYY-MM-DD |
| **挂牌价格** | 显示字符串,如 "$700,000" 或 "Contact Agent" |
| **市场天数** | 按挂牌日期计算,或手动输入 |

### 第 6 步 — 报告详情

| 字段 | 说明 |
|---|---|
| **中介姓名** | 从 PDF 预填,出现在封面 |
| **机构名** | 从 PDF 预填 |
| **中介邮箱** | 从 PDF 预填 |
| **报告日期** | 从 PDF 预填("Prepared on …") |
| **标记大小** | 小 / 中 / 大 —— 影响所有地图标记 |
| **包含地图** | 开关:总览图、成交图、在售图 |

### 生成 PDF

点 **生成 PDF**。仅在以下条件全部满足时按钮才可点:
- 已加载 PDF session
- 已填 Google Maps API Key
- 已填目标房源地址
- 所有可比行已拉取或填完(无 loading / error 状态)
- 至少有一个可比

**生成中**,进度条显示当前步骤和剩余时间:

```
渲染可比成交 (3/7)...                  剩余 ~14s    42%
████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░
```

**完成后**,绿色结果框出现 **下载** 链接。生成的 PDF:

- 替换封面页(新封面图、新中介信息块)
- 更新封面信(原中介信息抹掉、替换)
- 替换可比地图页(全新 Google 静态地图)
- 替换可比详情页(每页 5 卡,全部新数据)
- 其他页面跟原 PDF 完全一致

---

## 架构

> 完整模块说明 + PDF 生成管线 + REA 爬虫数据流详见 [英文版 README](README.md#architecture)。

简要:
- `backend/app.py` — FastAPI 服务器(HTTP + SSE 进度 + heartbeat)
- `backend/scrapers/rea.py` — Patchright 爬虫(ArgonautExchange → JSON-LD → HTML 兜底)
- `backend/generators/` — Pydantic 模型 + 样式 + 渲染器(封面 / 地图 / 卡片)+ 拼接器
- `backend/utils/` — PDF 解析、图像提取、PyMuPDF 视觉排序、距离计算、Google 静态地图
- `frontend/index.html` — 单页应用(原生 JS,无构建步骤)

---

## API 参考

> 完整 API 字段定义 + 响应示例详见 [英文版 README](README.md#api-reference)。

主要端点:

| 类别 | 端点 |
|---|---|
| Session | `POST /api/session` 上传 PDF;`POST /api/upload-hero` / `POST /api/upload-comparable-thumb` |
| 抓取 / 地理 | `POST /api/scrape` 抓 REA URL;`POST /api/geocode` / `POST /api/geocode-batch` |
| 生成 | `POST /api/generate`(SSE 流);`GET /api/download/{session_id}`;`GET /api/thumb/{token}` |
| 维护 | `POST /api/heartbeat`;`GET /api/health` |

---

## 打包分发

### 构建发布版

```bat
build_release.bat
```

在项目目录生成 **`CMA-Editor.zip`**(~3-5 MB)。排除:`.git`、`.venv`、`uploads/`、`output/`、`.tmp/`、调试脚本、PDF。

### 收件人怎么用

1. 解压到任意目录,如 `C:\CMA-Editor`
2. *(可选)* 双击一次 `create_shortcut.bat` 创建桌面快捷方式
3. 双击 `start.bat` —— 首次自动装好一切

**首次启动:** 2-5 分钟(装包 + 下载 Chromium ~200 MB)
**后续启动:** < 5 秒

### 自动更新行为

`start.bat` 每次启动哈希 `requirements.txt`,跟 `.venv\.installed` 里存的哈希对比。哈希变了(新版本加了包)pip 自动更新,Chromium 不重下。

---

## 故障排查

### "Scrape failed: Could not find ArgonautExchange JSON"
REA 页返回 CAPTCHA 或反机器人拦截。
- 等 5 分钟再试
- 浏览器里确认 URL 还在
- 本地家用 IP 上很少见

### 自动找返回 "HTTP ERROR 429" / 限流
REA 临时拦了爬虫(请求太密)。
- 爬虫自动 15 + 45 秒重试 —— 等就行
- 如果反复出现,过几分钟再用自动找
- **持久 profile 登录** 减少 429:首次自动找时浏览器会打开 —— 在那个窗口登录 realestate.com.au。会话保存到 `~/.cma_rea_profile`,后续每次复用,爬虫表现得像正常登录用户
- 想禁用持久 profile,启动前设环境变量 `CMA_CHROME_PROFILE=`(空)

### "地图显示灰色占位"
- Google Maps API Key 无效,或 Maps Static API 未启用
- 验证:[console.cloud.google.com/apis/library/static-maps-backend.googleapis.com](https://console.cloud.google.com/apis/library/static-maps-backend.googleapis.com)

### "上传失败: Failed to fetch"
服务器没启起来。看应用目录下 `server.log` 找 Python traceback。

常见原因:
- Python 不在 PATH → 重装 Python 勾选 "Add to PATH"
- 8000 端口被占 → `start.bat` 会自动尝试杀,失败就重启电脑
- 缺包 → 删 `.venv\` 重跑 `start.bat`

### "可比距离显示 —"
可比或目标房源缺经纬度。点 **⊕ 自动填坐标** 或在可比 tab 用 **全部地理编码**。

### "生成卡住 / 超时"
- 重启服务器(关终端,重跑 `start.bat`)
- 看 `server.log` 找卡在哪步的 traceback

### 打包版本读不出 PDF 数据
确认用的是 `pymupdf` 加入 `requirements.txt` 之后的版本。删 `.venv\` 重跑 `start.bat` 强制重新安装。

---

## 调试爬虫

要看 REA 给某个房源返回了什么数据,在项目目录跑:

```bat
.venv\Scripts\python debug_scrape.py "https://www.realestate.com.au/property-house-nsw-..."
```

输出 `debug_raw.json` 是完整 ArgonautExchange JSON —— 用来诊断为什么某个字段抽不出来。

---

## 依赖

| 包 | 版本 | 用途 |
|---|---|---|
| `fastapi` | ≥0.110 | HTTP 服务器框架 |
| `uvicorn[standard]` | ≥0.27 | ASGI 服务器 |
| `python-multipart` | ≥0.0.9 | 文件上传 |
| `pydantic` | ≥2.6 | 请求 / 响应验证 + 数据模型 |
| `requests` | ≥2.31 | HTTP 下载(缩略图、地图) |
| `beautifulsoup4` | ≥4.12 | HTML 解析兜底 |
| `patchright` | ≥1.42 | Playwright fork —— 绕过 REA 反爬 |
| `pypdf` | ≥4.0 | PDF 文本提取 + 页面拼接 |
| `pymupdf` | ≥1.23 | 位置感知图像提取 |
| `reportlab` | ≥4.1 | PDF 页生成(可比卡片、地图、封面) |
| `Pillow` | ≥10.2 | 图像处理、格式转换、缩略图 |

### 外部服务

| 服务 | 用于 | 必需 |
|---|---|---|
| Google Maps Static API | 地图图块 | 是,需要地图 |
| Google Maps Geocoding API | 地址 → 坐标 | 是,需要地理编码 |
| realestate.com.au | 房源实时数据 | 可选(手动输入也能离线工作) |

---

## License

私有 —— 仅供 VPI Group 内部使用。
