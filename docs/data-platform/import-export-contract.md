# CSV/JSON 导入导出契约

状态：提议，待上游任务评审
日期：2026-08-10
契约范围：标注、比赛、事件、模型评估、球员统计和工作区选择性备份

## 1. 契约原则

1. **先验证，后提交**：所有导入先完整解析和跨记录校验，`validate_only` 不写业务数据。
2. **全有或全无**：一个 commit job 在单个工作区事务内提交；失败不能留下半个版本。
3. **保留原件**：导入文件先登记为 `import_source` 资产，保存 SHA-256、大小、原始名称和契约版本。
4. **显式版本**：JSON 必须包含 `format` 和 `format_version`；CSV 通过同名 sidecar manifest 指定契约。
5. **稳定标识**：JSON 使用 UUID；CSV 同时导出稳定 ID 和人类可读字段，导入不依赖行号。
6. **不静默覆盖**：相同 ID 但内容不同必须按冲突策略处理，默认失败。
7. **无损与表格视图区分**：JSON 可以完整往返；CSV 是特定资源的扁平视图，不能表达所有历史关系。
8. **可证明来源**：导出 manifest 列出选择条件、来源版本、对象哈希、记录数和生成器版本。

## 2. 通用编码与序列化

### 2.1 CSV

- 导入接受 UTF-8 或 UTF-8 BOM；拒绝无法解码内容。
- 导出使用 UTF-8 BOM 和 CRLF，保持 Excel/Windows 兼容并延续当前标注契约。
- 分隔符固定为逗号，引用遵循 RFC 4180。
- 表头区分大小写；拒绝重复表头和表头之外的额外单元格。
- 空字符串表示可空文本；数值和布尔字段不得用空格、`NaN`、`Infinity` 或本地化数字。
- 时间秒数使用十进制文本，最多三位小数；内部按毫秒保存。
- 行顺序不定义身份；有 `sort_order` 时只定义展示/导出顺序。

### 2.2 JSON

- UTF-8、无 BOM、LF 换行。
- 导出默认两空格缩进；哈希计算使用规范化 JSON，不依赖缩进或对象键顺序。
- 日期时间使用 UTC ISO 8601，例如 `2026-08-10T08:30:00.000Z`。
- 视频时间使用整数毫秒。兼容旧格式时允许秒数字段，但必须由对应版本适配器解析。
- UUID、SHA-256 和枚举均为小写字符串；未知枚举值不得静默映射。
- 顶层未知字段可在同一主版本内忽略，但核心实体中的未知字段默认拒绝，避免拼写错误被吞掉。

## 3. 导入工作流

### 3.1 两阶段接口

```text
POST /api/v1/workspaces/{workspace_id}/imports
```

请求先创建 `validate_only` job：

```json
{
  "kind": "annotation_csv",
  "contract_version": 1,
  "mode": "validate_only",
  "source_asset_id": "019f...",
  "options": {
    "video_root_token": null,
    "asset_mode": "managed",
    "conflict_policy": "fail"
  }
}
```

验证成功后，用同一 source SHA-256 创建 `commit` job。commit 必须重新校验源哈希，不能信任过期预览。

### 3.2 导入状态

```text
queued -> validating -> ready_to_commit -> importing -> succeeded
                    \-> failed                 \-> failed
                    \-> canceled               \-> canceled
```

### 3.3 错误报告

```json
{
  "status": "failed",
  "summary": {
    "rows_seen": 90,
    "rows_valid": 89,
    "errors": 1,
    "warnings": 0
  },
  "errors": [
    {
      "code": "invalid_time_range",
      "location": {
        "file": "annotations.csv",
        "row": 18,
        "column": "end_seconds"
      },
      "message": "end_seconds must be greater than start_seconds."
    }
  ]
}
```

- 错误按文件、行、列和 JSON pointer 定位。
- validation error 默认最多返回 100 条，summary 保留完整计数。
- 路径、token、数据库信息和异常堆栈不进入用户错误消息。

## 4. 标注 CSV v1

### 4.1 兼容导入

为兼容当前仓库，`spiketrace.annotation-csv` v1 接受以下列。

必需列：

```text
video_path,start_seconds,end_seconds,label,split
```

可选列：

```text
annotation_id,source_annotation_id,team_side,player_id,player_number,
crop_x1,crop_y1,crop_x2,crop_y2,review_status,notes,source_time_precision_ms,sort_order
```

字段规则：

| 字段 | 规则 |
| --- | --- |
| `video_path` | 非空；相对路径以 CSV 所在目录或显式 video root 解析 |
| `start_seconds/end_seconds` | 非负有限十进制；转换后 `start_ms < end_ms` |
| `label` | 必须属于所声明的动作标签 schema |
| `split` | `train`、`val` 或 `test`；同一比赛只能属于一个 split |
| `team_side` | 空、`near` 或 `far` |
| `player_id` | 可空 UUID；提供时必须属于当前工作区 |
| `player_number` | 可空展示/兼容字段；不能单独建立永久球员身份 |
| `crop_*` | 四项全有或全空；非负整数且 `x2 > x1`、`y2 > y1` |
| `review_status` | 空、`unreviewed`、`reviewed` 或 `needs_review` |
| `source_time_precision_ms` | 正整数；省略时根据小数位推断，最低 1 ms |

若没有 `annotation_id`，导入器生成 UUID；导入映射保存 `source_row_number -> annotation_id`，但行号不进入后续版本身份。

### 4.2 CSV sidecar manifest

新导出必须同时生成 `annotations.csv` 和 `annotations.manifest.json`：

```json
{
  "format": "spiketrace.annotation-csv",
  "format_version": 1,
  "generated_at": "2026-08-10T08:30:00.000Z",
  "generator_version": "spike-trace/0.1.0",
  "workspace_id": "019f...",
  "match_id": "019f...",
  "annotation_dataset_id": "019f...",
  "annotation_version_id": "019f...",
  "label_schema": {
    "name": "volleyball-action",
    "version": 2,
    "labels": [
      "background",
      "serve",
      "receive",
      "set",
      "attack",
      "block",
      "dig"
    ]
  },
  "file": {
    "name": "annotations.csv",
    "sha256": "<64 lowercase hex characters>",
    "rows": 90,
    "encoding": "utf-8-sig",
    "line_endings": "crlf"
  }
}
```

旧 CSV 没有 sidecar 时，用户必须在导入预览确认“legacy annotation CSV v1”解释，系统将来源契约记录为 `legacy-inferred-v1`。

### 4.3 标注导出模式

- `current_version`：只导出指定 dataset 的当前冻结版本。
- `specific_version`：导出明确 `annotation_version_id`。
- `version_history_json`：完整版本图只能用 JSON 导出，不能压成一个 CSV。

## 5. 事件 CSV v1

兼容当前 `events.csv` 的必需列：

```text
video_id,event_id,start_ms,end_ms,action,confidence,
team_side,player_number,status,model_version,source
```

数据平台扩展列：

```text
workspace_id,match_id,event_revision_id,player_id,roster_entry_id,
model_artifact_id,prediction_run_id,revision_number,source_event_id
```

导出选项：

- `current_events`：每个 event 只导出当前 revision，适合统计或外部分析。
- `all_revisions`：每个 revision 一行，必须包含 `event_revision_id` 和 `revision_number`。
- `raw_predictions`：导出 predicted events，不能混称已确认事件。

若选择 `current_events`，CSV manifest 必须记录当前 revision 集合 SHA-256，保证统计来源可复现。

## 6. 其他 CSV 视图

### 6.1 `matches.csv`

```text
workspace_id,match_id,external_key,name,competition,season,played_at,
our_team_id,our_team_name,opponent_team_id,opponent_team_name,
status,primary_video_id,current_annotation_version_id
```

### 6.2 `players.csv`

```text
workspace_id,player_id,display_name,external_key,notes
```

### 6.3 `match_roster.csv`

```text
workspace_id,match_id,roster_entry_id,team_id,player_id,
jersey_number,role,is_our_team,valid_from_ms,valid_to_ms
```

### 6.4 `player_statistics.csv`

```text
workspace_id,match_id,stat_snapshot_id,stat_definition_key,
stat_definition_version,scope,player_id,roster_entry_id,set_number,
numerator,denominator,value,unit,source_event_revision_set_sha256
```

### 6.5 `model_evaluations.csv`

```text
workspace_id,evaluation_id,model_artifact_id,model_sha256,
annotation_version_id,evaluation_contract_version,label_schema_version,
metric_scope,metric_name,label,numerator,denominator,value,created_at
```

混淆矩阵和逐窗证据更适合 JSON；CSV 仅提供长表视图。

## 7. 无损 JSON 包 v1

### 7.1 顶层 envelope

```json
{
  "format": "spiketrace.data-package",
  "format_version": 1,
  "package_id": "019f...",
  "generated_at": "2026-08-10T08:30:00.000Z",
  "generator_version": "spike-trace/0.1.0",
  "workspace": {
    "id": "019f...",
    "name": "USA 2024 analysis"
  },
  "selection": {
    "match_ids": ["019f..."],
    "include": [
      "matches",
      "videos",
      "annotation_history",
      "model_evaluations",
      "events",
      "statistics"
    ]
  },
  "contracts": {
    "action_label_schema_version": 2,
    "event_contract_version": 1,
    "statistics_contract_version": 1
  },
  "entities": {},
  "assets": [],
  "integrity": {
    "entity_counts": {},
    "entities_sha256": "<64 lowercase hex characters>"
  }
}
```

### 7.2 `entities`

`entities` 按资源类型分组，至少支持：

```text
teams
players
matches
match_videos
match_roster_entries
annotation_datasets
annotation_versions
annotation_records
annotation_change_sets
annotation_changes
model_artifacts
model_evaluations
prediction_runs
predicted_events
events
event_revisions
stat_definitions
stat_snapshots
stat_values
```

每个实体对象都含稳定 `id` 和 `workspace_id`。导出时数组按 `(resource_type, id)` 稳定排序，以便可重复哈希和差异比较。

### 7.3 `assets`

```json
{
  "asset_id": "019f...",
  "kind": "video",
  "sha256": "<64 lowercase hex characters>",
  "byte_size": 123456789,
  "media_type": "video/mp4",
  "original_name": "match.mp4",
  "included": false,
  "relative_path": null
}
```

- 纯 JSON 导出默认只包含资产清单，不内嵌二进制或 base64。
- 将来 `.spiketrace` 归档可以用 ZIP64 封装 `package.json` 和 `objects/`，但仍使用同一 JSON envelope。
- `included = false` 的资产在导入后状态为 `missing`，直到用户重新链接或提供匹配哈希的文件。

## 8. 冲突与幂等性

支持以下 `conflict_policy`：

| 策略 | 行为 | 允许场景 |
| --- | --- | --- |
| `fail` | 任一稳定 ID 内容冲突即整包失败 | 默认、最安全 |
| `skip_identical` | ID 和规范化内容哈希相同则复用，不同则失败 | 重复导入同一包 |
| `fork_versions` | 可版本化实体在相同 base 上创建新分支，其他冲突失败 | 明确的离线编辑回传 |
| `remap_ids` | 为导入包生成新 UUID 并重写内部引用 | 复制到新工作区 |

禁止“last write wins”。

幂等键定义：

```text
workspace_id + import_kind + source_sha256 + contract_version + normalized_options
```

相同键已成功时返回原 job；已失败时允许显式 `retry_of_job_id` 创建新 job。

## 9. 导出工作流

### 9.1 创建导出

```json
{
  "kind": "data_package_json",
  "contract_version": 1,
  "selection": {
    "match_ids": ["019f..."],
    "annotation_version": "current",
    "event_revisions": "current",
    "statistics_snapshot": "current"
  },
  "include_assets": "manifest_only"
}
```

### 9.2 一致性快照

- 导出开始时在只读事务中解析所有 current pointers 为明确版本 ID。
- 后续构建只读取这些固定 ID；导出过程中产生的新修订不混入包。
- manifest 记录固定 ID、记录数和来源集合 SHA-256。
- 完成后登记导出资产 SHA-256；下载/复制只读取完成态资产。

### 9.3 文件命名

```text
<workspace-slug>_<selection>_<yyyy-mm-dd>_v<contract-version>.<ext>
```

文件名只用于展示，不参与实体身份。重复导出允许文件名相同，但资产 ID 和 SHA-256 分开保存。

## 10. 安全限制

- 导入器不跟随归档中的绝对路径、盘符、`..` 或符号链接到包外。
- JSON 里的 `linked_path` 只作提示，不能自动授予本机文件访问。
- CSV 以 `=`, `+`, `-`, `@` 开头的自由文本导出到面向电子表格的文件时必须防公式注入。
- 单文件大小、解压后总大小、实体数量和嵌套深度设上限；超限返回稳定错误。
- 导出账户资料时不包含未来认证凭证、token、系统绝对路径和内部错误日志。

## 11. 往返验收

### 11.1 当前仓库迁移验收

- 5 份现有标注 CSV 能以 legacy v1 导入，记录数、标签分布、总时长和 split 一致。
- 现有复核与补标 JSON 能建立父版本、变更集、来源快照和资产哈希。
- 当前比赛 JSON 的元数据、6 个半场段、两批补标状态和预训练基线能无信息丢失导入。
- 导出当前 90 条标注后，使用现有 loader 校验通过。

### 11.2 通用验收

- JSON 包导出后导入新工作区，所有稳定关系、版本图和内容 SHA-256 一致。
- CSV 导出后导回，不改变毫秒时间、标签、split、裁剪和可空字段。
- 重复导入同一来源是幂等操作。
- 任一错误都会回滚业务事务并保留完整错误报告。
- 不支持的未来版本明确失败，不产生部分实体。
- current 指针在导出开始后变化，不影响该导出的固定快照。
