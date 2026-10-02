# AbeCloud Auto-Grab（阿贝云自动抢购 · GitHub Actions 版）

> **本文档的用途**：交给 AI，让它据此生成完整的 Python 项目，
> 部署在 GitHub Actions 上定时运行。
> 文档只描述**设计规格、接口约定、行为要求**，不含实现代码。

---

## 0. 给 AI 的总体指令（重要）

**请你扮演一名资深 Python 工程师，按照本文档构建一个可在 GitHub Actions
上稳定运行的自动抢购脚本。**

1. **语言**：Python 3.10+。
2. **风格**：PEP 8，模块化，函数职责单一。
3. **注释**：所有公开函数写 docstring。
4. **类型标注**：所有函数签名带完整类型标注。
5. **错误处理**：不允许静默失败；所有异常必须捕获、记录、必要时降级。
6. **架构约定**：模块划分、函数签名、环境变量名，请**严格按本文档执行**。
7. **可运行优先**：先保证端到端能跑通，再考虑优化。
8. **不上传敏感信息到仓库**：所有凭据走 GitHub Secrets。
9. **GitHub Actions 兼容**：代码必须能在无显示器、无持久状态的 Actions 环境运行。

---

## 1. 项目目标

在 GitHub Actions 上定时运行一个自动抢购程序：

- **每 10 分钟**触发一次（由 workflow 的 cron 控制）
- 每次运行时**尝试登录阿贝云并抢购免费服务器**
- **抢购成功** → 通过 PushPlus 推送消息 → **停止后续所有执行**
- **抢购失败** → 安静退出，等下一次调度
- **未到开抢时间** → 快速退出，节省 Actions 额度

**核心机制**：

- **无状态环境下的状态持久化**：GitHub Actions 每次运行都是全新容器，
  必须通过**外部存储**（仓库文件 / Actions cache / GitHub API）保存"是否已成功"
- **成功后自动停止**：通过**禁用 workflow**（主方案）+ **state 文件标记**（兜底方案）
  实现"不再执行"
- **快速退出**：未到开抢时间时不启动浏览器，用一次轻量 HTTP 请求判断

---

## 2. 合规与风险提醒（必读）

**给 AI 的提示**：以下内容必须在 README 中显著提示用户。

**本项目仅用于个人正常领取阿贝云官方提供的免费服务器**。使用者需自行确保：

1. 遵守阿贝云的**服务条款**和**活动规则**
2. 不进行**高频请求**或**并发多账号**刷取
3. 不将免费服务器用于**商业用途**（阿贝云明确禁止）
4. 不将本项目用于**代抢、倒卖**或其他违反平台规则的行为

**风险提示**：

- **账号风险**：自动化登录可能触发平台风控，导致账号被封禁或限制
- **IP 风险**：GitHub Actions 的公网 IP 段可能被阿贝云识别并限制
- **验证码风险**：如果阿贝云登录或抢购环节需要验证码，本项目**无法工作**
- **合规风险**：若阿贝云明确禁止自动化操作，使用本项目可能违反 TOS

**使用前必须自行评估风险，本项目作者不承担任何后果**。

---

## 3. GitHub Actions 的限制（重要，必须先理解）

**给 AI 的提示**：这些限制直接影响项目设计，必须在 README 中说明。

### 3.1 定时精度

- GitHub Actions 的 cron **最小间隔 5 分钟**
- 实际执行时间**不保证精确**，高峰时段可能延迟 5-15 分钟
- 极端情况下，某些时间点可能**被跳过**
- 本项目用 `*/10` 触发，实际可能每 10-25 分钟执行一次

**结论**：本项目**不适合抢秒杀型**活动。仅适用于"持续开放、随时可领"的场景。

### 3.2 免费额度

- **公开仓库**：Actions 分钟数**无限**（推荐）
- **私有仓库**：每月 2000 分钟免费额度

**本项目每分钟消耗的估算**：

- 未到开抢时间（快速退出）：约 5-10 秒
- 到开抢时间（启动 Playwright）：约 30-60 秒
- 每 10 分钟触发一次，每天 144 次

**私有仓库下**：
- 假设每次平均 30 秒，每天 144 次 = **72 分钟/天 = 2160 分钟/月**
- **会超出 2000 分钟免费额度！**

**强烈建议用公开仓库**。若必须用私有仓库：
- 降低频率（例如每 30 分钟一次）
- 缩短单次执行时间（快速退出优化）
- 购买额外分钟数

### 3.3 60 天无活动自动禁用

GitHub 规定：**仓库 60 天无任何 push 或 workflow 活动时，
所有 scheduled workflow 会被自动禁用**。

**后果**：如果抢购一直没成功，60 天后定时任务会自动停止。

**解决方案**：
- 定期手动 commit 一次（例如每月一次）
- 或者配置一个"心跳"workflow，每次运行结束时 commit 一个心跳文件
- 或者接受"60 天后需手动重启"

**本项目采用**：每次运行结束时（未成功时），**静默更新一个心跳文件并 commit**，
避免 60 天禁用。

### 3.4 并发与排队

- 同一 workflow 的多次运行**默认可以并发**
- 若单次运行超过 10 分钟，下一次会被触发 → 可能导致重叠

**解决方案**：在 workflow 中配置 `concurrency`，避免重叠。

### 3.5 无持久状态

GitHub Actions 每次运行都是**全新容器**，无法依赖本地文件保存状态。

**本项目采用的外部存储方案**（按优先级）：

1. **禁用 workflow**（最可靠，成功后立即生效）
2. **state 文件提交到仓库**（兜底）
3. **Actions cache**（不推荐，有并发问题）

---

## 4. 技术栈建议（给 AI 的提示）

**推荐使用的 Python 库**：

- 浏览器自动化：**Playwright**（Chromium，headless）
- HTTP 请求：**requests**（用于快速退出判断和 API 调用）
- 配置：环境变量 + 简单 YAML（可选）
- 命令行：**argparse**
- 日志：标准库 **logging**
- 时间处理：**python-dateutil**
- GitHub API 调用：**requests**（无需 PyGithub，减少依赖）

**不建议使用**：Selenium（Actions 上启动慢）、PhantomJS（已废弃）。

**给 AI 的提示**：

- 优先用 requests 做轻量判断，只有必要时才启动 Playwright
- Playwright 在 Actions 上首次启动约 5-10 秒，尽量避免频繁启动

---

## 5. 目录结构（严格遵循）

项目根目录名为 `abecloud-grab`：

**根目录文件**：

- `main.py`：程序入口
- `config.yaml`：非敏感配置（可选）
- `config.example.yaml`：配置模板
- `requirements.txt`
- `README.md`
- `.gitignore`

**`.github/workflows/` 目录**：

- `grab.yml`：定时抢购 workflow
- `manual.yml`（可选）：手动触发 workflow，用于测试

**`modules/` 目录**：

- `__init__.py`
- `config.py`：配置加载（环境变量优先）
- `logger.py`：日志封装
- `auth.py`：登录
- `grabber.py`：抢购核心逻辑
- `state.py`：状态持久化（GitHub API 或 git commit）
- `notify.py`：PushPlus 通知
- `browser.py`：Playwright 工厂
- `utils.py`：通用工具

**`state/` 目录**：

- `grab_state.json`：持久化状态（提交到仓库）

**`logs/`、`screenshots/`**：运行时生成（不提交到仓库）

---

## 6. 配置规范

### 6.1 环境变量（GitHub Secrets）

**所有敏感信息必须通过 GitHub Secrets 注入**，不写入代码或配置文件。

**必须配置的 Secrets**：

- `ABECLOUD_USERNAME`：阿贝云账号（手机号或邮箱）
- `ABECLOUD_PASSWORD`：阿贝云密码
- `PUSHPLUS_TOKEN`：PushPlus 用户 token

**可选配置的 Secrets**：

- `PUSHPLUS_WECHAT_ENABLED`：是否启用微信渠道（默认 true）
- `PUSHPLUS_QQ_ENABLED`：是否启用 QQ 渠道（默认 false）
- `PUSHPLUS_SMS_ENABLED`：是否启用短信渠道（默认 false）

**GitHub 自动注入的环境变量**（无需配置）：

- `GITHUB_TOKEN`：Actions 自动注入，用于提交 state 文件和禁用 workflow
- `GITHUB_REPOSITORY`：格式 `owner/repo`
- `GITHUB_WORKFLOW`：workflow 文件名
- `GITHUB_REF`：分支引用

**给 AI 的提示**：

- 所有敏感信息通过 `os.environ` 读取
- 缺失必填环境变量时抛错并退出码 5
- 环境变量值不写入日志

### 6.2 非敏感配置（config.yaml，可选）

**给 AI 的提示**：如果用户希望简化，可以完全省略 config.yaml，
所有配置走环境变量。配置文件仅存放非敏感的调优参数。

**可配置项**：

- `abecloud.login_url`：阿贝云登录页 URL
- `abecloud.grab_url`：抢购页 URL
- `abecloud.target_plan`：目标套餐（如"免费套餐"）
- `abecloud.headless`：默认 true
- `abecloud.timeout_ms`：默认 60000
- `abecloud.work_hours_only`：默认 false
- `abecloud.work_hour_start`：默认 9
- `abecloud.work_hour_end`：默认 22
- `abecloud.timezone`：默认 `Asia/Shanghai`

- `pushplus.api_url`：默认 `https://www.pushplus.plus/send`
- `pushplus.timeout`：默认 10 秒

- `state.enable_git_commit`：默认 true。是否提交 state 文件
- `state.enable_disable_workflow`：默认 true。成功后是否禁用 workflow
- `state.heartbeat_on_failure`：默认 true。失败时是否更新心跳文件

- `output.log_dir`：默认 `logs`
- `output.screenshot_dir`：默认 `screenshots`
- `output.keep_screenshots`：默认 5

---

## 7. 模块规格（接口契约）

### 7.1 `modules/config.py`

**职责**：加载配置。

**对外函数**：

- `load_config()`：从环境变量 + 可选 config.yaml 加载，返回 Config 对象
- `validate_config(cfg)`：返回 `(bool, errors_list)`

**给 AI 的提示**：

- 环境变量优先于 config.yaml
- 缺失必填项时列出所有缺失项并退出

### 7.2 `modules/logger.py`

**职责**：统一日志输出。

**对外函数**：

- `setup_logger(log_dir, level)`：返回根 logger

**行为**：

- 输出到 stdout（GitHub Actions 会捕获）
- 可选输出到 `logs/grab_YYYYMMDD.log`
- 格式：`[时间] [级别] [模块] message`
- 自定义 `SUCCESS`（25）和 `SKIP`（15）级别
- **自动脱敏**：账号、密码、PushPlus token、Cookie 值、GITHUB_TOKEN

**脱敏规则**：

- 匹配 40 字符以上的 base64 字符串 → `***`
- 匹配 `token=xxx` → `token=***`
- 匹配 `password=xxx` → `password=***`
- 账号只显示前 3 位 + `***`

### 7.3 `modules/auth.py`

**职责**：登录阿贝云。

**对外函数**：

- `login(username, password, config)`：返回登录状态对象
  - 成功：`{success: True, cookies: dict, context_state: dict}`
  - 失败：`{success: False, reason: str, error: str}`
- `is_logged_in(context, config)`：检查当前是否已登录
- `detect_captcha(page)`：检测是否有验证码

**登录流程**：

1. 打开登录页
2. 等待表单加载
3. 填写账号密码
4. 点击登录
5. **检测验证码**：若出现图形验证码/滑块 → 返回失败，reason=`captcha`
6. 等待跳转
7. 检测登录成功：URL 跳转出登录页，或页面出现用户名

**给 AI 的提示**：

- 若遇验证码，**立即失败退出**，不尝试绕过
- 登录失败原因分类：`captcha` / `wrong_credentials` / `network` / `unknown`
- 每次登录用**全新的浏览器上下文**（Actions 无持久 Cookie）

### 7.4 `modules/grabber.py`

**职责**：抢购核心逻辑。

**对外函数**：

- `check_availability(config)`：**轻量检查是否可抢**
  - 用 requests + 登录 Cookie GET 抢购页
  - 返回 `{available: bool, reason: str}`
  - 不启动 Playwright
- `attempt_grab(context, config)`：**实际抢购**
  - 打开抢购页
  - 找到目标套餐
  - 点击"立即领取"或类似按钮
  - 处理确认弹窗
  - 等待结果
  - 返回 `{success: bool, reason: str, detail: str, screenshot: str}`
- `verify_success(page, config)`：**验证抢购是否成功**
  - 检查页面是否出现"领取成功"、"已到账"、"恭喜"等文本
  - 检查是否跳转到成功页
  - 可选：访问个人中心确认实例列表

**抢购成功判定（给 AI 的关键提示）**：

成功判定必须有**多重验证**，避免误判：

1. 页面上出现成功文案（如"领取成功"）
2. AND 页面不再出现"已抢完"、"今日已结束"等失败文案
3. AND 抢购按钮变为"已领取"或消失

**失败判定**：

- 页面出现"已抢完"、"库存不足"、"今日已结束"
- 按钮 disabled
- 提交后返回错误信息

**未知状态**：

- 页面结构变了、无匹配文案 → 保存截图，reason=`unknown`
- 下次继续尝试，避免误判

**给 AI 的提示**：

- 所有选择器定义为多重 fallback
- 抢购后等待 3-5 秒再判断结果
- 失败时保存截图到 `screenshots/failed_<时间戳>.png`

### 7.5 `modules/state.py`

**职责**：状态持久化。

**对外函数**：

- `load_state()`：从 `state/grab_state.json` 加载
- `save_state(state)`：保存到本地文件
- `mark_success(detail)`：标记成功，返回新 state
- `mark_failure(reason, error)`：标记失败
- `update_heartbeat()`：更新心跳时间（防 60 天禁用）
- `is_already_success()`：检查是否已成功过
- `commit_state(message)`：**通过 git commit 提交 state 文件**
- `disable_workflow()`：**通过 GitHub API 禁用 workflow**

**state 文件结构**：

- `success`：布尔值。是否已成功抢购
- `success_time`：成功时间（ISO 8601）
- `success_detail`：成功详情（如实例 ID、套餐名）
- `last_attempt_time`：上次尝试时间
- `last_result`：上次结果（`success` / `failed` / `unknown` / `skipped`）
- `attempt_count`：累计尝试次数
- `heartbeat_time`：心跳时间

**commit_state 实现（给 AI 的提示）**：

两种实现方式，任选其一：

**方式 A：git 命令行**

- 设置 git 用户信息（用 GitHub Actions 默认）
- `git add state/grab_state.json`
- `git commit -m "Update grab state: <message>"`
- `git push`
- 需要 workflow 配置 `permissions: contents: write`

**方式 B：GitHub API**

- `GET /repos/{owner}/{repo}/contents/state/grab_state.json` 获取当前文件的 SHA
- `PUT /repos/{owner}/{repo}/contents/state/grab_state.json` 上传新内容
- 需要 `permissions: contents: write`
- 优点：不依赖 git 工作流，更简洁

**推荐方式 B**（GitHub API），因为更快、更可靠。

**disable_workflow 实现（给 AI 的提示）**：

- 调用 GitHub API：`PUT /repos/{owner}/{repo}/actions/workflows/{workflow_id}/disable`
- `workflow_id` 从环境变量 `GITHUB_WORKFLOW_REF` 提取文件名
- 需要 `permissions: actions: write`
- 成功后输出日志"Workflow disabled, no more scheduled runs"

**给 AI 的提示**：

- state 文件损坏时按"未成功"处理
- 首次运行 state 文件不存在时创建默认值
- **disable_workflow 失败时不阻塞**，仅记录日志
- 成功后必须**同时**做 state 标记和 disable workflow（双保险）

### 7.6 `modules/notify.py`

**职责**：PushPlus 通知。

**对外函数**：

- `send_success(detail, config)`：成功通知
- `send_failure(reason, error, config)`：失败通知（可选）
- `send_skip(reason, config)`：跳过通知（可选）
- `send_raw(title, content, channel, config)`：底层发送

**通知文案规范（给 AI 的关键提示）**：

**短信渠道（只发 title）**：

| 事件 | 标题 |
|------|------|
| 抢购成功 | `阿贝云免费服务器抢购成功` |
| 抢购失败（持续失败，可选） | `阿贝云免费服务器抢购失败` |
| 登录失败 | `阿贝云免费服务器抢购失败-登录失败` |
| 验证码拦截 | `阿贝云免费服务器抢购失败-需要验证码` |

**注意**：短信标题严格控制在**阿贝云免费服务器抢购成功/失败**主体，
失败时加简短阶段名（不超过 8 字），不加时间戳。

**微信 / QQ 渠道（markdown 全面内容）**：

**成功通知**：

- 标题：`[阿贝云] 抢购成功`
- 正文包含：
  - 事件：✅ 抢购成功
  - 时间（北京时间）
  - 目标套餐
  - 成功详情（如实例 ID）
  - 累计尝试次数
  - 服务器实例信息（如能从页面获取）
  - **提醒**：workflow 已自动禁用，如需再次抢购请手动启用

**失败通知**（可选，默认关闭避免噪音）：

- 标题：`[阿贝云] 抢购失败`
- 正文：失败原因、错误详情、尝试次数、下次重试时间

**给 AI 的提示**：

- 成功通知**必须发送**（除非用户明确禁用）
- 失败通知默认**关闭**（因为每 10 分钟一次，会产生大量通知）
- 只在连续失败 N 次后（例如 12 次 = 2 小时）才发送失败通知
- 通知失败不阻塞主流程

### 7.7 `modules/browser.py`

**职责**：Playwright 浏览器工厂。

**对外函数**：

- `launch_browser(config)`：上下文管理器
- `create_context(playwright, config)`：创建上下文

**Actions 环境适配（给 AI 的关键提示）**：

- **必须 headless**
- 启动参数添加 `--disable-dev-shm-usage --no-sandbox --disable-gpu`
- Actions 的 Ubuntu runner 需要 `playwright install-deps chromium`
- **不使用系统 Edge**（Actions 上没有）

### 7.8 `modules/utils.py`

**职责**：通用工具。

**包含**：

- 时间处理（北京时间）
- 工作时间窗口判断
- 清理旧截图
- 敏感信息脱敏
- JSON 读写

### 7.9 `main.py`

**职责**：程序入口。

**主流程（重要）**：

1. 解析命令行参数
2. 加载配置（环境变量 + config.yaml）
3. 初始化日志
4. **读取 state 文件**
5. **检查是否已成功**：
   - 若 `state.success == true` → 日志 SKIP，可选通知，退出码 0
6. **检查工作时间窗口**（若启用）：
   - 不在窗口内 → 日志 SKIP，退出码 0
7. **轻量检查可抢购性**（`grabber.check_availability`）：
   - 不可抢 → 更新心跳，退出码 0（不启动 Playwright）
8. **启动 Playwright**，登录阿贝云：
   - 登录失败 → 更新 state 记录失败原因，退出码 4（不发通知或按策略）
9. **尝试抢购**（`grabber.attempt_grab`）：
   - 成功 → 保存截图 → 发送 PushPlus 通知 → 更新 state → **禁用 workflow** → 退出码 0
   - 失败 → 更新 state → 更新心跳 → 退出码 0
   - 未知 → 保存截图 → 更新 state → 退出码 0
10. 清理临时文件

**命令行参数**：

- `--config PATH`：可选配置文件
- `--dry-run`：只登录和检查，不实际抢购
- `--check-only`：只做轻量检查，不启动浏览器
- `--test-notify`：只发送测试通知
- `--force`：忽略"已成功"标记，强制执行（用于调试）
- `--verbose`：DEBUG 日志
- `--version`

**退出码**：

- 0：成功、跳过、或正常失败（下次继续）
- 4：登录失败
- 5：配置错误
- 6：未知错误

**给 AI 的提示**：

- 除配置错误外，**其他情况均返回 0**，避免 GitHub 将 workflow 标记为失败
- 所有异常必须捕获，记录日志，返回 0

---

## 8. 状态持久化与停止机制（核心）

**给 AI 的提示**：这是本项目的关键设计。

### 8.1 双保险机制

**主方案：禁用 workflow**

- 抢购成功后立即调用 GitHub API 禁用当前 workflow
- 效果：后续所有 scheduled 触发都不会再执行
- 优点：立即生效，不消耗后续额度
- 缺点：需要 `actions: write` 权限；禁用失败时兜底方案接管

**兜底方案：state 文件**

- 抢购成功后更新 `state/grab_state.json` 的 `success: true`
- 每次运行开始时先读取，若为 true 则立即退出
- 优点：即使 workflow 未被禁用，也不会重复抢购
- 缺点：每次运行仍消耗几秒 Actions 额度

**两个方案同时启用**，任一成功即可保证"不再执行"。

### 8.2 心跳机制（防 60 天禁用）

GitHub 规定：**仓库 60 天无活动时，scheduled workflow 自动禁用**。

**解决方案**：

- 每次运行结束时（未成功时），更新 `state/grab_state.json` 的 `heartbeat_time`
- **每 N 次运行**（如每 24 次 = 4 小时）提交一次到仓库
- 避免每次运行都 commit（太频繁会污染 git 历史）
- 心跳 commit 的 message 统一为 `chore: heartbeat <时间>`

**给 AI 的提示**：

- 心跳提交频率可配置，默认每 6 小时一次
- 心跳提交不应触发 workflow 的重新运行（GitHub 默认不会）
- 心跳提交的 message 用 `[skip ci]` 后缀避免触发其他 CI

### 8.3 恢复机制

**若用户希望重新启用抢购**（例如阿贝云又开放了新的抢购）：

1. 手动编辑 `state/grab_state.json`，将 `success` 改为 `false`
2. 在 GitHub 仓库的 Actions 页面手动启用 workflow
3. 或在 Actions 页面手动触发一次 `workflow_dispatch`

**给 AI 的提示**：

- 提供 `--reset` 参数，重置本地 state（需手动 commit）
- 在 README 中详细说明恢复步骤

---

## 9. GitHub Actions 工作流配置

**给 AI 的提示**：请生成 `.github/workflows/grab.yml` 的完整内容。

### 9.1 Workflow 要点

**触发方式**：

- `schedule`：`*/10 * * * *`（每 10 分钟）
- `workflow_dispatch`：允许手动触发（用于测试）

**权限**：

```
permissions:
  contents: write   # 提交 state 文件
  actions: write    # 禁用 workflow
```

**并发控制**：

```
concurrency:
  group: grab
  cancel-in-progress: false
```

避免两次运行重叠。

**运行环境**：

- `runs-on: ubuntu-latest`
- Python 3.11（用 `actions/setup-python`）

**步骤**：

1. Checkout 仓库
2. 设置 Python
3. 缓存 pip 依赖（用 `actions/cache`）
4. 安装 Python 依赖
5. 安装 Playwright 和系统依赖
6. 运行 `python main.py`
7. 上传失败截图（用 `actions/upload-artifact`，仅失败时）

**环境变量**：

- 从 GitHub Secrets 注入 `ABECLOUD_USERNAME`、`ABECLOUD_PASSWORD`、`PUSHPLUS_TOKEN`
- `GITHUB_TOKEN` 自动注入

**给 AI 的提示**：

- **不要在 workflow 中直接写敏感信息**
- **不要配置 `continue-on-error`**，让失败可见
- **Playwright 安装系统依赖可能需要 sudo**：`playwright install-deps chromium`
- **pip 缓存**：用 `actions/cache` 或 `actions/setup-python` 的 `cache: pip`

### 9.2 手动触发 Workflow（可选）

提供 `.github/workflows/manual.yml`，只包含 `workflow_dispatch`，
用于测试和调试。

### 9.3 静默失败

由于抢购"失败"是正常情况（大部分时间都抢不到），
**不应让 workflow 显示为失败**（红色 X）。

**实现方式**：

- `main.py` 除配置错误外均返回 0
- workflow 不配置 `continue-on-error`
- 这样 Actions 页面只显示绿色的勾

---

## 10. 部署步骤

**给 AI 的提示**：请将以下步骤写成 README 的"部署指南"章节，
描述性文字为主，不含代码。

1. **Fork 或创建仓库**：建议用公开仓库（Actions 额度无限）
2. **上传项目文件**：包括 `.github/workflows/grab.yml`、`main.py`、`modules/` 等
3. **配置 Secrets**：在仓库的 Settings → Secrets and variables → Actions 中添加
   `ABECLOUD_USERNAME`、`ABECLOUD_PASSWORD`、`PUSHPLUS_TOKEN`
4. **配置权限**：Settings → Actions → General → Workflow permissions
   选择 "Read and write permissions"
5. **测试运行**：在 Actions 页面手动触发一次（`workflow_dispatch`）
6. **查看日志**：确认登录成功、抢购逻辑正常
7. **启用定时任务**：等待下一次 `*/10` 触发，或手动启用
8. **验证 PushPlus**：确保能收到测试消息
9. **监控**：前几次运行观察日志，确认逻辑无误

---

## 11. 细节清单（易被忽略，请逐项确认）

**GitHub Actions 适配**：

- workflow 权限包含 `contents: write` 和 `actions: write`
- 配置 `concurrency` 避免重叠
- pip 依赖缓存
- Playwright 系统依赖安装
- 日志输出到 stdout（Actions 捕获）
- 失败截图上传为 artifact

**状态持久化**：

- state 文件首次运行自动创建
- 成功时同时更新 state 和禁用 workflow
- 心跳定期提交（防 60 天禁用）
- 心跳 commit 带 `[skip ci]` 后缀

**停止机制**：

- 成功 → 禁用 workflow（主方案）
- 成功 → state 标记（兜底）
- 每次运行先检查 state
- `--force` 可忽略 state（调试用）
- README 说明如何恢复

**登录**：

- 用全新浏览器上下文（无 Cookie 缓存）
- 检测验证码，遇验证码立即失败
- 登录失败原因分类
- 登录失败发送通知（可配置频率）

**抢购逻辑**：

- 轻量检查优先（requests）
- 只有可抢时才启动 Playwright
- 成功判定多重验证
- 失败原因分类
- 保存失败截图

**通知**：

- 成功通知必须发
- 失败通知默认关闭
- 连续失败 N 次后才发失败通知
- 短信标题严格规范
- 微信/QQ 内容全面
- 通知失败不阻塞

**脱敏**：

- 账号、密码、token、GITHUB_TOKEN 全部脱敏
- 日志中不出现完整凭据

**其他**：

- 时区处理正确（UTC 存储，北京时间展示）
- 工作时间窗口判断
- 清理旧截图
- 退出码规范

---

## 12. 常见问题

**Q1：Actions 每次运行都要重新登录吗？**

是的。GitHub Actions 每次运行都是全新容器，没有持久 Cookie。
所以每次都需要用账号密码登录。如果登录需要验证码，本项目无法工作。

**Q2：GitHub Actions 的定时精度如何？**

官方最小间隔 5 分钟，实际执行受队列影响，可能延迟 5-15 分钟。
**不适合抢秒杀型活动**，只适用于"持续开放"的场景。

**Q3：私有仓库的额度够吗？**

每 10 分钟一次，每次约 30 秒，每天约 72 分钟，每月约 2160 分钟，
**超出 2000 分钟免费额度**。**建议用公开仓库**。

**Q4：抢购成功了，为什么还在运行？**

可能原因：

- workflow 禁用失败（权限问题）
- state 文件未提交成功（权限问题）

**检查**：查看 `state/grab_state.json` 是否为 `success: true`。
**手动处理**：在 Actions 页面手动禁用 workflow。

**Q5：如何重新开始抢购？**

1. 编辑 `state/grab_state.json`，将 `success` 改为 `false` 并 commit
2. 在 Actions 页面启用 workflow

**Q6：60 天后定时任务不运行了？**

GitHub 会因仓库 60 天无活动而自动禁用。本项目有心跳机制定期 commit，
避免此问题。若仍发生，手动 push 一次即可恢复。

**Q7：阿贝云登录需要验证码怎么办？**

**本项目无法处理验证码**。若阿贝云启用了验证码，本项目会：
1. 登录失败，记录 `captcha` 原因
2. 可选：发送 PushPlus 通知提醒用户
3. 下次继续尝试（可能是偶发的风控）

如果验证码是**常态**，本项目不可用。

**Q8：Actions 的 IP 被阿贝云封了怎么办？**

GitHub Actions 使用公共 IP 段，可能被目标网站识别。
**解决方案**：
- 无法直接解决（Actions 无法指定固定 IP）
- 考虑改用有固定 IP 的服务器（如阿贝云自己的免费服务器）
- 或降低频率避免触发风控

**Q9：如何调试抢购逻辑？**

1. 在 Actions 页面手动触发（`workflow_dispatch`）
2. 查看运行日志
3. 若失败，下载 artifact 中的截图
4. 本地运行 `python main.py --dry-run` 逐步调试

**Q10：PushPlus 没收到消息？**

- 检查 token 是否正确
- 检查渠道是否启用
- 查看 Actions 日志中 PushPlus 响应
- 常见错误码：302（token 无效）、888（积分不足）

---

## 13. 版本历史

- 1.0.0：首个版本，支持 GitHub Actions 定时抢购、
  PushPlus 通知、成功自动停止、心跳防禁用

---

## requirements.txt（依赖清单）

**给 AI 的提示**：请生成 requirements.txt。

**必选依赖**：

- playwright：浏览器自动化
- requests：HTTP 请求（PushPlus、GitHub API、轻量检查）
- PyYAML：可选配置文件解析
- python-dateutil：时间处理

**可选依赖**：

- pytest、pytest-mock：测试