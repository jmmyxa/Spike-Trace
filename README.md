# Spike-Trace

Spike-Trace 是一个面向排球比赛视频的本地分析软件。长期目标是识别我方球员的技术动作，将事件归属到球衣号码，并形成可保存、复核和导出的球员数据。

当前 MVP 聚焦动作模型闭环：既保留可持续微调的 R3D-18 视频分类基线，也支持接入已有 YOLO 排球动作权重，先用真实比赛验证兼容性并减少从零标注。应用界面、号码 OCR 和正式比赛数据库后置。

完整产品决策和后续路线见 [项目规划](docs/PROJECT_PLAN.md)。

## 已实现

- 六类动作数据契约：`background`、`serve`、`receive`、`set`、`attack`、`block`。
- CSV 标注清单校验，并强制按完整比赛隔离训练集和验证集。
- OpenCV 视频元数据读取和固定帧数片段采样。
- 可训练的 R3D-18 基线，以及只用于冒烟测试的 Tiny3D 模型。
- 可选的 Ultralytics YOLO 预训练动作检测适配层，不复制外部项目源码。
- 外部模型标签归一化、逐窗口预测、混淆矩阵和人工复核 CSV。
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

如需评估外部 YOLO 动作权重，安装可选依赖：

```powershell
python -m pip install -e ".[pretrained]"
```

macOS 或 Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

程序会依次选择 CUDA、Apple MPS 或 CPU。使用预训练 R3D-18 时，torchvision 首次运行需要下载公开权重。

## 当前程序结构

README 必须随模块或目录变更同步更新。当前结构和职责如下：

```text
Spike-Trace/
├─ data/annotations/             # 可提交的清单与比赛元数据；原视频不进入 Git
├─ docs/PROJECT_PLAN.md          # 产品边界、技术决策与阶段路线
├─ examples/                     # 标注格式示例
├─ src/spiketrace/
│  ├─ cli.py                     # spiketrace 命令入口
│  ├─ constants.py               # 稳定动作标签与格式版本
│  ├─ domain.py                  # 标注、窗口、事件等数据对象
│  ├─ errors.py                  # 可操作的命令行错误
│  ├─ events.py                  # 滑窗结果合并为动作事件
│  ├─ inference.py               # R3D-18/Tiny3D 整场滑窗推理
│  ├─ manifest.py                # 标注 CSV 加载、校验与摘要
│  ├─ metrics.py                 # 分类指标和混淆矩阵
│  ├─ ml.py                      # PyTorch 模型、设备和 checkpoint
│  ├─ outputs.py                 # 事件 JSON/CSV 输出
│  ├─ pretrained.py              # 外部 YOLO 适配、标签归一化与评估
│  ├─ training.py                # R3D-18/Tiny3D 训练与验证
│  └─ video.py                   # 视频检查、原画幅帧采样和片段采样
├─ tests/                        # 不依赖真实视频或外部权重的单元测试
└─ tools/                        # 冒烟数据和人工复核辅助工具
```

动作模型采用两条可替换路径，并共享同一套六类标签与指标：

```text
标注 CSV + 比赛视频
├─ 自训练路径：片段采样 -> R3D-18/Tiny3D -> 滑窗合并 -> events.json/events.csv
└─ 预训练路径：原画幅采样 -> YOLO 检测 -> 标签归一化 -> 评估 JSON/复核 CSV
```

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

## 评估预训练 YOLO 动作模型

本仓库只提供兼容层，不附带第三方模型权重。上游提供的 [Action detection 下载项](https://drive.google.com/file/d/1o-KpRVBbjrGbqlT8tOjFv91YS8LIJZw2/view) 实际是 ZIP 压缩包；从中提取 `action_detection/6_class/1/weights/best.pt` 为本地 `.pt` 权重后，可先在现有标注窗口上评估：

```powershell
spiketrace evaluate-pretrained `
  data\annotations\usa_germany_2024_annotations.csv `
  checkpoints\volleyball-actions.pt `
  outputs\pretrained-usa-germany `
  --frames-per-window 6 `
  --confidence-threshold 0.25 `
  --device auto
```

程序按清单中的时间和半场裁剪采样原始画幅，在每个窗口内选择置信度最高的有效动作。没有有效检测时输出 `background`。外部标签统一转换为：

| 外部 YOLO 标签 | Spike-Trace 标签 | 处理方式 |
| --- | --- | --- |
| `ball` | 无 | 忽略，不作为动作 |
| `serve` | `serve` | 保留 |
| `receive` | `receive` | 保留 |
| `set` | `set` | 保留 |
| `spike` | `attack` | 归一化 |
| `block` | `block` | 保留 |

输出目录包含：

- `pretrained_evaluation.json`：权重 SHA-256、依赖版本、模型标签、设置、逐类指标、混淆矩阵和逐条检测证据。
- `pretrained_review.csv`：预期标签、预测标签、置信度和检测框，供人工播放复核。

这一层参考 [volleyball_analytics](https://github.com/masouduut94/volleyball_analytics) 公布的动作类别，但直接通过 Ultralytics 加载权重，没有复制其 GPLv2 主仓库代码。其 ML 子仓库标注为 MIT，而下载权重和训练数据的授权范围仍需在重新分发前单独确认；Ultralytics 本身也有 AGPL-3.0/商业授权要求。外部项目公布的指标只能用于筛选候选模型，不能当作本项目在美国队视频上的准确率。

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
- 当前清单共 67 个 provisional 窗口：`attack` 13、`background` 12、`block` 12、
  `receive` 12、`serve` 7、`set` 11。

同一局内可以固定我方半场裁剪；换边后需要按局次切换裁剪。训练与验证必须使用
不同的完整比赛，不能把本场不同局拆到不同数据集分区。

### 预训练权重兼容性基线

2026-08-03 已完成上游六类 YOLO 权重的第一次本地实测：

- 权重 SHA-256：`bfd7f2354ff15c91839cbe987a069d5f04b2311d296989487c87fb04bddef109`。
- 环境：PyTorch `2.13.0+cpu`、Ultralytics `8.4.115`、CPU、每窗口 6 帧。
- 使用清单中的美国队半场裁剪，阈值 `0.25`：Accuracy `0.268657`，Macro F1 `0.210812`。
- 12 个 `background` 全部命中，7 个 `serve` 命中 2 个；13 个 `attack` 命中 0 个。
- 阈值降到 `0.10` 后 Accuracy `0.253731`、Macro F1 `0.201573`，`attack` 召回仍为 0。

这些数字来自同一场比赛的 provisional 标签，只是零样本兼容性检查，不是正式模型成绩。它说明当前权重存在明显数据域差异，不能直接用于自动统计；合理用法是生成候选检测框、加速人工复核，再用本项目比赛数据微调。

## 当前限制

当前只有一场真实比赛和少量待复核试标注，没有可用于生产的模型权重。代码已经跑通
自训练工程链路，并具备外部 YOLO 权重的本地兼容性评估入口，但尚未用真实权重完成
本场正式基线。预训练动作检测只能减少“候选片段发现”的标注工作，不能自动完成美国队
过滤、球衣号码归属、发球成功率、得分结果、一传到位率或上场时间。下一步应取得授权
清晰的候选权重；当前候选已经完成零样本测试，下一步应人工复核检测框，并用修正后的
本项目数据微调 YOLO，同时继续扩充 R3D-18 数据。正式评估仍需至少一场独立比赛。
