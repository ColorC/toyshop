# ToyShop 重构状态修订（2026-03-07）

> 目标：将 ToyShop 从多方面深耦合实现，收敛为“干净的编排 + 数据管理”平台。

## 1. 本次评估结论（摘要）

- 架构成熟度：**中等（可运行，但仍处迁移中）**。
- 当前主线：`pm_cli.py -> pm.py -> tdd_pipeline.py`，可跑通。
- 已有进展：分阶段编排（research/MVP/SOTA）、质量门禁、退出条件、阶段事件与检查点已落盘。
- 核心风险：Graph 迁移互调风险、Port/Adapter 漂移、占位实现未清理、环境耦合偏重。

---

## 2. 已完成进度（与原蓝图对照）

### 2.1 编排侧

- 主编排入口与批处理：`toyshop/pm.py:1130-1182`
- 分阶段执行主链（含 deadlock 恢复）：`toyshop/pm.py:1205-1517`
- 引用增强主链：`toyshop/pm.py:1672+`
- 变更分析与 spec 演进：`toyshop/pm.py:1866-2023`

### 2.2 数据与审计侧

- 阶段事件、检查点、质量门禁、退出条件、中间汇报占位均已落盘：
  - `stage_events.jsonl`
  - `stage_checkpoint.json`
  - `quality_gates.json`
  - `exit_conditions.json`
  - `mid_report_hook.json`
- 数据库已有 workflow/run/change 相关表：`toyshop/storage/database.py:202-243`

### 2.3 抽象层（Port/Adapter）

- `CodingAgentPort` + `OpenHandsCodingAdapter` 已有雏形：
  - `toyshop/ports/coding.py`
  - `toyshop/adapters/coding.py`
- `CodeVersionPort` + `ASTCodeVersionAdapter` 已有雏形：
  - `toyshop/ports/version.py`
  - `toyshop/adapters/version.py`

---

## 3. 架构不干净点（需持续清理）

### 3.1 高优先问题（P0）

1. **Graph 迁移互调风险**
   - `pm.run_batch` 在 `TOYSHOP_USE_GRAPH=1` 时调用 `run_dev_graph`：`toyshop/pm.py:1146-1157`
   - `run_dev_graph` 现阶段又回调 `pm.run_batch`：`toyshop/graph/dev_pipeline.py:24-35`
   - 需要拆除互调，明确唯一调度方向。

2. **Port/Adapter 接口漂移**
   - `CodeVersionPort` 约定 `create_code_version(...)`：`toyshop/ports/version.py:13`
   - 适配器实现为 `create(...)`：`toyshop/adapters/version.py:18`
   - 调用方实际用 `version_port.create(...)`：`toyshop/pm.py:1888`
   - 需要统一命名与契约，恢复可替换性。

3. **旧新双轨边界不一致**
   - Legacy `pipeline.py` 仍在导出，且写 `openspec/specs/main.md`：`toyshop/pipeline.py:134-136`
   - 新主线使用 `openspec/spec.md`
   - 需要统一 spec 产物路径并明确单一主线。

### 3.2 中优先问题（P1）

4. **占位实现仍在主路径附近**
   - `AnalyzeInputExecutor` 为 placeholder：`toyshop/tools/analyze_input.py:87-114`
   - `mid_report_hook` 仍为占位：`toyshop/pm.py:1191-1203`

5. **环境与实现耦合较重**
   - `llm.py` 有硬编码路径与 SDK monkey patch：`toyshop/llm.py:29-37, 138-193`
   - 需下沉到网关/提供方适配层，避免污染编排核心。

---

## 4. 测试通畅度（本次实测）

### 4.1 关键链路测试

- `tests/test_pm_phased.py`、`tests/test_pm_refs.py`、`tests/test_tdd_test_quality_gate.py`
- `tests/test_toyshop_bridge.py`、`tests/test_research_agent.py`
- 结果：**关键编排/桥接/研究链路通过（28 + 33）**。

### 4.2 Agent 兼容测试

- `tests/test_agent.py` 单跑通过：**13 passed**。

### 4.3 现状结论

- 核心链路通畅度：**高**
- 全量“一把跑”稳定性：**中**（受环境、路径与测试发现机制影响）

---

## 5. 今日修订后的清理路线（面向“干净编排 + 数据”）

## Phase A（先止血，1 周）

- A1. 修复 graph 互调，确保单向调度。
- A2. 对齐 `CodeVersionPort` 与适配器契约。
- A3. 统一 spec 路径并冻结 legacy pipeline 行为（只读/弃用）。

**验收：**
- `TOYSHOP_USE_GRAPH=1` 不出现互调递归。
- change pipeline 全链路使用统一 version port 契约。
- OpenSpec 产物路径在主线一致。

## Phase B（降耦，1~2 周）

- B1. 将中间汇报从 `mid_report_hook.json` 升级为 `ReportingPort.publish()`。
- B2. 将网关兼容逻辑封装为 LLM Gateway Adapter，编排层不感知 patch 细节。
- B3. 统一 subprocess/test runner 边界，减少 regex 文本解析脆弱性。

**验收：**
- 编排核心不直接依赖具体外部 SDK 内部实现。
- 汇报/干预可替换（文件、webhook、消息总线）。

## Phase C（补数据面，2 周）

- C1. 完整 run 过程链：`process_steps + code_diffs + gate_results`。
- C2. 完成态强校验：无完整证据链不得 completed。
- C3. 增加回放能力（按 run_id/step_id）。

**验收：**
- 每次完成可追溯到：spec -> step -> diff -> gate -> decision。
- 支持最小回放与审计导出。

---

## 6. 下一步执行建议（从今天开始）

优先按 A1/A2/A3 开始，先把“结构性脏点”清掉，再做 B/C。这样能最快把系统从“可用但深耦合”推进到“可扩展平台核心”。
