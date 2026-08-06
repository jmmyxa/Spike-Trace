# Spike-Trace

Spike-Trace 是一个面向排球比赛视频的本地分析软件。长期目标是识别我方球员的技术动作，将事件归属到球衣号码，并形成可保存、复核和导出的球员数据。

当前 MVP 聚焦动作模型闭环：既保留可持续微调的 R3D-18 视频分类基线，也支持接入已有 YOLO 排球动作权重，先用真实比赛验证兼容性并减少从零标注。应用界面、号码 OCR 和正式比赛数据库后置。

完整产品决策和后续路线见 [项目规划](docs/PROJECT_PLAN.md)。

## 已实现

- 内部七类动作数据契约：`background`、`serve`、`receive`、`set`、`attack`、`block`、`dig`；旧六类 checkpoint 继续兼容。
- CSV 标注清单校验，并强制按完整比赛隔离训练集和验证集。
- OpenCV 视频元数据读取和固定帧数片段采样。
- 可训练的 R3D-18 基线，以及只用于冒烟测试的 Tiny3D 模型。
- 可选的 Ultralytics YOLO 预训练动作检测适配层，不复制外部项目源码。
- 外部模型标签归一化、逐窗口预测、混淆矩阵和人工复核 CSV。
- 基于 JSON 规格生成精简二次复核队列，并将带来源快照的人工确认结果安全应用到新清单。
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
├─ data/annotations/
│  ├─ *_annotations.csv          # 保留的首轮人工复核清单
│  ├─ *_second_review.json       # 二次复核请求规格
│  ├─ *_second_review_results.json # 可审计的人工确认结果与来源快照
│  ├─ *_annotations_second_reviewed.csv # 当前训练清单
│  └─ *_match.json               # 比赛信息、当前清单指针与复核状态
├─ docs/
│  ├─ PROJECT_PLAN.md            # 产品边界、技术决策与阶段路线
│  └─ superpowers/               # 已确认的阶段设计与逐步实现计划
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
│  ├─ review.py                  # 二次复核队列生成、结果校验与新清单写出
│  ├─ timecode.py                # 视频秒数与可读时间互转
│  ├─ training.py                # R3D-18/Tiny3D 训练与验证
│  └─ video.py                   # 视频检查、原画幅帧采样和片段采样
├─ tests/                        # 不依赖真实视频或外部权重的单元测试
└─ tools/                        # 冒烟数据和人工复核辅助工具
```

动作模型采用两条可替换路径。自训练路径使用内部七类严格契约；外部 YOLO 保留上游六类能力契约：

```text
标注 CSV + 比赛视频
├─ 自训练路径：七类标注 -> R3D-18/Tiny3D -> 滑窗合并 -> events.json/events.csv
└─ 预训练路径：六类 YOLO -> 标签归一化 -> 严格七类/兼容六类指标 -> 复核 CSV
```

`receive` 仅表示美国队直接接对方发球的第一次触球；`dig` 表示针对对方扣球、吊球等进攻的防守起球，不含拦网。对方直接出界且美国队未触球时标为 `background`。非进攻 free ball 的我方首次触球本轮暂标为 `background`，并在备注写 `free-ball`。

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

程序按清单中的时间和半场裁剪采样原始画幅，在每个窗口内选择置信度最高的有效动作。没有有效检测时输出 `background`。该 YOLO 权重不包含 `dig`，外部标签统一转换为：

| 外部 YOLO 标签 | Spike-Trace 标签 | 处理方式 |
| --- | --- | --- |
| `ball` | 无 | 忽略，不作为动作 |
| `serve` | `serve` | 保留 |
| `receive` | `receive` | 保留 |
| `set` | `set` | 保留 |
| `spike` | `attack` | 归一化 |
| `block` | `block` | 保留 |

输出目录包含：

- `pretrained_evaluation.json`：权重 SHA-256、依赖版本、模型标签、设置、逐类指标、混淆矩阵和逐条检测证据；同时包含严格七类 `metrics` 和将真值 `dig` 映射为 `receive` 的六类 `compatibility_metrics`。
- `pretrained_review.csv`：同时提供原始秒数和 `HH:MM:SS.ss` 可读时间，并保留置信度、`correct` 和检测框供人工播放复核。

复核时以 `start_time` 和 `end_time` 跳转视频，例如 `00:12:11.60` 表示视频开始后的 12 分 11.60 秒。`correct=False` 只表示 `expected_action` 与 `predicted_action` 不同，不代表人工标注一定错误；必须播放视频后再决定保留或修改标签。

人工复核表以“人工确认动作”非空作为已复核标志，不设置单独的审核状态；无法确定的片段直接在备注中说明。

`compatibility_metrics` 只衡量旧六类模型发现宽泛防守触球候选的能力，不能覆盖人工 `dig` 真值，也不能作为七类部署成绩。

这一层参考 [volleyball_analytics](https://github.com/masouduut94/volleyball_analytics) 公布的动作类别，但直接通过 Ultralytics 加载权重，没有复制其 GPLv2 主仓库代码。其 ML 子仓库标注为 MIT，而下载权重和训练数据的授权范围仍需在重新分发前单独确认；Ultralytics 本身也有 AGPL-3.0/商业授权要求。外部项目公布的指标只能用于筛选候选模型，不能当作本项目在美国队视频上的准确率。

## 生成与应用二次复核

二次复核请求保存在可提交的 JSON 规格中，程序将其与首轮已复核标注合并为 UTF-8 BOM CSV：

```powershell
spiketrace prepare-review `
  data\annotations\usa_germany_2024_annotations.csv `
  data\annotations\usa_germany_2024_second_review.json `
  outputs\second-review\usa_germany_2024_second_review.csv
```

跨设备仅检查结构且本地没有原视频时，可加 `--allow-missing-videos`。当前规格包含原清单记录 `1, 19, 21, 22, 23, 27, 31, 32, 35, 39, 43, 46, 47, 53, 65, 66, 67`。CSV 保留来源、建议处理方式和 `HH:MM:SS.ss` 可读时间；人工确认动作、开始/结束时间和备注默认留空。人工确认动作非空即表示该行完成复核，不另设审核状态。

便于填写的 XLSX 视图、预览和视频均保存在忽略的 `outputs/` 中，不提交 Git。填写后的人工动作非空即表示已确认；本场只能精确到秒级，因此结果文件将 `time_precision_seconds` 记录为 `1.0`，小数时间仍按原值保留，例如记录 21 的开始时间为 `3660.5` 秒。

人工结论规范化为可提交的 `*_second_review_results.json` 后，使用以下命令写出新的标注清单：

```powershell
spiketrace apply-review `
  data\annotations\usa_germany_2024_annotations.csv `
  data\annotations\usa_germany_2024_second_review.json `
  data\annotations\usa_germany_2024_second_review_results.json `
  data\annotations\usa_germany_2024_annotations_second_reviewed.csv
```

`prepare-review` 不允许输出覆盖来源清单或复核规格；`apply-review` 也不允许覆盖来源清单、复核规格或确认结果。应用时要求 17 条确认完整、唯一且按规格顺序排列，并逐条精确校验视频、原动作、原时间、分组、我方位置、号码、裁剪和原备注；CSV 行多出表头之外的单元格也会被拒绝。动作必须属于七类，时间必须为非负有限秒数且满足开始小于结束。普通操作原位更新并追加审计备注；`add_window` 保留来源行并把新窗口追加到末尾。

新清单先写入目标目录内的临时文件，重新加载验证成功后才原子替换正式输出；任何写入或验证错误都会保留已有输出。验证期间相对 `video_path` 始终按来源清单的视频根解释，因此输出可以放在其他目录；CSV 内的相对路径不会被改写，后续单独读取异目录输出时需通过 `--video-root` 指向原视频根。跨设备缺少原视频时可加 `--allow-missing-videos`。

本场保留 67 条首轮清单作为历史输入，提交 17 条机器可读确认结果，并生成 68 条二次复核清单。记录 46 的原 `set` 保留，同时新增一条 `attack`。

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
- 67 个候选窗口的首轮人工复核清单继续保留；17 条二次复核已完成并应用，当前清单为
  68 条，比赛状态为 `second_pass_reviewed`。
- 当前分布为：`background` 39、`block` 9、`receive` 3、`dig` 4、`serve` 9、
  `set` 2、`attack` 2，总窗口时长 `60.3` 秒，全部属于 `train`。
- 二次复核原位更新 16 条，并从记录 46 追加 1 条 `attack`；记录 21 的 `3660.5`
  秒小数边界和全部人工备注均保留。

同一局内可以固定我方半场裁剪；换边后需要按局次切换裁剪。训练与验证必须使用
不同的完整比赛，不能把本场不同局拆到不同数据集分区。

### 预训练权重兼容性基线

2026-08-03 已完成上游六类 YOLO 权重的第一次本地实测，2026-08-04 使用人工复核标签重新评估：

- 权重 SHA-256：`bfd7f2354ff15c91839cbe987a069d5f04b2311d296989487c87fb04bddef109`。
- 环境：PyTorch `2.13.0+cpu`、Ultralytics `8.4.115`、CPU、每窗口 6 帧。
- 使用人工复核清单和美国队半场裁剪，阈值 `0.25`：Accuracy `0.776119`，Macro F1 `0.491001`。
- `background` 命中 41/45、`serve` 3/5、`receive` 3/6、`block` 5/8；`set` 0/2、
  `attack` 0/1。
- 高 Accuracy 主要来自占比 67% 的 `background`，不能说明动作模型已经可用；`set` 和
  `attack` 仍没有有效召回。

这些数字基于已归档的 67 条首轮清单，仍只是同一场比赛上的零样本兼容性检查，不是正式模型成绩。二次复核已重新对齐目标窗口，因此后续比较必须在当前 68 条清单上重新评估。现有权重可以辅助发现部分 `serve`、`receive` 和 `block` 候选，但不能直接用于自动统计；下一步是补足正样本并建立独立验证比赛，再考虑微调。

## 当前限制

当前只有一场真实比赛和 68 个二次复核窗口，没有可用于生产的模型权重。代码已经跑通
自训练工程链路、外部 YOLO 权重评估和人工复核回写，但数据仍以 39 个 `background` 为主，
`set`、`attack`、`receive` 和 `dig` 正样本远远不足。预训练动作检测只能减少“候选片段发现”
的工作，不能自动完成美国队过滤、球衣号码归属、发球成功率、得分结果、一传到位率或
上场时间。下一步是从更多完整回合补齐七类正样本，并加入至少一场独立比赛作为验证集，
再进行微调和正式比较。
