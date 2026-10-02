"""AbeCloud Auto-Grab 核心模块包。

模块划分（见项目 README / 设计文档第 5 节）：
- config:   配置加载（环境变量优先）
- logger:   日志封装（自定义级别 + 自动脱敏）
- auth:     登录
- grabber:  抢购核心逻辑
- state:    状态持久化（GitHub API / 本地 JSON）
- notify:   PushPlus 通知
- browser:  Playwright 浏览器工厂
- utils:    通用工具
"""

__version__ = "1.0.0"
