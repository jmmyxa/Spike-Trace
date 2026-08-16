# Rangitoto 复核证据链设计

## 背景

Rangitoto 全场双裁剪扫描已经产生候选，但发布前审查发现三项可信度问题：

1. 训练通过 `CAP_PROP_POS_MSEC` 读取窗口中心附近的最近帧，整场顺序推理却使用 `floor(timestamp * fps)`，两条路径在 30 FPS、1 秒、16 帧窗口中通常只有 8 帧一致。
2. 单侧事件没有保存其成员窗口索引，复核工件只能按动作和时间重叠反推，导致同一个窗口被归入多个相邻同动作事件。
3. 最终 JSON 只包含被事件引用的部分窗口，并保留本机绝对路径；生成器也位于忽略目录，其他设备无法审计或重建复核材料。

本设计只修复动作模型候选生成和人工复核证据链，不开始号码归属、前端、账户或统计功能。

## 目标

- 训练、单片段读取和整场顺序推理使用同一个确定性帧采样合同。
- 每个单侧动作事件显式记录真实成员窗口索引，合格窗口至多归属一个事件。
- 最终 `merged_candidates.json` 自包含两路完整规范化输入、全部 32,896 个窗口和派生候选。
- JSON/CSV 的生成和验证进入受版本控制的 Python 程序；XLSX 构建器进入受版本控制的工具目录。
- 重新扫描 Rangitoto far/near 后再生成并交付工作簿，旧的 floor-sampling 候选不得继续用于人工复核。
- README 始终与程序结构、命令、数据合同和当前限制一致。

## 不采用的方案

### 仅修补当前一次性工作簿脚本

优点是改动小，但训练和推理仍可能再次漂移，证据合同也不会进入正式程序。拒绝。

### 保留 floor 采样并重新训练

训练视频和 Rangitoto 视频实测表明，历史 `CAP_PROP_POS_MSEC` 读取等价于窗口中心点的 half-up 最近帧。改为 floor 会人为改变已有 checkpoint 的训练分布，因此没有必要。拒绝。

## 帧采样合同

合同名为 `center-nearest-frame-v1`。

对半开窗口 `[start_seconds, end_seconds)` 和 `num_frames = N`：

```text
sample_time(i) = start + (i + 0.5) * (end - start) / N
frame_index(i) = clamp(floor(sample_time(i) * fps + 0.5), 0, frame_count - 1)
```

这里必须使用 half-up，不使用 Python 或 NumPy 的银行家舍入。

`sample_video_frames` 使用 `CAP_PROP_POS_FRAMES` 定位上述显式帧号；`iter_sequential_video_clip_batches` 顺序解码相同帧号。两条路径对相同视频、窗口、裁剪、帧数和图像尺寸必须输出逐字节相同的 RGB clip。

新训练配置和 checkpoint 写入 `sampling_contract: "center-nearest-frame-v1"`。历史 checkpoint 缺少该字段时按该合同解释，因为它与历史训练视频的实际 OpenCV 读取结果一致。推理输出也记录该字段。

本合同只承诺恒定帧率视频的 frame ordinal。未来支持 VFR 视频时必须单独定义基于 PTS 的合同，不能静默沿用本规则。

## 单侧事件窗口归属

`merge_action_windows` 保持现有公开返回值，新增一个带证据的接口：

```python
merge_action_windows_with_provenance(...) -> tuple[list[ActionEvent], dict[str, list[int]]]
```

输入窗口数组的位置就是稳定 `window_index`。合并状态机在创建和扩展 `_EventCandidate` 时同步累积索引；只有最终保留的事件才写入映射。

推理 JSON 格式升级为版本 2，每个事件增加 `source_window_indices`。CSV 仍保持面向事件的便携视图，不强迫用户阅读窗口列表。

必须验证以下不变量：

- 每个事件的成员索引唯一、递增且在窗口数组范围内。
- 每个成员窗口的动作与事件动作一致，且达到置信度阈值。
- 任意窗口索引最多属于一个事件。
- 事件开始、结束和平均置信度与成员窗口重算结果一致。
- 背景、低置信度、无效时间窗和被最短事件时长过滤的窗口不进入任何事件。

## 双裁剪合并核心

新增 Python 模块负责以下工作：

1. 读取和严格验证 far/near inference JSON v2。
2. 验证同一视频、模型、采样合同和除 crop/device 外的推理参数一致。
3. 将路径规范化为仓库相对 POSIX 路径；无法相对化时只保存文件名和内容哈希，不保存机器目录。
4. 保留现有跨侧规则：
   - 同动作重复：重叠至少 400 ms，且较短事件覆盖率至少 0.5 或中心差不超过 500 ms。
   - 不同动作冲突：重叠至少 400 ms 或中心差不超过 500 ms；两条候选都保留。
5. 输出稳定候选 ID、重复组、冲突组和来源事件引用。
6. 把两路完整规范化输入嵌入最终 JSON；候选只保存来源事件和窗口索引引用，不重复复制窗口对象。
7. 生成与候选逐行对应的 UTF-8 BOM CSV。

最终 JSON 使用 `format_version: 2` 和 `merge_format_version: 2`，并包含：

- `input_runs.far`、`input_runs.near`：完整 events、windows、规范化设置和原始/规范化 SHA-256。
- `events`：供人工复核的合并候选。
- `duplicate_groups`、`conflict_groups`：完整分组和链接证据。
- `settings`：算法版本、阈值、时间单位和半开区间语义。

验证器从 `input_runs` 重新计算所有派生字段并与文件内容比较，因此其他设备只拿到最终 JSON 也能审计合并过程。完整模型推理仍需要未提交的 3.2 GiB 原视频和约 127 MiB checkpoint；README 必须明确区分“推理可复现”和“合并可审计”。

## 工作簿

受版本控制的 Node 工具读取已经验证的 `merged_candidates.json`，只负责生成四页工作簿：

- `概览`
- `候选动作`
- `来源事件`
- `标签说明`

工作簿不加入 32,896 行全窗口表，避免破坏人工使用体验；完整窗口证据保存在 JSON。候选页只把黄色五列作为人工输入：人工确认动作、人工开始时间、人工结束时间、人工侧别、备注。人工确认动作非空即代表完成复核，`background` 代表误检。

XLSX ZIP 字节可能因时间戳变化而改变，因此验收以重新导入后的工作表、公式、值、空白输入列和视觉渲染为准，不以二进制 SHA 跨设备一致为唯一标准。

## 重跑与版本隔离

修复采样合同后使用原 checkpoint 重跑 Rangitoto 两个 crop。模型无需重训，但旧 floor-sampling 输出必须保留在忽略目录或明确替换，不能与新输出混用。

新推理输出和最终工件必须记录：

- checkpoint SHA-256
- `sampling_contract`
- OpenCV、PyTorch 和 torchvision 版本
- 视频元数据和视频文件 SHA-256
- crop、窗口、步长、阈值、batch size 和设备

完成重跑后重新计算候选数量和分布。README 不得继续引用旧的 2,876 个候选，除非新结果恰好相同并经独立校验。

## 测试与验收

- 帧索引 helper 覆盖 half-up 边界、重复索引、尾帧 clamp、非整数 FPS 和非整秒窗口。
- 随机读取与顺序读取在同一夹具和裁剪下逐字节相同。
- 新 checkpoint/config 写入采样合同；历史 checkpoint 缺字段时兼容。
- 单侧事件成员映射覆盖中间不同动作打断两个相邻同动作事件的反例。
- 双裁剪小型夹具覆盖单源、跨侧重复、跨侧冲突、主来源选择和路径规范化。
- 最终验证检查 32,896 个窗口完整且唯一、4,179 个来源事件完整、候选 CSV/JSON 一致，以及成员窗口无重复归属。
- 全部 Python 测试、Ruff、编译检查、Node 工具测试、XLSX 公式扫描和四页视觉检查通过后才交付人工复核。
