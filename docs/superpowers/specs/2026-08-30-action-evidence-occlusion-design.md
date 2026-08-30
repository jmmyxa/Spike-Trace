# 动作证据、遮挡与球员参与者设计

状态：已确认

日期：2026-08-30

## 背景

Rangitoto 首轮主动学习工作簿已经完成 40 个短片的人工复核，共填写 83 条记录。复核同时暴露了
现有五字段动作行无法安全表达的事实：部分动作在镜头外或被遮挡，只能通过裁判手势、比分或回合
上下文推断；无效拦网需要归属到实际起跳的球员；被动推球不应被误算为进攻；完全遮挡不能成为
动作训练样本或自动背景负样本。

当前 `review.xlsx` 是不可覆盖的人工来源。它继续保留，但不能直接通过 v1 应用器写入训练清单。
本设计在不要求用户重填、不增加审核状态列或复选框的前提下，把动作事实、证据、比赛结果、遮挡和
球员参与关系分开保存。

## 已确认的产品口径

1. `free_ball` 只作为现有“人工确认动作”字段的新取值，不新增名为 `free_ball` 的栏目。
2. `free_ball` 本身不等于失误。是否失误属于回合结果：成功过网不算失误；下网、出界或违例并
   直接失分时才算 free-ball 失误。
3. 连续遮挡按一次事件计数，同时记录持续时间。遮挡消失后再次出现才是新事件。
4. 镜头外动作与遮挡事件分别统计。
5. 主要依据裁判手势、比分或回合上下文推断，而非画面直接看见的动作，保留在比赛记录中，但不进入
   视觉动作模型训练。
6. 被对手晃起、即使没有触球的拦网起跳，仍可计为该球员的 `block_attempt`。不能因为看到一个球员
   拦网就把全队标成拦网参与者。

## 目标

- 无损保存直接可见、部分可见、完全遮挡、镜头外和推断动作。
- 只把视觉证据足够的动作投影到动作训练清单。
- 保证遮挡、镜头外、未解决和 free-ball 区间不会被再次抽成错误或重复的自动背景负样本。
- 在不扩充当前七类模型输出头的情况下保留 `free_ball` 原始语义。
- 支持一个动作关联零名、一名或多名球员，并保留每名球员的参与类型和触球状态。
- 同步输出动作观察、遮挡、镜头外和参与者数据，后续可导入软件内部存储并导出 CSV/JSON。
- 保留 Rangitoto 原工作簿、选择 JSON、代理短片和完整来源哈希。

## 非目标

- 本阶段不训练号码 OCR、人员跟踪或新的八分类动作模型。
- 本阶段不从裁判手势训练视觉动作分类器。
- 本阶段不自动推导全部得分、失误或上场时间统计。
- 本阶段不把主动学习样本成绩称为独立准确率。
- 本阶段不要求用户重新填写 40 个短片。

## 方案比较

### 采用：分层结果与哈希绑定的补充文件

保留 v1 工作簿，以工作簿 SHA-256、选择 JSON SHA-256、`clip_id` 和 `action_slot` 绑定一份补充
证据文件。合成后的 v2 结果分别保存动作观察、回合结果、遮挡、镜头外和球员参与者。训练清单只是
其中“可训练动作”的派生视图。

该方案不会让用户重填，也不会把不同含义塞进同一列；未来前端或下一版工作簿可以直接写相同 v2
契约。

### 不采用：只依靠备注关键字

只搜索“遮挡”“裁判”“推攻”等备注实现成本最低，但无法可靠区分“动作本身不可见”和“动作可见、
仅结果通过裁判手势判断”。关键字也无法稳定表达连续时间、多人参与和后续修订，因此只能作为迁移
提示，不能作为权威数据模型。

### 暂不采用：立即扩充模型和工作簿栏目

立即把 `free_ball` 加入模型输出头，并在当前工作簿增加多个状态列，会要求重新标注和重新训练；当前
样本量不足以支撑新的模型类别。`free_ball` 先作为复核语义保存，直接可见时投影成七类模型中的
`background` 负样本，未来有足够数据后可无损升级为第八类。

## 分层架构

```text
review.xlsx + selection.json + evidence overrides
  -> review observations v2（无损事实）
     ├─ action_observations（发生了什么）
     ├─ outcome_observations（结果是什么、依据是什么）
     ├─ occlusion_events（连续遮挡）
     ├─ off_camera_events（镜头外）
     └─ action_participants（哪些球员参与）
  -> training projection（只含视觉可训练动作）
  -> JSON 权威结果 + CSV 便携视图
```

五层数据必须通过稳定引用关联，但不能互相覆盖职责：

- 动作层决定 `serve`、`attack`、`free_ball` 等动作语义。
- 结果层决定得分、失误、继续比赛或未知，并保存裁判、比分等证据。
- 遮挡层决定画面可用性统计和训练排除区间。
- 参与者层决定球员号码归属和多人动作关系。
- 训练投影只负责把权威观察转换为当前七类模型可消费的清单。

## 动作观察契约

每条动作在一个 `result_set_id` 内使用稳定的 `action_ref`：

```text
<clip_id>/action-<三位原工作簿槽位>
```

例如第 5 个原工作簿槽位为 `round-01-clip-024/action-005`。补充文件新增的动作使用
`<clip_id>/supplemental-001` 等稳定引用，并把 `source_action_slot` 留空，不能冒充原工作簿行。
动作观察至少包含：

```json
{
  "action_ref": "round-01-clip-024/action-005",
  "clip_id": "round-01-clip-024",
  "source_action_slot": 5,
  "review_label": "free_ball",
  "relative_start_seconds": 11,
  "relative_end_seconds": 12,
  "start_seconds": 1234.0,
  "end_seconds": 1235.0,
  "team_side": "near",
  "visibility": "direct_clear",
  "evidence_basis": "direct_video",
  "training_decision": "eligible_as_background",
  "note": "垫过去的自由球"
}
```

`review_label` 允许：

```text
background, serve, receive, set, attack, block, dig, free_ball
```

`visibility` 允许：

```text
direct_clear, direct_partial, fully_occluded, off_camera, unresolved
```

`evidence_basis` 允许：

```text
direct_video, referee_signal, scoreboard, sequence_context, mixed
```

`training_decision` 由程序派生，不由用户填写：

- `direct_clear` 或 `direct_partial` 且动作可由画面本身确认：`eligible`。
- 直接可见的 `free_ball`：`eligible_as_background`。
- `fully_occluded`、`off_camera` 或 `unresolved`：`excluded`。
- 动作主要依靠 `referee_signal`、`scoreboard`、`sequence_context` 或无法证明画面本身足够时：
  `excluded`。

结果证据不能反向污染动作证据。例如发球动作直接可见、是否得分只由裁判手势确认时，发球仍可进入
动作训练；“发球得分”只进入结果层并标记 `referee_signal`。

## `background` 与 `free_ball` 规则

- 无时间的单条 `background` 继续表示“整段已看完，没有我方目标动作”的完成哨兵。
- 带时间的 `background` 表示显式可见的负样本区间，可以和其他动作共存。
- `free_ball` 保留为独立复核标签，不计入 `attack` 次数或 `attack` 失误。
- 直接可见的 `free_ball` 在当前七类训练清单中投影为定时 `background`，但 v2 权威结果始终保留
  `review_label=free_ball`，避免未来升级丢失语义。
- 备注提到但没有可靠时间的 free ball 不允许猜测时间。它进入 `unresolved` 观察，并保护可疑区间；
  若连可疑区间也无法确定，则保护整个短片。

## 回合结果契约

结果独立于动作保存：

```json
{
  "outcome_ref": "rangitoto-round-01/outcome-001",
  "related_action_refs": ["round-01-clip-034/action-001"],
  "outcome": "point_won",
  "result_type": "service_winner",
  "evidence_basis": "referee_signal",
  "status": "observed_or_inferred",
  "note": "根据裁判手势判断得分"
}
```

首版允许的 `outcome` 为：

```text
continued, point_won, point_lost, unknown
```

结果可以根据裁判、比分或上下文保存，但不会进入动作训练。free-ball 失误只有在结果明确为该动作直接
导致 `point_lost` 时才计数；否则只计 free-ball 次数，不计失误。

## 遮挡与镜头外契约

`occlusion_events` 与 `off_camera_events` 分开保存。连续遮挡按来源视频绝对时间合并：

- 区间重叠，或因整秒标注造成的间隔不超过 `1.0` 秒时，合并为同一次事件。
- 遮挡恢复超过 `1.0` 秒后再次发生，计为新事件。
- 完全遮挡但只能确定短片范围时，用完整短片边界，并记录 `interval_scope=clip_bounds`。
- 部分遮挡仍可直接确认动作时，动作可标 `direct_partial`；它不自动计入“完全遮挡次数”。
- 镜头外是摄像范围问题，不计入遮挡次数。

同步统计至少包括：

- `occlusion_event_count`
- `occlusion_total_duration_seconds`
- `off_camera_event_count`
- `affected_action_count`

所有完全遮挡、镜头外、未解决和人工定时观察区间都进入 `protected_intervals`。自动背景负样本不得与
同侧保护区间及其 guard 重叠。只有“整段确认无目标动作”的无时间 background 哨兵允许在合法区域
内继续抽取背景窗。

## 拦网与球员参与者

动作事件与球员是多对多关系。一个 `block` 观察只保留一条团队侧时间窗，不因有两名或三名球员而
复制训练样本。每名实际参与者单独保存：

```json
{
  "action_ref": "round-01-clip-011/supplemental-001",
  "track_id": "trk-0042",
  "identity_ref": "rangitoto:10",
  "player_number": "10",
  "participation": "block_attempt",
  "touch_status": "no_touch",
  "assignment_status": "confirmed",
  "assignment_confidence": 1.0,
  "evidence": []
}
```

规则：

- 被晃起但没有触球仍可记 `block_attempt + no_touch`。
- 只有实际起跳或明确执行拦网动作的球员才是参与者；场上其他队员不能被广播加入。
- `touch_status` 为 `touched | no_touch | unknown`，与是否得分分开。
- 只有 `assignment_status=confirmed` 的参与者进入个人统计。
- 兼容字段 `player_number` 只在恰好一名确认参与者时投影；多人动作保持空值，真实关系写入
  `action_participants`。
- 人员检测、跟踪和号码识别由 identity 层负责，不能通过动作标签猜号码。

## 当前工作簿迁移

原 `review.xlsx` 不覆盖、不原位修复。迁移生成一个不可覆盖的 evidence override 文件并绑定：

- selection 路径与 SHA-256
- workbook 路径与 SHA-256
- batch/round ID
- 每个覆盖项的 `clip_id + source_action_slot`
- 允许的来源修复和修复理由

当前批次的已知等价或录入差异按以下规则处理：

- Excel 自动去除说明文字尾随空格、shared formula 表示和数据验证导入差异，按语义验证，不按特定
  库的内存表示误判损坏。
- 一个只读短片 ID 空白只能由精确绑定的来源修复恢复；任何未列入补充文件的只读差异继续拒绝。
- 同一短片只有一个明确非空侧别时，后续空侧别可继承，并在结果中记录 `side_inherited=true`；零个或
  多个冲突侧别时拒绝自动推断。
- 定时 background 可以与动作共存，作为显式负样本处理。
- 备注只能生成迁移候选；程序不得从模糊文字静默发明动作时间、号码或结果。

本轮语义迁移至少覆盖：镜头外发球、裁判/比分推断、完全遮挡、自由球和无效拦网线索。无法从现有
整秒信息可靠确定的新增动作进入 `unresolved`，不进入训练。

## 输出与同步

权威输出为一个不可变 JSON 结果集，CSV 是同一结果集的便携视图。所有文件共享 `result_set_id`、
selection SHA-256、workbook SHA-256 和生成器版本：

```text
round-01-results.json              # 无损权威结果
action_training_round_01.csv       # 仅训练投影
round-01-observations.csv          # 全部动作与结果观察
round-01-occlusion-events.csv      # 遮挡与镜头外事件
round-01-action-participants.csv   # 多人球员参与关系
```

在身份模型尚未运行时，参与者 CSV 可以只有表头和来源元数据，不能用全队号码填充。后续 SQLite 和
前端读取权威 JSON/数据库实体，CSV 只作为导出视图。

## 错误处理与信任边界

- 不覆盖任何来源或已存在输出。
- 补充文件引用不存在的 clip/action、哈希不匹配、重复引用或时间越界时，整个应用失败。
- 未知可见性、未知证据或证据冲突默认排除训练，不能默认当成可见。
- 推断动作不得进入正样本；遮挡和未解决区间不得进入自动负样本。
- 训练 CSV、权威 JSON 和所有 CSV 视图必须原子发布；任一发布失败则全部回滚。
- 结果 JSON 必须保存原始人工值、规范化值、修复记录和训练投影原因。

## 测试与验收

实现必须使用现有 `unittest` 和 Node 可执行测试覆盖：

1. 原工作簿哈希在提取、迁移和应用前后保持不变。
2. 40 个短片和 83 条已填写记录均可读取，折叠或隐藏行不造成丢失。
3. Excel 等价表示可以通过语义验证，未授权的只读篡改仍被拒绝。
4. 侧别只在单一明确来源时继承，冲突时失败。
5. `free_ball` 保留原始标签并仅投影为训练 `background`；不计入 attack。
6. 裁判推断动作不进入训练；直接可见动作但仅结果由裁判判断时，动作仍可训练。
7. 遮挡、镜头外、free-ball 和其他人工定时观察都阻止重叠 hard negative。
8. 连续遮挡按 1 秒精度合并，分离遮挡与镜头外统计。
9. 多人拦网只生成一条动作观察和多条参与者记录，不广播全队、不复制训练窗。
10. JSON/CSV 输出共享同一来源哈希和结果集 ID，任何输出碰撞或中途失败均不留下半套结果。
11. README 同步程序结构、当前状态、数据口径和下一步顺序。

## 实施边界与顺序

1. 先修正工作簿语义验证和 v1 兼容提取，不修改来源文件。
2. 实现 evidence override 校验和 v2 无损结果合成。
3. 实现训练投影、保护区间和同步 JSON/CSV 输出。
4. 对 Rangitoto 真实工作簿执行只读迁移并验证结果。
5. 用户不再需要操作工作簿；程序完成结果应用后，再进入独立 `val` 比赛和两阶段微调。
6. 动作基线稳定后，identity 层实现球员跟踪、号码确认和 `action_participants` 填充。

## 完成标准

- 当前 40 段复核结果无损进入 v2 权威结果。
- 所有训练行都有直接视觉依据或合法的 `free_ball -> background` 投影。
- 完全遮挡、镜头外和推断动作在比赛记录中可查，但不会污染正样本或背景负样本。
- 遮挡和镜头外统计与动作结果共享同一来源版本。
- 无效拦网可由后续号码识别归属到实际参与球员，且不会广播给全队。
- 原工作簿、选择 JSON 和既有训练清单均未被覆盖。
