# Agent-Customer 构建脚本使用指南

本文档介绍 `scripts/` 目录下各个构建和使用脚本的详细功能和使用方法。

## 📁 目录结构

```
scripts/
├── __pycache__/          # Python 缓存目录
├── build_exe.py           # 完整打包脚本（功能丰富）
├── build_win_exe.py       # 简化打包脚本（快速构建）
├── install_playwright.py  # Playwright 浏览器安装脚本
├── test_voucher_dedup.py  # 核销流程防重复建单/防空参核销自测脚本
├── test_ship_confirm.py   # 发货「客户确认 → 真发货 → 必反馈」自测脚本
├── check_goods_config.py  # 商品验机配置缺口巡检（只读）
├── test_message_records.py# 消息记录（人工客服/AI）自测脚本
├── doctor.py              # 部署自检脚本（只读）
├── show_messages.py       # 聊天记录查看脚本（只读）
├── version_info.txt       # Windows 可执行文件版本信息
└── README.md              # 本使用说明文档
```

> ⚠️ **部署注意：必须整目录同步**
> 只拷贝部分目录（例如只覆盖 `Message/` 而没覆盖 `database/`）会造成
> "新版调用 + 旧版被调"的混合部署，典型症状是日志里出现
> `消息落库失败 … 'DatabaseManager' object has no attribute 'save_message'`，
> 消息记录会静默丢失。部署后用 `python scripts/doctor.py` 自检（退出码 0 才算好）。

## 🛠️ 脚本详细说明

### 1. build_exe.py - 完整打包脚本

**功能描述：**
使用 PyInstaller 将项目打包成独立的 Windows 可执行文件，支持完整的企业级打包功能。

**主要特性：**
- ✅ 自动设置 Python 虚拟环境
- ✅ 依赖检查和环境验证
- ✅ 支持发布和调试模式
- ✅ 自动创建分发包文件
- ✅ 生成 NSIS 安装包脚本
- ✅ 支持清理构建缓存
- ✅ 详细的错误处理和日志

**使用方法：**

```bash
# 基本用法
python scripts/build_exe.py

# 完整参数列表
python scripts/build_exe.py [选项]
```

**支持的参数：**
```bash
--python VERSION     # Python 版本，默认 3.11
--mode MODE          # 构建模式：release(默认) 或 debug
--clean              # 构建前清理缓存文件
--installer          # 生成 NSIS 安装包脚本
--check-only         # 仅检查依赖，不执行构建
```

**使用示例：**

```bash
# 标准构建
python scripts/build_exe.py

# 调试模式构建
python scripts/build_exe.py --mode debug

# 清理后重新构建
python scripts/build_exe.py --clean

# 生成安装包脚本
python scripts/build_exe.py --installer

# 仅检查依赖
python scripts/build_exe.py --check-only

# 完整构建流程（推荐）
python scripts/build_exe.py --clean --installer
```

**输出文件：**
- `dist/AgentCustomer.exe` - 主程序
- `dist/temp/` - 临时文件目录
- `dist/README.txt` - 用户说明
- `dist/run.bat` - Windows 批处理启动脚本
- `dist/config.json` - 配置文件模板
- `installer.nsi` - NSIS 安装包脚本（如果使用 --installer）

**前置要求：**
- 安装 `uv` 包管理器
- Windows 操作系统
- 项目根目录必须包含：
  - `app.py`
  - `config.json`
  - `icon/icon.ico`
  - `scripts/agent_customer.spec` (当前不存在，需要创建)
  - `requirements.txt`

---

### 2. build_win_exe.py - 简化打包脚本

**功能描述：**
快速构建 Windows 可执行文件的简化版本，适合快速测试和开发。

**主要特性：**
- ⚡ 快速构建，最小化配置
- 🔧 基础环境检查
- 📦 外置浏览器模式（减小包体积）

**使用方法：**

```bash
# 基本构建
python scripts/build_win_exe.py

# 指定 Python 版本
python scripts/build_win_exe.py --python 3.11
```

**输出文件：**
- `dist/AgentCustomer.exe` - 可执行文件

**前置要求：**
- 安装 `uv` 包管理器
- 项目基础依赖文件

---

### 3. install_playwright.py - Playwright 浏览器安装脚本

**功能描述：**
自动安装 Playwright 浏览器二进制文件和系统依赖，支持开发和打包环境。

**主要特性：**
- 🌐 自动安装 Chromium 和 Firefox
- 🔧 安装系统依赖
- ✅ 安装验证和测试
- 📂 智能路径检测

**使用方法：**

```bash
# 安装所有浏览器
python scripts/install_playwright.py
```

**安装内容：**
1. **Chromium 浏览器** - 主要用于 Web 自动化
2. **Firefox 浏览器** - 备用浏览器选择
3. **系统依赖** - 浏览器运行所需的系统库

**安装路径：**
- 开发环境：`.browsers/`
- 打包环境：可执行文件同目录的 `ms-playwright/`

**验证功能：**
脚本会自动测试浏览器是否能正常启动并访问网页。

---

### 4. test_voucher_dedup.py - 核销流程自测脚本

**功能描述：**
离线（不联网、使用临时 SQLite）验证核销流程：同一张卡券/同一个序列号重复提交不会重复建单，核销接口不会被空参数调用，失败时能区分是卡券问题还是序列号问题，图片识别路径与文本路径行为一致。

**覆盖场景：**
- 文本卡券 → 同卡券图片 → 序列号文本（只应产生 1 条订单、核销 1 次）
- 重复发同卡券/同序列号、核销失败后重发、超过续接窗口后重发
- 序列号/IMEI 纠正（文字与图片识别结果都算，沿用同一张单不新建单号）
- 卡券号纠正（打错/少位后重新发送按纠正处理，已核销成功的单永不改写）
- 卡券号位数规则：10-14 位（待补卡券的严格模式 10-13 位）、支持空格/连字符分组、排除手机号与 15 位 IMEI、排除订单号
- **分两次发（S19）**：客户先发新券、再补序列号（线上真实 BUG 的节奏）——序列号必须补到
  新券那一单里并用**新券**核销；若反过来先发序列号再发新券，也要能核销新券
- **端到端回归（S18）**：模拟"券只能用一次"的拼多多返回——第一次券成功后，同一序列号发新券
  必须另建单并用新券核销成功，接口调用顺序必须是 [第1张券, 新券]，客户收到的是核销成功而非核销失败
- **已核销成功后再来第二张新券**（同一台机器/同一序列号）：必须另建一单并用新券核销，
  绝不能拿旧券重复核销（旧券已使用，接口会返回核销失败）——文字与图片识别路径都覆盖
- **已核销成功的券重复提交**：不再重复调用核销接口，直接回复"已核销成功，无需重复核销"，
  不再误报"核销失败"
- 旧单还没成功（待核销/失败）时同序列号改发新券：按纠正处理，但核销必须用新券
- 服务器日志回放（2026-09-23 真实客户序列：两个 IMEI、无效 15 位数字、卡券图片 OCR）
- 提取逻辑样本：型号+序列号连写、关于本机 OCR、卡券截图 OCR

**使用方法：**

```bash
PYTHONIOENCODING=utf-8 python scripts/test_voucher_dedup.py
```

输出 ASCII 摘要与 RESULT: pass=x fail=y，失败时退出码非 0。

---

### 5. doctor.py - 部署自检脚本（只读）

**功能描述：**
一眼看出"运行中的代码是否完整"，专门用来排查混合部署导致的静默丢数据。

**检查内容：**
- 关键文件（`database/db_manager.py`、`database/models.py`、`Message/core/persistence.py`、
  `Agent/CustomerAgent/custom/session_manager.py`、`Channel/pinduoduo/core/pdd_message_handler.py`）
  的路径、修改时间、关键符号是否存在
- 运行时实例：DI 容器里的 `DatabaseManager` 是否有 `save_message`、`session_scope`
- 数据库：`message_records` / `agent_messages` 表是否存在、`agent_messages.source` 列是否存在
- 消息记录统计：`direction + msg_type` 分组计数

**使用方法：**

```bash
PYTHONIOENCODING=utf-8 python scripts/doctor.py
PYTHONIOENCODING=utf-8 python scripts/doctor.py --db ./temp/channel_shop.db
```

退出码：0 = 全部通过；1 = 存在问题（可直接用于部署脚本判断）。

---

### 6. show_messages.py - 聊天记录查看脚本（只读）

**功能描述：**
查看某个客户的消息记录，并区分**人工客服**与**AI 自动回复**。

**判读规则：**
- `message_records.direction=in` → 客户发送；`out` → 客服/程序发送
- `message_records.msg_type=mall_cs` → 本店人工客服发送
- `message_records.msg_type=mall_cs_control` → STOP AI / START AI 控制命令（仅审计）
- `message_records.msg_type=text` → 程序发送（AI 回复、核销引导语）
- `agent_messages.role=assistant` 且 `source=mall_cs` → 人工客服；`source=ai` → AI 自动回复
  （`role` 只有 user/assistant，来源必须看 `source`）

**使用方法：**

```bash
PYTHONIOENCODING=utf-8 python scripts/show_messages.py --uid 4251855160868 --limit 30
PYTHONIOENCODING=utf-8 python scripts/show_messages.py --stats
PYTHONIOENCODING=utf-8 python scripts/show_messages.py --uid 4251855160868 --only mall_cs
PYTHONIOENCODING=utf-8 python scripts/show_messages.py --uid 4251855160868 --db ./temp/channel_shop.db
```

---

### 7. test_message_records.py - 消息记录自测脚本

**功能描述：**
离线（不联网、使用临时 SQLite）验证消息记录链路：客户消息、人工客服消息、AI 回复是否正确落库，
重复推送是否会重复记录，混合部署时是否会静默丢数据。

**覆盖场景：**
1. 拼多多推送角色判定：`from.role=mall_cs` → MALL_CS，客户消息 → TEXT
2. 客户消息落库：`in / text`
3. 店家人工客服消息落库：`out / mall_cs` + AI 会话历史 `role=assistant, source=mall_cs`
4. AI 自动回复落库：`out / text` + 会话历史 `source=ai`
5. 同一条推送（同 msg_id）重复到达：只记 1 行
6. 程序自己发出的消息被平台回推（msg_id 不同、内容相同、120s 内）：只记 1 行
7. 混合部署负例：`DatabaseManager` 缺 `save_message` → 明确报错 + 降级写入成功，不静默丢数据
8. 控制命令 STOP AI / START AI：只写 `mall_cs_control` 审计行，不写入 AI 会话历史
9. 人工客服消息缺少客户 UID：不抛异常、不产生脏记录

**使用方法：**

```bash
PYTHONIOENCODING=utf-8 python scripts/test_message_records.py
```

输出 `RESULT: pass=x fail=y`，失败时退出码非 0。

---

### 8. version_info.txt - 版本信息配置

**功能描述：**
Windows 可执行文件的版本信息配置文件，用于 PyInstaller 打包。

**包含信息：**
- 公司名称：Agent-Customer
- 文件描述：电商AI客服助手
- 版本号：0.1.0.0
- 版权信息：Copyright (C) 2025 Agent-Customer Team
- 产品名称：Agent-Customer

**修改方法：**
如需修改版本信息，请编辑相应字段：
```python
StringStruct(u'FileVersion', u'0.1.0.0'),  # 修改版本号
StringStruct(u'CompanyName', u'你的公司名'),  # 修改公司名
```

---

## 🔄 构建流程推荐

### 开发阶段
```bash
# 1. 首次设置
python scripts/install_playwright.py

# 2. 快速测试构建
python scripts/build_win_exe.py
```

### 发布阶段
```bash
# 1. 检查依赖
python scripts/build_exe.py --check-only

# 2. 完整构建
python scripts/build_exe.py --clean --installer

# 3. 生成安装包（使用 NSIS）
# 打开 installer.nsi 文件，使用 NSIS 编译器生成安装包
```

### 调试阶段
```bash
# 调试模式构建
python scripts/build_exe.py --mode debug --clean
```

---

## ⚠️ 注意事项

### 依赖问题
- 确保所有必要的文件都存在于项目根目录
- `agent_customer.spec` 文件当前不存在，需要先创建
- 网络连接问题可能影响 Playwright 浏览器下载

### 构建问题
- 构建过程可能需要几分钟时间
- 如果构建失败，尝试使用 `--clean` 参数清理缓存
- 杀毒软件可能误报，需要添加白名单

### 平台支持
- 构建脚本主要针对 Windows 平台设计
- 在其他平台运行可能需要修改路径和命令

### 包大小
- 外置浏览器模式可显著减小可执行文件体积
- 用户需要单独安装浏览器或使用系统浏览器

---

## 🛠️ 故障排除

### 常见错误及解决方案

**1. uv 命令未找到**
```bash
# 解决方案：安装 uv
pip install uv
```

**2. 找不到 agent_customer.spec 文件**
```bash
# 需要先创建 PyInstaller spec 文件
# 可以使用以下命令生成模板：
pyi-makespec app.py --onefile --windowed --name AgentCustomer
```

**3. Playwright 浏览器安装失败**
```bash
# 检查网络连接
# 手动设置代理（如果需要）
set PLAYWRIGHT_DOWNLOAD_HOST=https://playwright.azureedge.net
python scripts/install_playwright.py
```

**4. 构建失败：找不到图标文件**
```bash
# 确保图标文件存在
icon/icon.ico
```

**5. 可执行文件运行报错**
```bash
# 检查依赖是否完整
# 使用调试模式查看详细错误
python scripts/build_exe.py --mode debug
```

---

## 📝 开发者信息

- **项目名称：** Agent-Customer
- **版本：** 0.1.0
- **描述：** 电商AI客服助手
- **主要技术栈：** Python, PyQt6, Playwright, OpenAI, LanceDB

---

## 📞 技术支持

如果遇到问题：
1. 查看本文档的故障排除部分
2. 检查项目的 GitHub Issues
3. 提交新的 Issue 并包含详细错误信息

---

*最后更新：2025年12月*