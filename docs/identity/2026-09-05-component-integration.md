# 下一步组件接入建议

身份 MVP 先保持接口稳定，再逐步接入可替换的开源组件。每个组件只负责自己的证据，不能直接把候选结果写成确认身份。

## 检测

以 YOLO 系列的人体检测器或 RT-DETR 作为可选实现，输出 `PlayerDetection` 的源帧框、置信度、时间戳和可见性。检测器不可用、框太小或画面被遮挡时输出空证据，保持 `unknown`，不通过半场裁剪推断球队。

## 跟踪

以 ByteTrack 或 BoT-SORT 作为首选适配器，输入检测框并输出短期 `Track`。只在位置连续且外观相似时跨短遮挡重连；超过 1 秒或证据冲突就结束旧轨迹并创建新轨迹。重连事件保留为人工复核项。

## OCR

以 PaddleOCR、Tesseract 等可替换 OCR 适配器输出 `NumberObservation`。只提交清晰、足够大的球衣区域；正面、背面和遮挡状态分别记录。`aggregate_number_candidates` 负责跨帧加权和 roster 过滤：候选冲突、只读出一个字符、低置信度或不在 roster 时维持 `candidate`、`unreadable` 或 `not_visible`。

## 复核边界

`team=unknown`、号码冲突、遮挡重连、号码不在 roster、换边附近和一个动作对应多条 USA 轨迹都进入人工复核。只有 `identity_status=confirmed` 且 `number_status=confirmed` 的 assignment 才能通过事件适配器进入球员统计；组件替换不改变 ActionEvent 的动作标签、时间窗或置信度。
