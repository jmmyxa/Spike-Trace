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
- 比赛元数据保存美国队每局近端/远端的已确认有效比赛区间和对应半场裁剪。
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
├─ .gitattributes                # 固定标注 CSV 换行，保证跨设备字节哈希稳定
├─ agent-lanes.md                # 持久 Agent Lane 注册表与互斥写入范围
├─ .agent-lanes/                 # 各 Lane 的工作日志
├─ data/annotations/
│  ├─ *_annotations.csv          # 保留的首轮人工复核清单
│  ├─ *_second_review.json       # 二次复核请求规格
│  ├─ *_second_review_results.json # 可审计的人工确认结果与来源快照
│  ├─ *_annotations_second_reviewed.csv # 二次复核后的来源清单
│  ├─ *_expansion_batch_*.json # 完整回合穷举补标批次规格/结果
│  ├─ usa_germany_2024_expansion_batch_02_results.json # 第二批已应用结果与来源快照
│  ├─ *_annotations_expanded_batch_*.csv # 应用补标后的训练清单
│  ├─ usa_germany_2024_annotations_expanded_batch_02.csv # 当前 90 条训练清单
│  └─ *_match.json               # 比赛信息、当前清单、复核状态与半场区间
├─ docs/
│  ├─ identity/                   # 球员身份与号码识别设计
│  ├─ product/                    # 产品规划与前端体验设计
│  ├─ data-platform/              # 数据平台与用户工作区设计
│  ├─ PROJECT_PLAN.md            # 产品边界、技术决策与阶段路线
│  └─ superpowers/               # 已确认的阶段设计与逐步实现计划
├─ examples/                     # 标注格式示例
├─ outputs/expansion-batch-01/
│  └─ *_expansion_batch_01.xlsx  # 可跨设备填写并提交的完整回合补标工作簿
├─ outputs/expansion-batch-02/
│  └─ *_expansion_batch_02.xlsx  # 已填写并纳入版本控制的第二批完整回合补标工作簿
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

### 持久 Agent Lane

当前项目拆分为三个长期协作任务：`player-identity` 负责美国队球员检测、跟踪和号码归属；
`product-frontend` 负责总体产品规划、导航以及标注/分析前端；`data-workspace` 负责数据存储、
版本审计、CSV/JSON 导入导出和账户/工作区数据契约。每个任务只能修改注册表中的自身范围，
工作进展记录在 `.agent-lanes/*/worklog.md`；号码识别尚未进入实现阶段，先完成设计和数据契约。

`data/annotations/*.csv` 固定使用 CRLF 换行；Python `csv` 写出的清单本身也采用这一格式。不要绕过 Git 属性手工转换这些文件的换行，否则用于基线和补标规格的 SHA-256 会变化。

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
  data\annotations\usa_germany_2024_annotations_expanded_batch_01.csv `
  checkpoints\volleyball-actions.pt `
  outputs\pretrained-usa-germany-expanded-batch-01 `
  --frames-per-window 6 `
  --confidence-threshold 0.25 `
  --device cpu
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

当前 `evaluate-pretrained` 只评估清单里已经给出的时间窗，不会从整场比赛自动发现新动作。`infer` 虽能执行整场滑窗推理，但只接受 Spike-Trace 自训练 checkpoint，不能直接使用这份外部 YOLO 权重。因此现阶段新增训练样本仍要先人工选出完整回合，再对回合内所有美国队动作做穷举标注。

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

便于填写的临时 XLSX 视图、预览和视频通常保存在忽略的 `outputs/` 中，不提交 Git。完整回合补标工作簿是例外，会明确纳入版本控制，便于跨设备继续填写。填写后的人工动作非空即表示已确认；本场只能精确到秒级，因此结果文件将 `time_precision_seconds` 记录为 `1.0`，小数时间仍按原值保留，例如记录 21 的开始时间为 `3660.5` 秒。

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

## 完整回合补标

新基线确认外部 YOLO 会漏掉大量正类，因此下一批不再从模型预测中挑选，而是完整播放指定回合，只补录当前清单缺少的美国队动作。第一批规格为 `data/annotations/usa_germany_2024_expansion_batch_01.json`，包含 6 段、共 70.6 秒，按补标优先级排列：

| 视频时间 | 美国队位置 | 选择原因 |
| --- | --- | --- |
| `01:55:40-01:55:54` | 近端 | 7 条现有窗口全是背景，但实际是含多次防守和转换的长回合 |
| `01:13:10-01:13:19.4` | 远端 | 多次往返中 `01:13:15-01:13:17` 的转换阶段未覆盖 |
| `01:00:56.8-01:01:03` | 远端 | 开头有举球式触球，随后还有拦防动作 |
| `00:06:10-00:06:18` | 远端 | 两个已有拦网之前存在处理球和攻防转换 |
| `00:21:58-00:22:08` | 远端 | 目前只有发球，需检查对方进攻后的拦防和二传 |
| `01:19:05-01:19:28` | 远端 | 后 9 秒包含发球、对方进攻和美国队防守转换 |

填写入口为 `outputs/expansion-batch-01/usa_germany_2024_expansion_batch_01.xlsx`，该文件会随仓库同步到其他设备。在“完整回合”页看完整段后把“人工确认完整回合”选为“是”；只把缺少的动作写入“新增动作”页，时间直接填视频播放器显示的 `HH:MM:SS`，允许只估到秒。已有动作保持不变，空白动作行会被忽略，工作簿不包含模型结果、`False` 或单独审核状态。

第一批已完成：6/6 个回合确认，动作页为空，但 R19 和 R06 在“完整回合”备注中记录了 8 个明确动作。结果文件 `data/annotations/usa_germany_2024_expansion_batch_01_results.json` 保留了工作簿 SHA-256、单元格位置、原始备注和来源快照；明确写着“没碰到球”的 attack 未加入，R11、R02、R14 各移除一条后出现的重复窗口，同时保留不可变的 68 条来源清单。

应用后的训练清单为 `data/annotations/usa_germany_2024_annotations_expanded_batch_01.csv`，共 73 条、64.7 秒：新增 `receive` 1、`set` 2、`attack` 1、`block` 3、`dig` 1，同时移除重复的 `block` 2 和 `dig` 1，净增 5 条。原 68 条二次复核清单保持不变，历史基线摘要保留在比赛元数据中；扩展清单仍全部属于同一场比赛的 `train`，不能当作独立验证集。

后续批次仍保留原工作簿路径和文件名：在“完整回合”页确认看完，在“新增动作”页填写动作和时间；若动作写在备注中，必须使用同样的绝对视频时间并明确标签，之后需由 Codex 或人工规范化，并把备注单元格作为来源单独记录。

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
- 67 个候选窗口的首轮人工复核清单继续保留；17 条二次复核及两批完整回合补标均已应用，
  当前训练清单为 90 条，比赛状态为 `expansion_batch_02_applied`。
- 当前训练清单分布为：`attack` 8、`background` 39、`block` 12、`dig` 6、`receive` 6、
  `serve` 11、`set` 8，总窗口时长 `80.9` 秒，全部属于 `train`。
- 二次复核原位更新 16 条，并从记录 46 追加 1 条 `attack`；记录 21 的 `3660.5`
  秒小数边界和全部人工备注均保留。
- 第一批完整回合已确认 6/6 段，应用 8 个新增动作并移除 3 个重复窗口；结果 JSON 保留
  工作簿哈希、单元格来源、原始备注、删除与保留记录快照。动作页为空的事实也已记录，
  不能把这批结果误解为模型自动标注。
- 第二批完整回合已确认 6/6 段，动作页为空；从“完整回合”备注规范化新增 17 个美国队动作。
  `B02-R05` 的 `background`（对手失误）只保留为忽略备注，未新增美国队动作；`B02-R04` 同秒
  `set`/快攻经原视频 0.1 秒密集抽帧细化为 `5655.9-5656.5` 和 `5656.6-5657.2`，不重叠。

比赛元数据的 `usa_side_segments` 现保存以下已确认的主动比赛区间：

| 局次/阶段 | 视频时间 | 秒数 | 美国队位置 | 裁剪 |
| --- | --- | ---: | --- | --- |
| 第 1 局 | `00:01:14-00:25:04` | `74-1504` | 远端 `far` | `0,0,1280,430` |
| 第 2 局 | `00:28:44-00:51:24` | `1724-3084` | 近端 `near` | `0,170,1280,720` |
| 第 3 局 | `00:55:23-01:19:36` | `3323-4776` | 远端 `far` | `0,0,1280,430` |
| 第 4 局 | `01:23:34-01:48:03` | `5014-6483` | 近端 `near` | `0,170,1280,720` |
| 第 5 局换边前 | `01:51:51-01:58:31` | `6711-7111` | 近端 `near` | `0,170,1280,720` |
| 第 5 局换边后 | `01:59:54-02:07:55` | `7194-7675` | 远端 `far` | `0,0,1280,430` |

这些值来自关键边界抽帧视觉检查，并由用户于 2026-08-09 确认；精度为 1 秒，
可作为后续标注和扫描的工作区间，但不能当作正式裁判记录。
每段有唯一 `segment_id`；第 5 局用 `set_5_pre_switch` 和 `set_5_post_switch` 明确区分换边前后。
边界语义为 `inclusive_candidate`，并记录 `boundary_tolerance_seconds: 1.0`；下游生成滑窗时应
在边界两侧保留这 1 秒余量，再按实际动作窗口裁切。
它们只描述含有效回合的比赛范围；局间和第 5 局换边空档会保留为空白，不应自动套用前一段
裁剪，局内暂停仍属于该局范围。现有 20 个复核区间均被同侧区间唯一覆盖；这些段已确认，
后续整场扫描可以直接按它们过滤。

同一局内可以固定我方半场裁剪；第 5 局中途换边后必须切换裁剪。训练与验证必须使用
不同的完整比赛，不能把本场不同局拆到不同数据集分区。

### 预训练权重兼容性基线

2026-08-09 已在第一批 73 条扩展清单上重跑上游六类 YOLO 权重；68 条二次复核清单的结果保留为直接对比基线：

- 权重 SHA-256：`bfd7f2354ff15c91839cbe987a069d5f04b2311d296989487c87fb04bddef109`。
- 环境：PyTorch `2.13.0+cpu`、Ultralytics `8.4.115`、CPU、每窗口 6 帧。
- 使用当前清单、美国队半场裁剪和阈值 `0.25`：严格七类 Accuracy `0.630137`
  （46/73），Macro F1 `0.344159`；六类兼容 Macro F1 `0.366886`。
- 逐类 Recall：`background` 94.9%、`serve` 22.2%、`receive` 50.0%、`set` 25.0%、
  `attack` 0%、`block` 40.0%、`dig` 0%。
- 最大错分组为 `serve -> background` 7 条、`block -> background` 3 条、
  `block -> set` 3 条。
- 和 68 条结果相比，共同 65 个窗口的预测没有变化；去重移除的 3 条中有 1 条预测正确，
  新增 8 条中也只有 1 条预测正确，因此正确数保持 46，Accuracy 下降 `0.046334`。

68 条基线 Accuracy `0.676471`、Macro F1 `0.353654` 和 67 条首轮历史结果仍保留；73 条结果的下降来自清单去重和新增困难正类，不能当作模型代码回归。该外部预训练 YOLO 基线只对应第一批 73 条清单，第二批当前 90 条清单尚未重跑，不能混称。全部数据仍来自同一场比赛，因此这只是零样本兼容性检查，不是正式模型成绩。完整评估和逐窗 CSV 保存在本地忽略目录 `outputs/pretrained-usa-germany-expanded-batch-01/`；可复现参数、产物哈希、当前指标、68 条历史摘要和差异保存在比赛元数据的 `pretrained_baseline` 中。

### 第二批完整回合补标（已应用）

六段美国队半场边界已确认，第二批从每段各选一个新的完整回合，合计 6 段、85 秒，
每段预留 8 个动作槽位；区间均避开其来源的第一批 73 条清单窗口：

| 批次编号 | 来源段 | 视频时间 | 美国队位置 |
| --- | --- | --- | --- |
| `B02-R01` | `set_1` | `00:13:57-00:14:06` | 远端 `far` |
| `B02-R02` | `set_2` | `00:39:35-00:39:47` | 近端 `near` |
| `B02-R03` | `set_3` | `01:10:09-01:10:22` | 远端 `far` |
| `B02-R04` | `set_4` | `01:34:10-01:34:29` | 近端 `near` |
| `B02-R05` | `set_5_pre_switch` | `01:52:56-01:53:14` | 近端 `near` |
| `B02-R06` | `set_5_post_switch` | `02:02:58-02:03:12` | 远端 `far` |

第二批于 2026-08-10 应用：6/6 个完整回合均已确认。规格为
`data/annotations/usa_germany_2024_expansion_batch_02.json`；已填写并纳入版本控制的工作簿为
`outputs/expansion-batch-02/usa_germany_2024_expansion_batch_02.xlsx`，SHA-256 为
`6054c3abc54d8ac1dda7cdfec0a8297501e6aee63c2239d4966b962529180a58`。其来源第一批清单
`usa_germany_2024_annotations_expanded_batch_01.csv` 的 SHA-256 为
`2ad5ce8d46e5f065c76d82d641f0fb824f5e8da9457df7bde5466d110cc12981`。

“新增动作”页为空，17 个美国队动作均从“完整回合”备注规范化；`B02-R05` 的 `background`
（对手失误）仅作为忽略备注，不新增美国队动作。`B02-R04` 同秒的 `set`/快攻经原视频
0.1 秒密集抽帧细化为 `set 5655.9-5656.5`、`attack 5656.6-5657.2`，两个窗口不重叠。

结果为 `data/annotations/usa_germany_2024_expansion_batch_02_results.json`，输出清单为
`data/annotations/usa_germany_2024_annotations_expanded_batch_02.csv`，其 SHA-256 为
`17fab8ffa9bf6d77c896491aa7da7e15920679ff9bda8cbb207259e89815ef2d`。当前清单共 90 条、
80.9 秒：`attack` 8、`background` 39、`block` 12、`dig` 6、`receive` 6、`serve` 11、`set` 8，
全部属于 `train`；原 73 条第一批清单保持不变。

## 当前限制

当前只有一场真实比赛和 90 个训练窗口，没有可用于生产的模型权重。代码已经跑通
自训练工程链路、外部 YOLO 权重评估和人工复核回写，但数据仍以 39 个 `background` 为主，
`set`、`attack`、`receive` 和 `dig` 正样本远远不足。当前外部 YOLO 只能给已有窗口提供
预测证据，不能自动扫描整场，也不能完成美国队过滤、球衣号码归属、发球成功率、得分
结果、一传到位率或上场时间。下一步继续扩大正类完整回合数据，并尽快加入另一场完整比赛作为
独立验证集，再进行微调和正式比较。
