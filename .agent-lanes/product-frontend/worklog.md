# product-frontend 工作日志

## Lane

- lane: `product-frontend`
- 职责：Spike-Trace MVP 工作流的产品规划与前端体验
- 当前阶段：仅审计与设计
- 本阶段允许写入：`docs/product/**` 和本工作日志

## 重要指令

- 用户将本 lane 指派为 MVP 工作流的产品规划与前端体验负责人，覆盖视频导入、标注复核、模型评估、球员统计和 CSV/JSON 导出。
- 用户明确要求第一阶段只做审计和设计，不改运行时代码。
- 用户把工作日志路径统一改为 `.agent-lanes/product-frontend/worklog.md`。
- 用户要求保留工作树里已经存在的有效进展，不要覆盖或重复。
- 用户提到注册表在主分支提交 `8e04d29`，本阶段未修改注册表。

## 工作树检查

- `docs/product/` 在本次开始时存在，但目录内尚无产品设计文件。
- `.agent-lanes/product-frontend/` 在本次开始时存在，但还没有可用的 worklog 内容。
- 当前工作树里没有 `agent-lanes.md`，因此本阶段以用户委派和 lane 本地日志作为事实来源。

## 审计证据

- 仓库基线是 Python CLI 流程，还不是完成态的 Web 或桌面应用。
- 已核对的用户侧命令位于 `src/spiketrace/cli.py`：`inspect-video`、`validate-manifest`、`train`、`infer`、`evaluate-pretrained`、`prepare-review`、`apply-review`。
- 已核对的契约：
  - `src/spiketrace/domain.py` 中的 `AnnotationRecord`、`VideoMetadata`、`ActionWindow`、`ActionEvent`
  - `src/spiketrace/review.py` 中的非破坏式复核队列与结果应用流程
  - `src/spiketrace/outputs.py` 中的 `events.json` 和 `events.csv`
  - `src/spiketrace/manifest.py`、`src/spiketrace/metrics.py`、`src/spiketrace/pretrained.py` 中的 schema 和指标钩子
- `docs/PROJECT_PLAN.md` 进一步确认：
  - 项目长期目标是本地排球比赛视频分析软件
  - 当前真正的 MVP 中心仍是模型与复核工作流，不是完整分析平台
  - 当前严格动作集是七类
  - 当前评估数据还不是独立比赛验证

## 设计决策

- 推荐产品形态：分析任务工作台，而不是完整平台。
- MVP 工作流中心：一个任务依次经过导入、复核、评估、事件查看、统计和导出。
- 产品状态定义为 UI / 工作流状态，不规定底层存储实现。
- 跨 Lane 接口只定义产物引用、provenance、schema 和状态表面，不决定内部算法或数据库。
- 身份补全只按可选事件增强来定义，不决定 OCR 或跟踪内部实现。
- MVP 球员统计先限定为动作计数，除非用户再扩展范围。

## 变更文件

- 新建 `docs/product/mvp-workflow-design.md`
- 新建 `.agent-lanes/product-frontend/worklog.md`

## 验证

- 已完成阶段一范围检查：没有修改运行时代码。
- 已执行 `git diff --check`，无格式或补丁错误。
- 已核对变更范围：只涉及 `docs/product/**` 和 `.agent-lanes/product-frontend/worklog.md`。

## 待用户确认

- MVP 容器：本地浏览器应用还是桌面壳层。
- 未归属事件是否显示为显式 `Unknown` 桶。
- MVP 球员统计是否只做动作计数，还是要包含人工录入结果。
- 导出 schema 是否直接冻结当前事件契约为 `v1`，或再加一层产品级 schema。
