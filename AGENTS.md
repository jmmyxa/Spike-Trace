# Spike-Trace 工作规则

- 每次与 Spike-Trace 相关的聊天结束前，更新 `docs/PROJECT_STATUS.md`：记录日期、当前进度、实际验证结果、阻塞项、下一步，以及 Git 分支/远程同步状态。
- 需要提交或推送时，使用 `.agents/skills/push-project-status/SKILL.md` 的收尾流程；没有远程核验证据时，不得声称已同步。
- 只选择性暂存本次会话的文件，保留用户已有改动；不要使用 `reset`、`checkout`、`clean`、无授权强推或自动覆盖。
- 目录、模块或工作流发生变化时，同时更新 `README.md` 和 `docs/PROJECT_STATUS.md`。
- 用户中断或环境故障导致无法收尾时，下一次会话先检查未推送的状态日志，并在日志中补记原因。
