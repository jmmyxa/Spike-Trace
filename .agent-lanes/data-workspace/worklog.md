# data-workspace 工作日志

## 2026-08-10 - 第一阶段：仓库审计与设计

### 重要指令

- 来源任务：`019fbd2c-10ae-78a2-90e4-79c173424ff2`。
- 注册键与 Lane 标识：`data-workspace`；因 Windows 脚本编码兼容，工作日志固定为 `.agent-lanes/data-workspace/worklog.md`，不得使用中文目录名。
- Lane 职责：数据存储、账户与工作区数据契约、账户/工作区页面；保存比赛、视频、标注版本、模型评估和球员统计；支持 CSV/JSON 导入导出。
- 第一阶段只做审计与设计，不修改共享运行时代码。
- 唯一写入范围：`docs/data-platform/**`、`src/spiketrace/storage/**`、`tests/storage/**`、`frontend/src/features/workspace/**` 和本工作日志。
- 边界：全局导航以及分析/标注体验由“产品规划与前端体验” Lane 负责。

### 仓库审计

- 当前项目是 Python 3.10+ 的本地 CLI/模块化单体，没有数据库、HTTP API、账户系统或前端工程。
- 当前持久化以文件为中心：标注 CSV、比赛/复核/补标 JSON、训练配置与指标 JSON、PyTorch checkpoint、推理事件 JSON/CSV、评估 JSON/CSV、视频与工作簿文件。
- 稳定契约包括七类动作标签和 `ACTION_LABEL_SCHEMA_VERSION = 2`、checkpoint `format_version = 1`、事件输出 `format_version = 1`、复核规格/结果 `format_version = 1`。
- 标注 CSV 使用 UTF-8 BOM 与 CRLF；Git 属性固定换行，以保证跨设备 SHA-256 稳定。训练/验证/测试按完整视频隔离。
- 复核结果保留来源快照，禁止覆盖输入；新 CSV 在同目录临时写入、重新加载验证后原子替换。
- 补标规格和结果通过来源 manifest/workbook SHA-256 锁定谱系；旧 manifest 保持不可变，新结果写入新文件。
- 当前只有一场比赛、90 条训练窗口，全部属于 `train`；球员号码字段存在但为空，尚无 roster、稳定球员 ID 或正式统计事实。
- `docs/PROJECT_PLAN.md` 已确认长期采用本地优先架构：SQLite 保存结构化数据，视频和证据文件留在文件系统；原始预测与人工修正同时保留；统计从事件重算。

### 初步决策与待确认设计

- 以“SQLite 元数据/事务 + 内容寻址文件资产 + 可移植导入导出包”为推荐路线；不采用纯 JSON 目录作为长期事实库，也不在第一版引入云端 Postgres/微服务。
- `workspace_id` 作为所有业务数据的强制租户边界。第一版允许本机单用户自动进入默认工作区，同时保留账户、成员和角色表，为以后登录与协作升级留接口。
- 标注采用不可变版本与显式父版本，不原位改写；预测、人工确认和统计投影分层保存。
- 账户/工作区页面只负责个人偏好、工作区资料、成员/权限占位、存储用量、导入导出和数据维护；不承接全局导航或分析/标注工作台。

### 已检查文件

- `README.md`
- `docs/PROJECT_PLAN.md`
- `docs/superpowers/specs/2026-08-04-receive-dig-second-review-design.md`
- `src/spiketrace/domain.py`
- `src/spiketrace/manifest.py`
- `src/spiketrace/review.py`
- `src/spiketrace/training.py`
- `src/spiketrace/ml.py`
- `src/spiketrace/pretrained.py`
- `src/spiketrace/events.py`
- `src/spiketrace/inference.py`
- `src/spiketrace/outputs.py`
- `data/annotations/*.csv`
- `data/annotations/*.json`
- `tests/test_*.py`

### 验证

- `git status --short --branch`：审计开始时为 detached `HEAD`，无本地改动。
- `python -m unittest discover -s tests -v`：未运行；当前 PowerShell 的 `python.exe` 指向 Microsoft Store 应用别名，未找到可用解释器。
- 当前工作树未包含上游新建的 `agent-lanes.md` 或已有工作日志；按用户最新直接指令在本工作树创建本日志，没有创建或修改 Lane 注册表。
