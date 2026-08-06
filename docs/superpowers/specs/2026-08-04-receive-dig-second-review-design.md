# Receive/Dig 标签拆分与二次复核设计

## 背景

首轮人工复核已完成 67 个候选窗口，但当前 `receive` 同时包含接发球和防守起球，且多条备注指出 0.8 秒窗口没有覆盖实际动作。上游 YOLO 权重只有六类，无法直接输出新的 `dig`。

本轮只解决两个问题：

1. 将 Spike-Trace 自有标签契约升级为七类，明确区分 `receive` 和 `dig`。
2. 生成一份 17 条的精简二次复核队列，处理现有 6 条 `receive` 和时间/不确定备注。

本轮不训练模型、不统计球员数据，也不修改尚未经二次确认的真实视频标签。

## 标签定义

内部标签契约版本升级为 2，顺序为：

```text
background, serve, receive, set, attack, block, dig
```

- `receive`：美国队直接接对方发球的第一次触球。
- `dig`：美国队针对对方扣球、吊球等进攻击球完成的防守起球，不包含拦网触球。
- `background`：没有美国队目标动作；例如对方直接出界且美国队没有触球。
- 对方非进攻 free ball 的我方首次触球暂不强行并入 `receive` 或 `dig`；本轮标为 `background` 并在备注写 `free-ball`，等待后续单独确定口径。

`dig` 追加在原六类之后，不改变原六类顺序。

## 旧模型兼容

R3D-18/Tiny3D checkpoint 自带标签列表和输出头大小。旧六类 checkpoint 继续按自己的六类标签加载和推理；新训练任务使用七类输出头。checkpoint 文件结构不变，因此不提升 checkpoint 格式版本，但新 checkpoint 记录标签契约版本 2。

上游 YOLO 仍按六类适配：

```text
background, serve, receive, set, attack, block
```

它不会被要求提供 `dig`，也不能在严格指标中正确预测 `dig`。评估报告提供两组指标：

- `metrics`：严格七分类指标，保留 `dig` 真值，真实显示能力缺口。
- `compatibility_metrics`：仅用于比较旧六类模型的粗粒度候选发现能力，把真值 `dig` 映射为 `receive` 后计算六分类指标。

复核 CSV 始终保留严格真值；兼容指标不能覆盖人工标签，也不能作为可部署模型成绩。

## 二次复核队列

二次复核请求使用提交到 Git 的 JSON 规格文件，记录来源序号、复核原因和可选建议。程序读取已复核 manifest 与该规格，校验记录号、标签和建议时间，生成 UTF-8 BOM CSV。

队列包含以下 17 条原记录：

```text
1, 19, 21, 22, 23, 27, 31, 32, 35, 39, 43, 46, 47, 53, 65, 66, 67
```

其中 21、22、27、35、43、53 用于拆分 `receive/dig/background`；其余记录来自时间偏移或不确定备注。记录 41 的“没接到球”已明确为 `background`，不进入二次复核。

队列输出同时保留：

- 原记录号、当前动作、当前起止时间和我方位置。
- 原始人工备注与复核原因。
- 建议处理方式、建议动作和建议起止时间。
- 留空的人工确认动作、确认时间和补充备注。

Excel 只是便于填写的视图，CSV/JSON 才是可复现输入。生成的 workbook、预览和视频仍保留在 `outputs/`，不提交 Git。填写内容规范化为带来源快照的结果 JSON，记录秒级人工精度、接受的操作、确认动作、确认起止秒数和备注。

## 命令与数据流

```text
review spec JSON + reviewed annotation CSV
    -> spiketrace prepare-review
    -> second_review_queue.csv
    -> artifact-tool workbook
    -> 用户填写人工确认动作/时间/备注
    -> normalized review results JSON
    -> spiketrace apply-review
    -> second-reviewed annotation CSV
```

`prepare-review` 和 `apply-review` 默认检查视频存在；跨设备只检查结构时可使用 `--allow-missing-videos`。`prepare-review` 禁止覆盖来源清单或复核规格，`apply-review` 禁止覆盖来源清单、复核规格或确认结果。普通操作原位更新，`add_window` 保留来源记录并将新窗口追加到末尾。应用结果时，相对 `video_path` 始终按来源清单的视频根验证；输出位于其他目录时路径文本保持不变，后续读取需显式提供相同视频根。

## 错误处理

- 规格或结果版本未知、记录号缺失/重复/额外/乱序、未知标签、无效操作或无效时间范围时立即失败。
- 规格和结果中的 manifest/spec 文件名必须与实际输入一致，避免把复核请求套到另一份数据。
- 建议开始和建议结束可分别省略；正式确认结果必须同时提供动作、开始秒数和结束秒数，且时间为非负有限数并满足 `start < end`。
- 结果逐条精确校验来源视频、动作、时间、split、我方位置、号码、裁剪和原备注；任何来源时间变化都拒绝应用。
- 源 CSV 出现表头之外的额外单元格时立即失败，不把底层写入错误暴露给用户。
- 生成队列和应用结果都不覆盖输入文件；结果先写入同目录临时文件，重新加载通过后原子替换，失败时保留已有输出。

## 测试与验收

- manifest 接受 `dig`，未知标签仍被拒绝。
- 旧六类 checkpoint 在七类代码中仍可加载。
- 真值 `dig`、预测 `receive` 时严格指标判错，兼容指标判对，复核记录仍保留 `dig`。
- 复核队列按原 manifest 顺序输出 17 条，并正确提取 `reviewer note:` 后的内容。
- workbook 包含 17 条、七类下拉选项、可读时间和建议列；所有工作表完成数值检查、公式错误扫描和渲染目检。
- 结果应用保留 67 条首轮数据，原位更新 16 条并追加 1 条，最终 68 条且总时长 60.3 秒。
- 缺失、重复、额外、乱序确认，来源快照变化，非法动作/时间、异常 CSV 和同路径覆盖均失败且不破坏已有输出。
- 完整单元测试、Ruff lint 与格式检查通过。

## 延后事项

- 将填写后的 workbook 自动规范化为结果 JSON；当前由人工/开发流程完成规范化。
- 支持人工明确删除或合并窗口；当前只支持 `keep/relabel/move_window/add_window`。
- 处理 free ball 的正式标签口径。
- 从更多完整回合补充 `serve`、`set`、`attack` 和 `dig` 正样本。
- 使用另一场完整比赛建立独立验证集后再微调。
