# 账户与工作区页面设计

状态：提议，待上游任务和产品 Lane 评审
日期：2026-08-10
负责范围：账户资料、工作区设置、成员占位、存储用量、数据健康、导入和导出管理

## 1. 边界

本 Lane 负责页面的数据契约和功能内容，不负责：

- 全局导航结构、主侧栏、顶部导航和跨模块入口顺序。
- 视频导入后的分析流程、播放器、时间轴和标注/复核工作台。
- 模型评估详情、球员统计详情或报告的核心可视化体验。

这些页面由产品 Lane 决定从哪里进入；本设计只保证被路由到页面后，账户与工作区操作完整、安静且可扫描。

建议路由不等同于全局导航决定：

```text
/account
/workspaces/{workspace_id}/settings/general
/workspaces/{workspace_id}/settings/members
/workspaces/{workspace_id}/settings/storage
/workspaces/{workspace_id}/data/imports
/workspaces/{workspace_id}/data/exports
/workspaces/{workspace_id}/data/health
```

首版单用户可以隐藏 Members 路由，但数据模型和权限检查仍保留。

## 2. 体验原则

- 面向反复操作的本地分析软件，布局应紧凑、稳定、工作导向，不做营销式 hero 或装饰性卡片墙。
- 页面主栏使用清晰分区和表格；卡片只用于单个重复 job、确认对话框或确有边界的状态摘要。
- 账户和工作区切换是数据上下文切换，必须显示当前工作区名称；全局位置由产品 Lane 决定。
- 危险操作与普通设置分区，删除前展示准确影响范围和可恢复策略。
- 后端保存绝对路径时，页面默认只展示工作区名称、磁盘/目录别名和可用空间，不泄露不必要的完整路径。
- 所有长任务展示明确状态、开始时间、结果与可操作错误，不用无限 loading 代替 job 状态。
- 移动端可查看和完成轻量设置；大规模导入、导出和存储维护优先桌面布局，但不能产生横向溢出或控件重叠。

## 3. 账户页面

### 3.1 页面目标

账户页管理“操作者身份与显示偏好”，不管理工作区业务数据。首版本机身份不要求登录，页面标题仍可使用“账户”或由产品 Lane 统一成“个人资料”。

### 3.2 分区

#### 个人资料

字段：

```text
display_name
email (optional, read/write only when cloud identity is enabled)
avatar_asset_id (optional)
```

交互：

- 头像用图片选择控件，空值显示首字母占位。
- 显示名称用文本输入。
- 保存按钮只在值变化且验证通过时启用。
- 邮箱在本地模式下不强制显示，避免暗示已有云账户。

#### 显示与地区

字段：

```text
locale
timezone
default_workspace_id
```

交互：

- 语言和时区使用下拉菜单。
- 默认工作区使用可搜索工作区选择器。
- 保存后只影响日期、数字和启动上下文；视频相对时间不做时区转换。

#### 本地数据与隐私

只读信息：

```text
account_mode: local | cloud
workspace_count
last_backup_at
application_data_location_display
```

命令：

- 导出个人偏好 JSON。
- 重置本机偏好，不删除工作区数据。

首版不展示密码、登录设备、订阅或账单入口，因为不存在对应能力。

### 3.3 数据契约

```json
{
  "id": "019f...",
  "display_name": "Analyst",
  "email": null,
  "locale": "zh-CN",
  "timezone": "Asia/Tokyo",
  "default_workspace_id": "019f...",
  "account_mode": "local",
  "row_version": 3,
  "updated_at": "2026-08-10T08:30:00.000Z"
}
```

更新使用：

```text
PATCH /api/v1/me
If-Match: "account-019f...-3"
```

并发冲突时保留表单输入，重新获取当前数据并显示“设置已在其他窗口更新”，不静默覆盖。

## 4. 工作区通用设置

### 4.1 页面目标

让 owner/editor 明确当前数据属于哪个工作区、存在哪里、规模多大、是否健康，以及如何迁移或导出。

### 4.2 基本资料

字段：

```text
name
slug
locale
timezone
asset_mode: managed | linked | mixed
```

- `name` 是主要展示名。
- `slug` 只用于导出文件名和可读 URL，不作为主键；自动规范化但允许编辑。
- `asset_mode` 变化只影响新资产。已有资产迁移必须走独立任务，不能用设置开关瞬间搬动大文件。

### 4.3 摘要带

用紧凑、无装饰的横向指标带展示：

```text
matches
videos
players
annotation_versions
model_artifacts
storage_used_bytes
```

这些是导航辅助摘要，不是球员统计或模型质量分析。

### 4.4 危险区域

命令：

- 归档工作区：可恢复，默认行为。
- 删除工作区：先生成影响预览，要求输入工作区名称确认；进入软删除和保留期。
- 清理无引用对象：独立任务，先显示对象数量和可释放空间。

任何危险操作都不得和普通保存按钮放在同一按钮组。

## 5. 成员与权限

### 5.1 首版行为

- 本地单用户工作区只有一名 owner。
- 若没有云身份/局域网协作能力，成员页隐藏邀请命令，显示当前 owner 和模式状态即可。
- 不显示无效的“邀请已发送”或虚构多人协作入口。

### 5.2 未来行为

成员表列：

```text
member
role
status
joined_at
last_active_at
actions
```

角色能力：

| 能力 | Owner | Editor | Viewer |
| --- | --- | --- | --- |
| 查看工作区数据 | 是 | 是 | 是 |
| 导入比赛与视频 | 是 | 是 | 否 |
| 创建标注/事件修订 | 是 | 是 | 否 |
| 导出数据 | 是 | 是 | 是，可受策略限制 |
| 修改工作区设置 | 是 | 否 | 否 |
| 管理成员和删除工作区 | 是 | 否 | 否 |

服务端必须执行权限，前端隐藏按钮只改善体验，不构成安全边界。

## 6. 存储页面

### 6.1 用量概览

展示：

```text
available_bytes
used_bytes
database_bytes
managed_video_bytes
model_bytes
evidence_bytes
import_source_bytes
export_bytes
unreferenced_bytes
last_health_check_at
```

用分段容量条或表格展示类别，不用仪表盘式装饰图。颜色只用于健康/警告/错误状态，并保持文本标签。

### 6.2 资产表

筛选：类型、可用状态、存储模式、引用状态。

列：

```text
name
kind
storage_mode
size
availability
references
last_verified_at
```

行命令：

- 重新校验哈希。
- 重新链接 missing/changed 外部资产。
- 查看引用对象。
- 对无引用 managed 资产提交清理任务。

不允许直接在表格中编辑真实文件路径。

### 6.3 存储迁移

从 linked 转 managed 或移动工作区根目录时：

1. 选择目标位置。
2. 预检空间、权限和文件冲突。
3. 显示将复制/链接的对象数和大小。
4. 创建可恢复 job。
5. 校验全部哈希后切换引用。
6. 原位置不自动删除；由用户单独确认清理。

## 7. 导入中心

### 7.1 列表

导入列表属于数据维护，不承担“开始分析”的产品流程。

列：

```text
source_name
kind
status
records_seen
errors
started_at
requested_by
```

筛选：状态、种类、日期。

### 7.2 新建导入

步骤：

1. 选择 `annotation_csv`、`data_package_json`、`events_csv` 或 `statistics_csv`。
2. 选择文件；若 CSV 引用视频，再选择 video root 或让系统按现有资产匹配。
3. 运行 validation job。
4. 查看摘要、警告和按行错误。
5. 选择冲突策略；默认 `fail`。
6. 提交 import job。

页面不得在验证完成前显示“已导入”。失败 job 保留原件、选项和错误报告，用户可修正源文件后创建新 job。

### 7.3 验证结果

摘要：

```text
entities_to_create
entities_to_reuse
versions_to_create
assets_found
assets_missing
warnings
errors
```

错误表支持按文件、行、列过滤，并提供下载 JSON 错误报告命令。错误消息只说明问题和修正位置，不展示堆栈。

## 8. 导出中心

### 8.1 列表

列：

```text
name
kind
selection
status
size
created_at
expires_or_retention
requested_by
```

行命令：下载/在文件夹中显示、复制校验和、重新运行、删除导出产物。

### 8.2 新建导出

配置：

- 格式：JSON 无损包、标注 CSV、事件 CSV、球员统计 CSV、模型评估 CSV。
- 范围：整个工作区、指定比赛或指定球员/比赛统计。
- 版本：明确 annotation/event/stat snapshot，默认在提交时解析 current。
- 资产：默认只含 manifest；视频/模型必须显式勾选并显示预计大小。

版本选择使用下拉菜单；二元设置用开关或复选框；导出命令使用下载图标和文本。生成完成前不出现可下载链接。

## 9. 数据健康页面

### 9.1 检查项

```text
sqlite_integrity
foreign_keys
current_version_pointers
asset_presence
asset_hashes
annotation_split_isolation
annotation_version_hashes
model_evaluation_references
event_revision_chains
statistics_source_hashes
orphaned_assets
```

每项状态：`healthy`、`warning`、`error`、`not_run`。

### 9.2 修复原则

- 检查和修复分开；健康检查本身只读。
- 自动修复前显示将修改的记录和可恢复方式。
- 缺失 linked 视频只能重新链接，不能用同名文件自动替代。
- 统计来源变化时重建新 snapshot，不在原 snapshot 上改数字。
- 任一修复创建审计记录和 job 结果。

## 10. 页面状态

所有页面需要以下状态：

| 状态 | 行为 |
| --- | --- |
| 初始加载 | 保留稳定布局和表头，不让 loading 文本改变控件尺寸 |
| 空状态 | 显示与当前页面直接相关的主要命令，例如“新建导入” |
| 权限不足 | 保留可查看内容，隐藏或禁用命令并说明所需角色 |
| 后端不可用 | 保留上次已加载数据，显示可重试状态，不伪装保存成功 |
| 并发冲突 | 保留用户输入，显示当前服务器版本并允许重新应用 |
| 存储不足 | 在任务提交前显示所需/可用空间，阻止会失败的写入 |
| job 失败 | 显示稳定错误摘要、时间和查看错误报告命令 |
| 资产缺失 | 显示缺失状态和重新链接命令，不自动选择同名文件 |

## 11. 响应式与可访问性

- 桌面设置页使用固定宽度次级设置导航与弹性主内容；移动端切换为顶部页面选择器，次级导航不挤压正文。
- 表格在窄屏转换为字段列表或允许受控横向滚动；主要命令保持可见，文本不能覆盖状态徽标。
- 表单标签始终可见，不只依赖 placeholder。
- 所有状态同时使用文本和图标，不能只靠颜色。
- 图标按钮使用项目现有 icon 库或 Lucide，并提供 tooltip 与可访问名称。
- 删除、归档、清理和迁移对话框支持键盘操作，默认焦点不落在危险确认按钮上。
- 数字输入、选择器、开关和按钮使用稳定尺寸，异步状态不引发布局跳动。

## 12. 前端数据层边界

未来 `frontend/src/features/workspace/**` 只包含本功能的页面、查询、表单和状态，不实现全局导航。

建议结构：

```text
features/workspace/
├─ api/              # typed client 与 query keys
├─ components/       # workspace settings/data controls
├─ pages/            # route-level account/workspace pages
├─ schemas/          # form/response validation
├─ state/            # unsaved local form state only
└─ index.ts           # public feature exports
```

约束：

- 服务端数据由 query cache 管理，不复制到全局 mutable store。
- 表单 schema 与 API contract 对齐，但服务端仍是最终校验者。
- 绝对路径、SQLite row 和数据库枚举实现细节不进入组件 props。
- 产品 Lane 可组合路由和导航，但通过 `index.ts` 公共入口消费，不深层导入内部组件。

## 13. 页面验收

- 本地单用户在没有登录能力时可以编辑资料、选择默认工作区和管理本地数据，不看到虚构云功能。
- owner 可以查看工作区规模、存储分类、健康状态和完整 import/export job 历史。
- editor/viewer 的命令与服务端权限一致。
- 导入必须经过 validation preview；有错误时不能提交。
- 导出明确锁定版本，完成后能显示大小、SHA-256 和来源选择。
- linked 资产丢失或变化时有清晰状态和重新链接流程。
- 页面在桌面与移动宽度下无文本/控件重叠，长文件名可换行或截断并提供完整 tooltip。
- 页面不修改产品 Lane 的全局导航，也不复制分析/标注工作台。
