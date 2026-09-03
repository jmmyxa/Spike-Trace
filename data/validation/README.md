# SoCal 独立验证资料

本目录只保存可提交的小型来源声明和真值契约文件，不保存原始比赛视频、代理 MP4、
checkpoint 或识别输出。固定比赛为 SoCal Cup Final 2025，验证目标队为 `C2 Attack 17-1 Elite`，
比赛标识为 `socal-cup-final-2025`。

## 已提交文件

- `socal_cup_c2_video.json`：视频相对路径、SHA-256 和媒体元数据的冻结声明。

运行时仍必须通过 `--video-root` 指向本机视频目录，并重新计算 SHA-256 和元数据；声明文件
不能代替源文件校验。视频路径相对 `video-root` 解释，而不是相对当前终端目录。

## 本地工作流

以下命令按顺序建立验证材料。所有路径都显式传入，避免把 SoCal 内容混入训练清单或主动学习
选择源。`freeze-validation-video` 和 `prepare-validation-rallies` 会生成本地绑定、回合队列和
静音代理；代理目录可以在复核后删除，不能提交到 Git。

```powershell
spiketrace freeze-validation-video `
  "data\SoCal Cup Final_ MVVC 17 Red vs C2 Attack 17-1, 06_15_2025 [9ESOXojmAGI].mp4" `
  data\validation\socal_cup_c2_binding.json `
  --repo-root . --video-root E:\Spike-Trace `
  --match-id socal-cup-final-2025 `
  --expected-sha256 b29e55cde114f5fda745349f86cc878d8abb81ba44ee430f467885bd7ce11c17

spiketrace prepare-validation-rallies `
  data\validation\socal_cup_c2_binding.json `
  data\validation\socal_cup_c2_queue.json `
  outputs\validation\socal-cup-c2-proxies `
  --repo-root . --video-root E:\Spike-Trace `
  --side-map data\validation\socal_cup_c2_side_map.json

spiketrace init-validation-truth `
  data\validation\socal_cup_c2_queue.json `
  data\validation\socal_cup_c2_truth-draft.json `
  --code-sha <draft-code-sha>

spiketrace validate-validation-truth `
  data\validation\socal_cup_c2_binding.json `
  data\validation\socal_cup_c2_truth-draft.json `
  --repo-root . --video-root E:\Spike-Trace

spiketrace lock-validation-truth `
  data\validation\socal_cup_c2_binding.json `
  data\validation\socal_cup_c2_truth-draft.json `
  data\validation\socal_cup_c2_validation.json `
  data\validation\socal_cup_c2_validation.csv `
  --repo-root . --video-root E:\Spike-Trace `
  --code-sha <lock-code-sha> --created-at <utc-iso8601>

spiketrace verify-validation-truth `
  data\validation\socal_cup_c2_binding.json `
  data\validation\socal_cup_c2_validation.json `
  data\validation\socal_cup_c2_validation.csv `
  --repo-root . --video-root E:\Spike-Trace

spiketrace verify-validation-isolation `
  data\validation\socal_cup_c2_binding.json `
  --repo-root . --video-root E:\Spike-Trace `
  --manifest data\annotations\usa_germany_2024_annotations_expanded_batch_02.csv

spiketrace evaluate-validation `
  "data\SoCal Cup Final_ MVVC 17 Red vs C2 Attack 17-1, 06_15_2025 [9ESOXojmAGI].mp4" `
  data\validation\socal_cup_c2_validation.json `
  runs\action-r3d18\best.pt `
  outputs\validation\socal-cup-c2-baseline `
  --repo-root . --video-root E:\Spike-Trace `
  --manifest data\annotations\usa_germany_2024_annotations_expanded_batch_02.csv `
  --stride-seconds 0.4 --confidence-threshold 0.5 `
  --merge-gap-seconds 0.25 --min-event-seconds 0.2 `
  --batch-size 8 --device cuda

spiketrace verify-validation `
  outputs\validation\socal-cup-c2-baseline `
  --repo-root . --video-root E:\Spike-Trace
```

在锁定前，真值草稿必须保持 prediction-blind：逐个回合确认覆盖、我方动作和可见性，或明确
写入 `no_c2_action=true`。当前人工时间精度为整秒；不能用裁判手势或模型候选猜测被遮挡动作。
只有 `verify-validation-truth` 成功后才允许加载 checkpoint。首次基线结果只属于 `val`，不进入
训练、伪标签、主动学习选择或缓存；之后的 checkpoint 使用新的输出目录。

提交时只提交本说明、冻结声明，以及经过人工确认后明确要保留的真值 JSON/CSV。绑定文件、真值
和输出均采用不可覆盖发布；若源视频变化，必须建立新的版本和新的输出目录。
