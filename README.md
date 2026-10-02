# AbeCloud Auto-Grab（阿贝云自动抢购 · GitHub Actions 版）

在 GitHub Actions 上每 10 分钟定时运行：登录阿贝云 → 抢购免费服务器 →  
成功后 PushPlus 推送并自动停止后续执行。

---

## ⚠️ 合规与风险提醒（使用前必读）

**本项目仅用于个人正常领取阿贝云官方提供的免费服务器。** 使用者需自行确保：

1. 遵守阿贝云的**服务条款**和**活动规则**
2. 不进行**高频请求**或**并发多账号**刷取
3. 不将免费服务器用于**商业用途**（阿贝云明确禁止）
4. 不将本项目用于**代抢、倒卖**或其他违反平台规则的行为

**风险提示：**

| 风险    | 说明                                  |
| ----- | ----------------------------------- |
| 账号风险  | 自动化登录可能触发平台风控，导致账号被封禁或限制            |
| IP 风险 | GitHub Actions 的公网 IP 段可能被阿贝云识别并限制  |
| 验证码风险 | 若登录或抢购环节需要验证码，本项目**无法工作**（遇码即停，不绕过） |
| 合规风险  | 若阿贝云明确禁止自动化操作，使用本项目可能违反 TOS         |

**使用前必须自行评估风险，本项目作者不承担任何后果。**

---

## 工作原理

```
cron */10 触发
   │
   ├─ state 已成功？ ──────────→ 跳过退出（兜底方案）
   ├─ 不在工作时间窗口？ ───────→ 跳过退出
   ├─ 轻量检查（requests）：不可抢 → 更新心跳退出（不启动浏览器）
   │
   └─ 启动 Playwright 登录
        ├─ 验证码 → 失败退出（发告警）
        ├─ 抢购 → 三重验证成功
        │    ├─ PushPlus 推送（微信/QQ markdown + 短信标题）
        │    ├─ state 标记 success=true 并 commit（兜底）
        │    └─ GitHub API 禁用 workflow（主方案）
        └─ 未抢到 → 记录失败 → 心跳 → 退出等下次
```

**成功后停止（双保险）：**

- 主方案：调 GitHub API 禁用 workflow，立即生效、不再消耗额度
- 兜底方案：state 文件 `success: true`，即使 workflow 仍在，每次运行秒级跳过

**心跳机制（防 60 天禁用）：** 未成功时每 6 小时 commit 一次  
`chore: heartbeat <时间> [skip ci]`，保持仓库活跃。

---

## GitHub Actions 的限制（必须了解）

1. **定时精度**：cron 最小间隔 5 分钟，实际可能延迟 5-15 分钟、极端时被跳过。  
   **不适合抢秒杀型活动**，仅适用于"持续开放、随时可领"的场景。
2. **免费额度**：公开仓库 Actions 分钟数无限（**推荐**）；  
   私有仓库每月 2000 分钟——本项目每 10 分钟一次约 2160 分钟/月，**会超**。  
   私有仓库需降频（如每 30 分钟）或购买额度。
3. **60 天无活动自动禁用**：本项目用心跳 commit 规避；若仍发生，手动 push 一次即可。
4. **无持久状态**：每次运行全新容器，每次都需账号密码登录。  
   **若登录需验证码，本项目不可用。**

---

## 部署指南

1. **创建仓库**：建议用公开仓库（Actions 额度无限），把本项目文件全部上传  
   （包括 `.github/workflows/grab.yml`）
2. **配置 Secrets**：仓库 Settings → Secrets and variables → Actions，添加：
   - `ABECLOUD_USERNAME`：阿贝云账号（手机号或邮箱）
   - `ABECLOUD_PASSWORD`：阿贝云密码
   - `PUSHPLUS_TOKEN`：PushPlus 用户 token（[www.pushplus.plus](https://www.pushplus.plus) 免费获取）
3. **（可选）配置渠道开关**：Settings → Secrets and variables → Actions →  
   Variables 添加 `PUSHPLUS_WECHAT_ENABLED` / `PUSHPLUS_QQ_ENABLED` /  
   `PUSHPLUS_SMS_ENABLED`（默认仅微信开启）
4. **配置权限**：Settings → Actions → General → Workflow permissions  
   选择 **"Read and write permissions"**（提交 state 与禁用 workflow 需要）
5. **测试通知**：Actions 页面手动触发 `Manual Test` workflow，  
   选 `test-notify` 模式，确认手机能收到消息
6. **测试运行**：再次手动触发，选 `dry-run` 模式，查看日志确认登录成功
7. **观察定时运行**：等待下一次 `*/10` 触发，观察日志
8. **（可选）调整配置**：复制 `config.example.yaml` 为 `config.yaml`，  
   修改抢购页 URL、套餐名、心跳间隔等非敏感参数后提交

> **注意**：`config.example.yaml` 中的 `login_url` / `grab_url` 为占位值，  
> 部署前请改成阿贝云实际页面地址，并按真实页面校准 `modules/auth.py`、  
> `modules/grabber.py` 中的选择器常量（选择器集中定义，均已注释标明）。

---

## 运行与调试（本地）

```bash
pip install -r requirements.txt
playwright install chromium

# 本地跑需要先设置环境变量（Windows PowerShell 示例）
set ABECLOUD_USERNAME=你的账号
set ABECLOUD_PASSWORD=你的密码
set PUSHPLUS_TOKEN=你的token

python main.py --dry-run          # 只登录和检查，不实际抢购
python main.py --check-only       # 只做轻量检查，不启动浏览器
python main.py --test-notify      # 只发送测试通知
python main.py --force            # 忽略已成功标记强制执行
python main.py --verbose          # DEBUG 日志
python main.py --version
```

**退出码**：0 成功/跳过/正常失败；4 登录失败；5 配置错误；6 未知错误。  
在 Actions 上除配置错误外均返回 0（"没抢到"是常态，不应把 workflow 标红）。

**单元测试**：

```bash
pip install pytest pyyaml
python -m pytest tests/ -q
```

---

## 抢购成功了怎么恢复 / 重新开始抢购？

**成功后想再抢（例如活动又开放）：**

1. 编辑 `state/grab_state.json`，把 `success` 改回 `false`（或本地跑  
   `python main.py --reset` 后手动 push）
2. 到仓库 Actions 页面，左侧选择 `Grab` workflow → 右上角 `···` →  
   **Enable workflow**
3. 也可直接手动 `Run workflow` 触发一次验证

**成功了却还在运行？**（检查双保险是否都生效）

- 查看 `state/grab_state.json` 是否已提交为 `success: true`
- 若否：多半是权限问题——确认 Workflow permissions 已设为  
  "Read and write permissions"
- 临时处理：Actions 页面手动 Disable workflow

---

## 常见问题

**Q：每次运行都要重新登录吗？**  
是的，Actions 容器无持久 Cookie。若登录需验证码则无法工作。

**Q：私有仓库额度够吗？**  
每 10 分钟一次约 2160 分钟/月，超出 2000 分钟免费额度。建议公开仓库。

**Q：登录需要验证码怎么办？**  
本项目遇验证码立即失败并告警（绝不绕过）。若验证码是常态，本项目不可用。

**Q：Actions 的 IP 被封怎么办？**  
无法指定固定 IP。可考虑改用有固定 IP 的服务器运行，或降低频率。

**Q：PushPlus 没收到消息？**  
检查 token 是否正确；查看 Actions 日志中 PushPlus 响应。  
常见错误码：302（token 无效）、888（积分不足）。

**Q：如何调试抢购逻辑？**  
Actions 页面手动触发 `Manual Test`（dry-run）→ 看日志 → 失败时下载  
artifact 中的截图 → 本地 `python main.py --dry-run --verbose` 逐步调试。

---

## 目录结构

```
abecloud-auto-grab/
├── main.py                      # 程序入口
├── config.example.yaml          # 配置模板（非敏感调优参数）
├── requirements.txt
├── .github/workflows/
│   ├── grab.yml                 # 定时抢购 workflow（*/10）
│   └── manual.yml               # 手动测试 workflow
├── modules/
│   ├── config.py                # 配置加载（环境变量优先）
│   ├── logger.py                # 日志 + 脱敏
│   ├── auth.py                  # 登录（验证码检测，遇码即停）
│   ├── grabber.py               # 抢购核心（轻量检查 + 三重验证）
│   ├── state.py                 # 状态持久化 + GitHub API
│   ├── notify.py                # PushPlus 三渠道通知
│   ├── browser.py               # Playwright 工厂
│   └── utils.py                 # 时间/脱敏/清理工具
├── state/grab_state.json        # 持久化状态（提交到仓库）
├── tests/                       # 单元测试（pytest）
└── logs/ screenshots/           # 运行时生成（不提交）
```

---

## 版本历史

- 1.0.0：首个版本。GitHub Actions 定时抢购、PushPlus 三渠道通知、  
  成功自动停止（禁用 workflow + state 双保险）、心跳防 60 天禁用
