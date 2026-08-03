# Spike-Trace

Spike-Trace 是一个面向排球比赛视频的本地分析软件。长期目标是识别我方球员的技术动作，将事件归属到球衣号码，并形成可保存、复核和导出的球员数据。

当前 MVP 聚焦动作模型闭环：标注视频、训练模型、整场滑窗推理、事件合并，以及 JSON/CSV 输出。应用界面、号码 OCR 和正式比赛数据库后置。

完整产品决策和后续路线见 [项目规划](docs/PROJECT_PLAN.md)。

## 已实现

- 六类动作数据契约：`background`、`serve`、`receive`、`set`、`attack`、`block`。
- CSV 标注清单校验，并强制按完整比赛隔离训练集和验证集。
- OpenCV 视频元数据读取和固定帧数片段采样。
- 可训练的 R3D-18 基线，以及只用于冒烟测试的 Tiny3D 模型。
- 训练集/验证集损失、逐类 Precision/Recall/F1 和混淆矩阵。
- 可复现 checkpoint，包含标签、预处理参数、指标和模型版本。
- 整场视频滑动窗口推理、动作事件合并、JSON/CSV 导出。

## 环境安装

需要 Python 3.10 或更高版本。建议使用虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

macOS 或 Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

程序会依次选择 CUDA、Apple MPS 或 CPU。使用预训练 R3D-18 时，torchvision 首次运行需要下载公开权重。

## 准备标注

标注格式参考 [示例清单](examples/annotations.example.csv)：

```csv
video_path,start_seconds,end_seconds,label,team_side,player_number,crop_x1,crop_y1,crop_x2,crop_y2,split
videos/match_001.mp4,12.40,14.10,serve,far,8,0,0,1280,430,train
videos/match_010.mp4,28.50,30.00,attack,near,12,0,170,1280,720,val
```

同一个视频不能同时出现在 `train`、`val` 或 `test` 中。先校验清单：

```powershell
spiketrace validate-manifest data\annotations.csv
spiketrace inspect-video data\videos\match_001.mp4
```

## 训练 R3D-18

```powershell
spiketrace train data\annotations.csv runs\r3d18-v01 `
  --model r3d18 `
  --model-version action-r3d18-v0.1 `
  --pretrained `
  --epochs 20 `
  --batch-size 4
```

训练目录会生成：

- `best.pt`：验证集 Macro F1 最好的权重。
- `latest.pt`：最后一个 epoch 的权重。
- `training_config.json`：训练配置和数据集摘要。
- `metrics.json`：每个 epoch 的完整评估结果。

## 整场视频推理

```powershell
spiketrace infer data\videos\match_020.mp4 runs\r3d18-v01\best.pt outputs\match_020 `
  --stride-seconds 0.4 `
  --confidence-threshold 0.6 `
  --crop 0,0,1280,430
```

`crop_x1` 至 `crop_y2` 用于只保留目标球队所在半场。四项全部留空表示使用完整画面；推理时使用相同的 `--crop`。完整比赛发生换边时，应按局次分别推理对应半场。

输出目录包含 `events.json` 和带 UTF-8 BOM 的 `events.csv`。JSON 同时保留每个滑窗的原始预测，方便以后校对和重新合并事件。

## 冒烟测试

以下命令生成纯人工图形视频，用来验证程序链路。它不能衡量真实排球识别能力：

```powershell
python tools\generate_smoke_dataset.py data\smoke
spiketrace train data\smoke\annotations.csv runs\smoke `
  --model tiny3d --model-version smoke-tiny3d-v0 `
  --epochs 1 --batch-size 6 --num-frames 4 --image-size 32 `
  --window-seconds 1 --device cpu
spiketrace infer data\smoke\val_01.avi runs\smoke\best.pt outputs\smoke `
  --stride-seconds 0.5 --confidence-threshold 0 --device cpu
```

运行单元测试：

```powershell
python -m unittest discover -s tests -v
```

## 当前真实视频试验

首个真实视频样本为 2024 奥运会美国队对德国队的完整比赛。当前约定：

- 我方球队：美国队（USA），本场穿深蓝色球衣。
- 视频规格：1280x720、30 FPS、约 2 小时 8 分钟。
- 首个试标注区间：`728.0-735.0` 秒，美国队位于远端半场。
- 远端裁剪：`0,0,1280,430`；近端建议裁剪：`0,170,1280,720`。
- 试标注包含 `background`、`receive`、`set`、`attack` 和 `block`，状态为
  `provisional`，只用于检查数据与模型链路，尚未作为准确率依据。

同一局内可以固定我方半场裁剪；换边后需要按局次切换裁剪。训练与验证必须使用
不同的完整比赛，不能把本场不同局拆到不同数据集分区。

## 当前限制

当前只有一场真实比赛和少量待复核试标注，没有可用于生产的模型权重。代码已经跑通
工程链路，但不能据此声称具备可靠的排球识别准确率。下一步需要扩充并复核美国队
动作标注，再加入至少一场独立比赛作为验证集，建立按比赛隔离的基线评估。
