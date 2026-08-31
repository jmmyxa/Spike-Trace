# Spike-Trace

Spike-Trace 是一个面向排球比赛视频的本地分析软件。长期目标是识别我方球员的技术动作，将事件归属到球衣号码，并形成可保存、复核和导出的球员数据。

当前 MVP 已跑通动作模型、人工复核和事件导出的工程闭环。Rangitoto 首轮 40 段工作簿已经填写完成，共有 83 条人工记录；原工作簿保持不变。审计发现 v1 不能安全表达遮挡、镜头外、裁判推断、`free_ball` 和多人拦网归属，因此当前结果不会直接写入训练清单，而是先按已确认的证据分层设计迁移为 v2 权威结果。独立评估继续使用未参与训练的另一场完整比赛；号码识别运行时代码、前端和 SQLite 暂不启动。

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
- 整场视频滑动窗口推理（单次顺序解码整段视频）、动作事件合并、JSON/CSV 导出。

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
├─ .gitattributes                # 固定标注 CSV 与确定性审计 CSV 换行，保证跨设备字节稳定
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
├─ data/active-learning/
│  └─ rangitoto/
│     └─ round-01-selection.json # 可复现的首轮 40 段选片规格；视频和工作簿不入库
├─ docs/
│  ├─ data-platform/              # 数据平台与用户工作区设计
│  │  ├─ data-platform-design.md  # SQLite、文件资产、版本与 API 边界
│  │  ├─ import-export-contract.md # CSV/JSON 导入导出契约
│  │  └─ account-workspace-design.md # 账户与工作区页面数据契约
│  ├─ identity/
│  │  ├─ 2026-08-10-audit.md      # 号码归属相关仓库审计
│  │  └─ 2026-08-10-design.md     # 检测、跟踪、号码与人工复核契约
│  ├─ product/
│  │  └─ mvp-workflow-design.md   # 导入、复核、评估、统计和导出流程
│  ├─ PROJECT_PLAN.md            # 产品边界、技术决策与阶段路线
│  └─ superpowers/               # 已确认的阶段设计与逐步实现计划；含动作证据、遮挡和参与者契约
├─ examples/                     # 标注格式示例
├─ outputs/expansion-batch-01/
│  └─ *_expansion_batch_01.xlsx  # 可跨设备填写并提交的完整回合补标工作簿
├─ outputs/expansion-batch-02/
│  └─ *_expansion_batch_02.xlsx  # 已填写并纳入版本控制的第二批完整回合补标工作簿
├─ outputs/rangitoto-r3d18-bootstrap-review/
│  ├─ merged_candidates.json     # format-2 自包含窗口证据；Task 5 重算后的最终 JSON
│  ├─ merged_candidates.csv      # 与最终 JSON 一致的候选便携表格
│  └─ rangitoto_action_review.xlsx # 由已验证 JSON 派生的全量审计工作簿；不要求逐行填写
├─ outputs/active-learning/rangitoto/
│  ├─ round-01/                 # 本地忽略的 40 段代理、manifest 和待填 review.xlsx
│  └─ round-01-previews/        # 本地忽略的四页工作簿预览
├─ src/spiketrace/
│  ├─ active_learning_selection.py # 主动学习选片的稳定公共入口
│  ├─ active_learning_review.py  # 应用主动学习人工结论、硬负样本与累计清单
│  ├─ _active_learning_selection_contract.py # 选片 schema、时间与设置纯函数
│  ├─ _active_learning_selection_artifact.py # 来源信任链、制品校验与安全写入
│  ├─ _active_learning_review_contract.py # 冻结复核输入、字节校验与证据观察契约
│  ├─ _active_learning_selector.py # 五桶候选生成与确定性编排
│  ├─ cli.py                     # spiketrace 命令入口
│  ├─ constants.py               # 稳定动作标签与格式版本
│  ├─ domain.py                  # 标注、窗口、事件等数据对象
│  ├─ dual_crop_review.py         # 确定性双裁剪合并、自包含验证与 JSON/CSV 输出
│  ├─ errors.py                  # 可操作的命令行错误
│  ├─ events.py                  # 滑窗结果合并为动作事件
│  ├─ inference.py               # R3D-18/Tiny3D 整场滑窗推理，复用单次顺序视频解码
│  ├─ manifest.py                # 标注 CSV 加载、校验与摘要
│  ├─ metrics.py                 # 分类指标和混淆矩阵
│  ├─ ml.py                      # PyTorch 模型、设备和 checkpoint
│  ├─ outputs.py                 # 事件 JSON/CSV 输出
│  ├─ pretrained.py              # 外部 YOLO 适配、标签归一化与评估
│  ├─ review_batch.py            # 主动学习 40 段静音代理视频批次与 manifest 写出
│  ├─ review.py                  # 二次复核队列生成、结果校验与新清单写出
│  ├─ timecode.py                # 视频秒数与可读时间互转
│  ├─ training.py                # R3D-18/Tiny3D 训练与验证
│  └─ video.py                   # 视频检查、原画幅帧采样和片段采样
├─ tests/
│  ├─ fixtures/dual_crop_review/ # 四窗口 far/near inference JSON v2 字面 fixture
│  ├─ test_active_learning_selection.py # 主动学习选片制品契约与信任链测试
│  ├─ test_active_learning_review.py # 人工结论应用、硬负样本与双输出回滚测试
│  ├─ test_active_learning_review_contract.py # 冻结证据输入与严格观察契约测试
│  ├─ test_dual_crop_review.py   # 双裁剪合并、篡改拒绝、规模与 CLI 测试
│  ├─ test_outputs.py            # inference JSON v2 窗口成员索引输出测试
│  ├─ test_review_batch.py       # 主动学习代理视频批次 manifest、原子写入与 CLI 测试
│  └─ ...                        # 其余不依赖真实视频或外部权重的单元测试
└─ tools/
   ├─ build_active_review_batch.mjs # 由选片 JSON 与代理 manifest 原子构建四页 40 段复核工作簿
   ├─ verify_active_review_batch.mjs # 验证短片、代理哈希、XLSX 投影、公式和人工输入边界
   ├─ extract_active_review_results.mjs # 读取完成的 40 段工作簿并硬链接发布不可覆盖的复核草稿 JSON
   ├─ active_review_evidence_overrides.mjs # 严格校验哈希绑定的证据覆盖信封与引用
   ├─ active_review_workbook_semantics.mjs # 对复核工作簿执行哈希绑定的语义等价、修复审计与动作行规范化
   ├─ compose_active_review_evidence.mjs # 将冻结的选择、工作簿和证据覆盖合成为不可覆盖的 v2 证据输入
   ├─ test_active_review_batch.mjs # 合成 40 段工作簿、预览、篡改拒绝与回滚可执行测试
   ├─ test_active_review_evidence.mjs # 证据覆盖信封、哈希绑定与引用校验测试
   ├─ build_rangitoto_review.mjs # 从已验证 format-2 JSON 构建四页复核工作簿和预览
   ├─ verify_rangitoto_review.mjs # 独立验证工作簿结构、行数、空白输入与公式
   ├─ test_rangitoto_review.mjs  # 四窗口 fixture 的可执行 XLSX 集成测试
   └─ ...                        # 冒烟数据和其他人工复核辅助工具
```

### 持久 Agent Lane

当前项目拆分为三个长期协作任务：`player-identity` 负责美国队球员检测、跟踪和号码归属；
`product-frontend` 负责总体产品规划、导航以及标注/分析前端；`data-workspace` 负责数据存储、
版本审计、CSV/JSON 导入导出和账户/工作区数据契约。每个任务只能修改注册表中的自身范围，
工作进展记录在 `.agent-lanes/*/worklog.md`。三条 Lane 的第一阶段设计已经汇入主分支；号码识别、前端和数据平台尚未进入运行时代码实现。

设计入口：

- [球员身份与号码归属设计](docs/identity/2026-08-10-design.md)
- [MVP 产品工作流](docs/product/mvp-workflow-design.md)
- [数据平台与工作区设计](docs/data-platform/README.md)
- [Rangitoto 主动学习设计](docs/superpowers/specs/2026-08-16-rangitoto-active-learning-design.md)
- [动作证据、遮挡与球员参与者设计](docs/superpowers/specs/2026-08-30-action-evidence-occlusion-design.md)
- [Rangitoto 证据分层迁移实现计划](docs/superpowers/plans/2026-08-30-rangitoto-evidence-aware-review.md)
- [Rangitoto 首轮 40 段主动学习实现计划](docs/superpowers/plans/2026-08-16-rangitoto-active-learning-round-01.md)
- [R3D-18 高效分阶段微调实现计划](docs/superpowers/plans/2026-08-16-action-model-efficient-finetuning.md)
- [第二轮完整概率与不确定性选样实现计划](docs/superpowers/plans/2026-08-16-active-learning-round-two-scoring.md)

上述四份实现计划已经确认，执行顺序固定为：先把已完成的首轮 40 段工作簿迁移为证据分层结果；该结果和独立
`val` 比赛就绪后实现并运行两阶段微调；只有新 checkpoint 生成后，才升级完整概率输出并
制作第二轮 margin/entropy 复核批次。

两阶段微调的训练模型固定从全新 Kinetics 权重开始；旧 bootstrap checkpoint 只用于同一
`val` 上的独立对照。训练清单中每个 `train`、`val` 或 `test` 行都必须有真实比赛级
`match_id`，并通过所有分区之间的比赛与视频内容防泄漏检查。微调还必须同时读取对应轮次的
results JSON，核对权威累计清单哈希和派生清单的逐行前缀，不能只凭轮次名称字符串声明数据来源。

下一阶段按以下顺序推进：

1. 保留已填写的 Rangitoto 首轮工作簿，按证据分层设计迁移 83 条人工记录并生成安全的累计训练清单。
2. 使用未加入训练的另一场完整比赛建立固定 `val`，以后再保留第三场完整比赛作为 `test`。
3. 动作模型基线稳定后，在少量已标注回合上实现人员检测、短期跟踪和人工号码确认，不先承诺全自动 OCR。
4. 把确认的号码归属写入版本化结果，并与现有 `ActionEvent` 关联。
5. 最后接入本地浏览器界面、SQLite 保存和 CSV/JSON 导出，形成端到端 MVP。

`data/annotations/*.csv` 固定使用 CRLF 换行；Python `csv` 写出的清单本身也采用这一格式。不要绕过 Git 属性手工转换这些文件的换行，否则用于基线和补标规格的 SHA-256 会变化。

动作模型采用两条可替换路径。自训练路径使用内部七类严格契约；外部 YOLO 保留上游六类能力契约：

```text
标注 CSV + 比赛视频
├─ 自训练路径：七类标注 -> R3D-18/Tiny3D -> 滑窗合并 -> events.json/events.csv
└─ 预训练路径：六类 YOLO -> 标签归一化 -> 严格七类/兼容六类指标 -> 复核 CSV
```

`receive` 仅表示我方直接接对方发球的第一次触球；`dig` 表示针对对方扣球、吊球等进攻的防守起球，不含拦网。对方直接出界且我方未触球时标为 `background`。复核层把被动送过网的非进攻触球保存为 `free_ball`；当前七类训练投影仅把直接可见的 `free_ball` 映射为 `background`，并在权威结果中保留原标签。它不计为 `attack`，是否失误由独立结果记录决定。

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

默认情况下，训练清单必须同时包含 `train` 和 `val` 记录，`best.pt` 按验证集 Macro F1 选择。对于尚未拥有独立比赛验证集的明确 bootstrap 场景，可以显式加入 `--allow-train-only`；程序会跳过验证 loader 和验证 epoch，并按训练集 Macro F1 选择 `best.pt`。此时 `training_config.json` 和 `metrics.json` 会标记 `selection_split: "train"`、`generalization_metrics_available: false` 和 `allow_train_only: true`，每个 epoch 仅报告训练指标。

这条 `--allow-train-only` 路径只是当前 bootstrap 兼容行为；主动学习实现必须改为固定轮数或使用独立验证信号，不能把训练集 Macro F1 当作模型选择或泛化证据。

例如，当前 90 条已确认的美国队对德国队窗口全部正确保留在 `train` 分区，可先训练一个用于生成 Rangitoto 候选、供人工复核的 R3D-18 checkpoint：

```powershell
spiketrace train `
  data\annotations\usa_germany_2024_annotations_expanded_batch_02.csv `
  runs\rangitoto-r3d18-bootstrap `
  --model r3d18 `
  --model-version rangitoto-r3d18-bootstrap-v0.1 `
  --pretrained `
  --epochs 20 `
  --batch-size 4 `
  --allow-train-only
```

训练集指标不是独立准确率或泛化能力测量，不能作为模型成绩发布；这个 checkpoint 仅用于在 Rangitoto 视频中生成主动学习候选池。获得另一场完整比赛并建立独立 `val` 分区后，应恢复常规训练和验证流程。

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

当前 `evaluate-pretrained` 只评估清单里已经给出的时间窗，不会从整场比赛自动发现新动作。`infer` 虽能执行整场滑窗推理，但只接受 Spike-Trace 自训练 checkpoint，不能直接使用这份外部 YOLO 权重。Rangitoto 主动学习选择器已经从整场候选池确定首轮 40 个短回合；现在只需在现成工作簿中穷举这些短回合内的我方动作。

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

输出目录包含 `events.json` 和带 UTF-8 BOM 的 `events.csv`。`events.json` 使用格式版本
`2`：`windows` 为每个滑窗保存唯一、稠密的 `window_index`，每个事件通过非空且严格递增的
`source_window_indices` 明确引用自己的成员窗口。窗口区间为半开区间，采样合同固定为
`center-nearest-frame-v1`；双裁剪合并只接受这种 v2 显式成员关系，不会从动作和时间重建旧版成员。

两路 v2 推理完成后，可生成并独立验证确定性的双裁剪复核材料：

```powershell
.venv\Scripts\python.exe -m spiketrace build-dual-crop-review outputs\rangitoto-far\events.json `
  outputs\rangitoto-near\events.json outputs\rangitoto-r3d18-bootstrap-review `
  --repo-root .
.venv\Scripts\python.exe -m spiketrace verify-dual-crop-review `
  outputs\rangitoto-r3d18-bootstrap-review\merged_candidates.json `
  --csv outputs\rangitoto-r3d18-bootstrap-review\merged_candidates.csv
node tools\build_rangitoto_review.mjs `
  outputs\rangitoto-r3d18-bootstrap-review\merged_candidates.json `
  outputs\rangitoto-r3d18-bootstrap-review\rangitoto_action_review.xlsx `
  outputs\.rangitoto-review-build\previews
node tools\verify_rangitoto_review.mjs `
  outputs\rangitoto-r3d18-bootstrap-review\merged_candidates.json `
  outputs\rangitoto-r3d18-bootstrap-review\rangitoto_action_review.xlsx
```

构建结果嵌入规范化后的 far/near 输入、来源文件 SHA-256 和规范化 payload SHA-256；验证命令
只依赖合并 JSON 即可重算候选、分组、主来源和可选 CSV。来源文件 SHA-256 仅作为 provenance
保留，自包含验证器不会在没有原输入文件时声称重新验证该字节哈希。完整推理仍需要本地视频
和 checkpoint；合并 JSON 的自包含审计不需要它们。format-2 JSON 保存完整窗口证据，XLSX
只保存由该 JSON 派生的人工复核行，不复制全量窗口表。

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

### Rangitoto 双裁剪候选池与主动学习

已按 `center-nearest-frame-v1` 完成一次全场双裁剪重算。远端裁剪为 `0,0,1920,645`，近端
裁剪为 `0,255,1920,1080`；两路均为 format-2 显式 `source_window_indices` 输入，各有
`16,448` 个稠密窗口。视频为 1920x1080、30 FPS、197,384 帧，SHA-256
`0102bf5de66a86677581155d1d2e723621a2a45d5c74b0757a8a256387204fbf`；checkpoint SHA-256
为 `bf88fb015ab61d68e49e499abff91693e94005bba777269bf573cf96d25f8200`。

- far：1,483 个来源事件，`attack` 912、`set` 563、`block` 4、`serve` 2、`dig` 1、`receive` 1；
  `events.json` SHA-256 为 `c43ba9b2eb4d065239e0158265d8362f18c7a93435a3c6846b7d70b5dfee3bcd`。
- near：2,799 个来源事件，`attack` 1,324、`set` 1,102、`serve` 364、`receive` 7、`dig` 2；
  `events.json` SHA-256 为 `0cecbe15f9ec2aa04d3eacb133bd1f5231855556977cfc9118fd90e96b07f92d`。
- 合并结果：4,282 个来源候选、2,942 个复核候选、1,340 条 duplicate links（823 组）、
  1,488 条 conflict links（495 组）；预测动作分布为 `attack` 1,202、`set` 1,361、`serve` 364、
  `receive` 8、`block` 4、`dig` 3。合并 JSON/CSV SHA-256 分别为
  `e79aba6e5eb3a1075819a290144198b7e393fceed94313cdf9fc171378a76e7e` /
  `96130e7f0c4ec2ba2e7ddd697ef7df9a98fd79c35557ae4e3782f35d6f2291d4`；最终 XLSX SHA-256 为
  `19b899cab6d963daf266b1fe1a966a518c48d7c74bb86ee247cfca39e2d7c725`。

这 2,942 条是低阈值全场扫描形成的审计候选池，不是 2,942 条人工待办。823 个重复组、
495 个冲突组以及大量相邻滑窗会反复指向同一段比赛；候选置信度中位数为 `0.2929`，只有
2 条达到 `0.5`。最终 JSON、CSV 和 XLSX 继续作为完整证据保留，但不要求填写全量工作簿。

已确认采用[按短回合的主动学习设计](docs/superpowers/specs/2026-08-16-rangitoto-active-learning-design.md)：
每轮自动选择 40 个 5 至 30 秒短片（默认、优先长度为 15 秒），优先覆盖跨视角冲突、`receive` / `block` / `dig`
少数候选、高置信度硬负、随机候选和双方均无候选的时间块。人工一次播放并穷举片段内全部
我方动作，输入片段内相对秒数；系统再换算为原视频时间并生成累计训练清单。首轮不要求用户
处理未被短片覆盖的其他候选，也不以清空历史候选为训练目标。

### Rangitoto 首轮主动学习复核交接

`data/active-learning/rangitoto/round-01-selection.json` 是已经提交的 40 段选择证据；完整
2,942 候选 JSON/CSV/XLSX 只作为审计证据保留。40 个代理短片、四张预览图和
`outputs/active-learning/rangitoto/round-01/review.xlsx` 已在本地生成，不提交版本库。

该工作簿已经填写完成，共读取到 83 条人工记录；用户不需要再操作工作簿。原工作簿保持
SHA-256 `3b3baa474bf5d20e24a2e979b389e5d1b6df755b3c8516c993d8cc719b53535b`，
不能原位修复或覆盖。由于 v1 草稿无法无损表达遮挡、镜头外、裁判推断、`free_ball` 和多人参与，
当前不得把它直接交给旧 `apply-active-review`。下一步按
[证据分层迁移实现计划](docs/superpowers/plans/2026-08-30-rangitoto-evidence-aware-review.md)
生成 v2 权威结果、七类训练投影和同步 CSV；在实现完成前不需要用户补充确认。

以下仅用于从头重建或审计，不是当前操作；命令采用不同目标路径，并且这些不可覆盖的目标必须
尚不存在：

```powershell
spiketrace select-review-batch outputs\rangitoto-r3d18-bootstrap-review\merged_candidates.json outputs\active-learning\rangitoto\round-01-selection-rebuild.json --repo-root .
node tools\build_active_review_batch.mjs data\active-learning\rangitoto\round-01-selection.json outputs\active-learning\rangitoto\round-01-rebuild outputs\active-learning\rangitoto\round-01-rebuild-previews
```

代理短片无音频；本轮人工记录使用片段内整数秒、动作和画面侧别，没有状态列、checkbox 或完成列。
无时间的单条 `background` 表示整段已看完且没有目标动作；带时间的 `background` 是显式负样本，
可以与动作共存。`receive` 仅指接对方发球，`dig` 仅指对方进攻后的防守起球。

这 40 段是为训练补样而偏置选择的主动学习样本，不是准确率测试。只要 Rangitoto 标注加入
训练，Rangitoto 就不能继续作为独立 `val` 或 `test`；Precision、Recall、Macro F1 和混淆
矩阵必须在一场从未用于训练或选样的完整比赛上测量。

重算、合并与工作簿验证命令如下：

```powershell
.venv\Scripts\spiketrace.exe infer data\YTDown.com_YouTube_Rangitoto-vs-Taka-National-Final-Sets-1-_Media_k3PdQgm2jVs_001_1080p.mp4 runs\rangitoto-r3d18-bootstrap\best.pt outputs\rangitoto-r3d18-bootstrap-center-nearest-v1\far --stride-seconds 0.4 --confidence-threshold 0.2 --batch-size 8 --device cuda --crop 0,0,1920,645
.venv\Scripts\spiketrace.exe infer data\YTDown.com_YouTube_Rangitoto-vs-Taka-National-Final-Sets-1-_Media_k3PdQgm2jVs_001_1080p.mp4 runs\rangitoto-r3d18-bootstrap\best.pt outputs\rangitoto-r3d18-bootstrap-center-nearest-v1\near --stride-seconds 0.4 --confidence-threshold 0.2 --batch-size 8 --device cuda --crop 0,255,1920,1080
.venv\Scripts\spiketrace.exe build-dual-crop-review outputs\rangitoto-r3d18-bootstrap-center-nearest-v1\far\events.json outputs\rangitoto-r3d18-bootstrap-center-nearest-v1\near\events.json outputs\rangitoto-r3d18-bootstrap-review --repo-root .
.venv\Scripts\spiketrace.exe verify-dual-crop-review outputs\rangitoto-r3d18-bootstrap-review\merged_candidates.json --csv outputs\rangitoto-r3d18-bootstrap-review\merged_candidates.csv
node tools\build_rangitoto_review.mjs outputs\rangitoto-r3d18-bootstrap-review\merged_candidates.json outputs\rangitoto-r3d18-bootstrap-review\rangitoto_action_review.xlsx outputs\.rangitoto-review-build\previews
node tools\verify_rangitoto_review.mjs outputs\rangitoto-r3d18-bootstrap-review\merged_candidates.json outputs\rangitoto-r3d18-bootstrap-review\rangitoto_action_review.xlsx
```

本轮人工只编辑了工作簿的五个黄色列：`人工确认动作`、`人工开始时间`、`人工结束时间`、`人工侧别`
和 `备注`。`人工确认动作` 非空即代表已复核；没有额外状态列、勾选框或完成列。人工开始/结束
时间是整数秒，可读时间码只是只读显示文本。`receive` 仅指
接对方发球，`dig` 仅指对方进攻后的防守起球；`far` / `near` 是视觉裁剪位置，不代表球队。

该 bootstrap checkpoint 只用美国队对德国队同一场比赛的 90 个训练窗口训练。Rangitoto
预测明显集中在 `set`/`attack`，现阶段只能用于发现候选，不能声明泛化准确率。候选复核只能
估计 Precision；必须额外穷举模型没有报动作的时间块才能发现漏报。如果主动学习标签加入训练，
Rangitoto 就不能继续作为独立 `val` 或 `test`；可信的 Precision、Recall、Macro F1 和逐类
混淆矩阵必须来自另一场从未参与训练和选样的完整比赛。

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

当前训练数据仍只有美国队对德国队一场比赛的 90 个窗口，没有可用于生产的模型权重。
Rangitoto 第二场完整比赛的 `center-nearest-frame-v1` 双裁剪 format-2 复核材料、首轮 40 段
确定性选择、本地代理和 83 条人工记录已经完成：候选池共有 2,942 个候选、4,282 个来源候选、
823 个 duplicate groups 和 495 个 conflict groups。当前工程任务是实现证据分层迁移，安全处理
遮挡、镜头外、推断结果、`free_ball` 与未来的多人参与关系；用户不需要继续填写工作簿。在独立
比赛真值建立前，不能把候选数或主动学习批次成绩当作准确率或泛化结果。代码已经跑通
自训练工程链路、外部 YOLO 权重评估、整场顺序推理和可审计双裁剪合并，但训练数据仍以
39 个 `background` 为主，
`set`、`attack`、`receive` 和 `dig` 正样本远远不足。当前外部 YOLO 只能给已有窗口提供
预测证据，不能自动扫描整场，也不能完成美国队过滤、球衣号码归属、发球成功率、得分
结果、一传到位率或上场时间。继续添加同场训练窗口只能增加训练素材，无法证明模型泛化精度；
完成一至三轮主动学习并建立独立 `val` 或 `test` 真值后，再根据逐类指标决定是否继续微调动作模型。
号码归属、产品前端、账户和数据库实现继续暂停，直到动作模型闭环形成可验证基线。
