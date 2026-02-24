# ToyShop 分离式架构完整方案（已暂缓，作为后续参考）

> 状态：`Deferred`（2026-02-23）
>
> 重要决策：分离式架构、LangGraph 全迁移、硬 SKILL 全量化 **暂时不做**。  
> 当前阶段采用“保留现有深耦合执行链 + 增量集成关键能力（优先 GPT Researcher）”策略。

## 当前执行主线（生效）
- 不进行全量去耦合重构，不拆现有 OpenHands 深耦合主链。
- 优先做 GPT Researcher 集成，并按触发策略使用：
  - 新需求进入时：先形成 `SOTA` 全量方案，再从中抽取 `MVP` 可执行子集。
  - 执行顺序固定为：`MVP -> 中间态 -> SOTA`。
  - 本地开发陷入绕圈时：触发外部调研辅助决策。
- 中间汇报接口先预留：
  - 完成 MVP 后，先进行中间态上传并发出汇报事件。
  - 未来接入人工通道后允许“在 MVP 阶段停下”。
  - 当前无人工通道时默认自动进入 SOTA 阶段。
- 继续强化控制点：修改范围、过程历史、质量门禁、退出条件。
- 本文剩余内容保留为“未来重构蓝图”，不作为当前迭代实施清单。

## 执行进度记录

### 2026-02-23T17:13:09+08:00
- 状态快照：`Current Mainline Completed`
- 当前主线完成度：`100%`（按“当前执行主线（生效）”口径）
- 交付补充：
  - 新增质量门禁落盘：`quality_gates.json`（按阶段记录 spec/tdd gate 通过情况）。
  - 新增退出条件落盘：`exit_conditions.json`（完成/失败分支都记录退出检查）。
  - 分阶段失败路径补齐退出记录，避免“失败即返回但无退出证据链”。
  - pytest 标记注册补齐（`e2e` / `slow`），消除未注册标记噪音。
  - 脚本型 E2E 增加“LLM 不可用快速探测 + 跳过退出 + 超时保护”，避免外部服务抖动导致流水线假失败。
- 测试状态：
  - 单元/非 E2E：`110 passed`。
  - pytest e2e：`1 passed, 12 skipped`（未开启 live 模式时按策略跳过）。
  - 脚本 E2E：`run_tdd_e2e.py / run_full_e2e.py / run_complete_e2e.py / run_modify_e2e.py / run_change_e2e.py` 均 `exit 0`。

### 2026-02-23T15:48:16+08:00
- 状态快照：`In Progress`
- 已完成（当前执行主线）：
  - 已实现 GPT Researcher 集成入口与结构化工件落盘（`research/request.json`、`research/result.json`、`research/summary.md`）。
  - 已实现分阶段执行主链：`MVP -> mvp_uploaded -> SOTA`（支持 `stop_after_mvp`）。
  - 已实现中间汇报占位与阶段事件：`mid_report_hook.json` + `stage_events.jsonl` + `stage_checkpoint.json`。
  - 已补齐“纠结触发搜索”主线能力：阶段失败后触发 `deadlock_resolution` 调研并进行一次恢复重试。
  - 已补充手动触发入口：`pm_cli research-deadlock`。
- 本轮新增（对应缺口收敛）：
  - 研究触发类型约束收口为：`kickoff_mvp_sota | deadlock_resolution`。
  - 研究工件改为“最新快照 + 历史留痕”并行写入，避免多次调研相互覆盖。
  - 阶段结果新增 deadlock 恢复标记，便于后续门禁统计。
- 未纳入当前迭代（保持 Deferred）：
  - LangGraph 全迁移、Port/Adapter 全量解耦、Hard Skill 编译验证全链路。
  - ChangePlan/Scope Guard/ProcessStep 全量数据库模型与强校验门禁。

## 1. 目标与约束

### 1.1 目标
- 将 ToyShop 从 OpenHands 深耦合单体，升级为可插拔的分离式编排平台。
- 统一两类协议：
  - InterAgent 协议：OpenSpec（需求/设计/任务/规格传递）
  - ToAgent 协议：SKILL（SOP 传递与执行约束）
- 支持多执行端：
  - 编程 Agent：OpenHands
  - 调研 Agent：GPTResearcher
  - 汇报/干预通道：OpenClaw
- 增加“硬 SKILL”机制：执行前必须可验证存在退出路径与兜底路径。
- 优先保障关键 E2E 测试的可复用性，单元测试按新边界重建。

### 1.2 非目标（当前阶段）
- 不追求一次性替换所有历史模块。
- 不追求所有 Agent 的能力一致，只保证协议一致与失败可控。
- 不追求零重构成本，允许阶段性双轨运行（旧流 + 新流）。

---

## 2. 目标分层架构

## 2.1 层次
- Layer A: Protocol Layer（协议层）
  - OpenSpec Contract
  - SkillSpec Contract
  - EventEnvelope Contract
- Layer B: Orchestration Core（编排核心）
  - LangGraph Runtime
  - Workflow Registry
  - Skill Compiler / Validator
  - Run State Machine
- Layer C: Capability Adapters（能力适配层）
  - CodingAgentPort -> OpenHandsAdapter
  - ResearchAgentPort -> GPTResearcherAdapter
  - ReportingPort -> OpenClawAdapter
  - StoragePort -> SQLite/Postgres Adapter
- Layer D: Pipelines（业务流）
  - Development Pipeline
  - Maintenance Pipeline
  - Iteration Pipeline

## 2.2 关键原则
- Protocol First：跨组件只传标准协议，不传实现私有结构。
- Port/Adapter：核心只依赖抽象端口。
- Deterministic by Default：关键流程可重放、可回放、可审计。
- Verify Before Execute：硬 SKILL 先验验证后执行。
- Scope Locked Execution：执行前锁定修改范围，执行中禁止越界写入。
- Event Sourcing History：全过程事件化留痕，保证审计、回放、回滚。

---

## 3. 组件与接口设计

## 3.1 核心端口（建议）

```python
class CodingAgentPort(Protocol):
    def run_task(self, request: CodingTaskRequest) -> CodingTaskResult: ...

class ResearchAgentPort(Protocol):
    def run_research(self, request: ResearchRequest) -> ResearchResult: ...

class ReportingPort(Protocol):
    def publish(self, event: ProgressEvent) -> None: ...
    def request_human_intervention(self, ticket: InterventionTicket) -> InterventionDecision: ...

class SkillRuntimePort(Protocol):
    def compile(self, skill_doc: SkillDocument) -> CompiledWorkflow: ...
    def validate(self, workflow: CompiledWorkflow) -> SkillValidationReport: ...
    def execute(self, workflow: CompiledWorkflow, ctx: RunContext) -> RunResult: ...

class SpecPort(Protocol):
    def parse(self, docs: OpenSpecBundle) -> ParsedSpec: ...
    def validate(self, docs: OpenSpecBundle) -> ValidationReport: ...

class RunStorePort(Protocol):
    def create_run(self, run: RunRecord) -> str: ...
    def append_event(self, run_id: str, event: ProgressEvent) -> None: ...
    def update_run_state(self, run_id: str, state: str) -> None: ...

class ScopeControlPort(Protocol):
    def create_change_plan(self, req: ChangePlanRequest) -> ChangePlan: ...
    def validate_write(self, run_id: str, path: str, symbol: str | None = None) -> ScopeDecision: ...
    def report_violation(self, run_id: str, violation: ScopeViolation) -> None: ...

class ProcessHistoryPort(Protocol):
    def append_step(self, run_id: str, step: ProcessStep) -> None: ...
    def bind_diff(self, run_id: str, step_id: str, diff: CodeDiff) -> None: ...
    def bind_gate_result(self, run_id: str, step_id: str, gate: GateResult) -> None: ...
    def replay(self, run_id: str, to_step: str | None = None) -> ReplayResult: ...
```

## 3.2 适配器
- OpenHandsAdapter：实现 `CodingAgentPort`。
- GPTResearcherAdapter：实现 `ResearchAgentPort`。
- OpenClawAdapter：实现 `ReportingPort`（webhook/ws/message bus）。
- OpenSpecAdapter：实现 `SpecPort`。
- SQLiteRunStore：实现 `RunStorePort`。
- PolicyEngineAdapter：实现 `ScopeControlPort`（路径/符号/预算策略）。
- EventLogAdapter：实现 `ProcessHistoryPort`（步骤、diff、门禁、决策）。

## 3.3 编排核心
- 使用 LangGraph 作为统一编排引擎。
- 每个 pipeline = 一张 graph。
- 节点只调用 port，不直接 import 具体实现。
- graph 状态必须可序列化（用于恢复、重放、E2E断言）。

---

## 4. 协议设计

## 4.1 InterAgent（OpenSpec）
- 继续使用 `proposal.md/design.md/tasks.md/spec.md` 作为文档承载。
- 增加机器友好镜像：`openspec/bundle.json`。
- 所有阶段间传递时统一引用 `spec_bundle_id`（避免字符串复制漂移）。

## 4.1.1 搜索触发策略（Research Trigger Policy）
- 搜索不是常驻行为，而是“条件触发”的标准动作。
- 触发条件仅两类：
  1. 出发时（新需求进入）：
     - 先产出 `SOTA`（业界最佳实践）全量方案。
     - 再从 SOTA 中抽取 `MVP`（最小可验证）子集。
     - 必须结合当前平台约束做可行性裁剪（性能、成本、依赖复杂度、交付周期）。
     - 进入阶段执行：先做 MVP，完成后再做 SOTA。
  2. 纠结时（本地陷入循环）：
     - 本地修复路径明显绕圈，关键矛盾长期未解时触发外部搜索。
     - 优先搜索“近期变化快/模型记忆可能过时”的主题（新框架版本、协议变更、已知回归）。
- 禁止触发：
  - 有明确本地修复路径且在收敛时，不触发网络搜索。
  - 仅因“想多看几种方案”而无限扩展搜索范围。

## 4.2 ToAgent（SkillSpec）
- 新增 `skill.yaml`（或 `skill.json`）作为 SKILL 结构化描述。
- 最小字段：
  - `id`, `version`, `entry`, `steps[]`, `exit_conditions[]`, `fallbacks[]`, `max_retries`, `timeouts`。
- SKILL.md 作为人类文档；SkillSpec 作为执行依据。

## 4.3 事件协议（EventEnvelope）

```json
{
  "run_id": "...",
  "timestamp": "...",
  "stage": "requirement|architecture|coding|testing|reporting",
  "type": "progress|warning|error|intervention_required|completed",
  "payload": {},
  "correlation_id": "..."
}
```

## 4.3.1 搜索请求/结果协议（建议）
- `ResearchRequest`（触发即记录）：
  - `trigger_type`: `kickoff_mvp_sota | deadlock_resolution`
  - `problem_statement`
  - `local_attempt_summary`
  - `constraints`（性能/成本/兼容性）
  - `timebox_minutes`
- `ResearchResult`（必须结构化回传）：
  - `mvp_option`
  - `sota_option`
  - `mvp_extracted_from_sota`（bool，必须为 true）
  - `mvp_scope`（从 SOTA 中裁剪出的模块/接口/任务集合）
  - `tradeoffs`
  - `recommended_option`
  - `adoption_plan`
  - `sources[]`

## 4.3.2 阶段检查点与中间汇报协议（建议）
- `StageCheckpoint`（阶段状态机）：
  - `current_stage`: `mvp | mvp_uploaded | sota | done`
  - `stage_artifact_refs[]`
  - `stage_gate_passed`
- `MidReportHook`（接口占位）：
  - `run_id`
  - `checkpoint`: `mvp_uploaded`
  - `summary`
  - `decision_required`: `continue_to_sota | stop_after_mvp`
  - `default_decision_when_unavailable`: `continue_to_sota`

## 4.4 修改范围协议（ChangePlan）
- 执行前必须生成并绑定 `change_plan_id`。
- 运行时每次写入都走 scope 校验，不通过则拦截并触发 fallback/人工干预。

```json
{
  "change_plan_id": "...",
  "run_id": "...",
  "allowed_paths": ["src/**", "tests/**"],
  "forbidden_paths": [".github/**", "infra/prod/**"],
  "allowed_symbols": ["AuthService.*", "UserRepo.save"],
  "max_files_changed": 12,
  "max_lines_added": 600,
  "max_lines_deleted": 300,
  "required_tests": ["tests/test_auth.py", "tests/test_api_login.py"],
  "approval_policy": "auto|human_required",
  "created_at": "..."
}
```

## 4.5 修改过程协议（ProcessStep）
- 每个关键步骤写入一条过程记录，必须可关联 diff、测试结果和决策原因。

```json
{
  "step_id": "...",
  "run_id": "...",
  "agent_id": "coding_agent_1",
  "stage": "coding",
  "action": "edit_file|run_test|request_intervention|apply_fallback",
  "reason_ref": {
    "spec_bundle_id": "...",
    "skill_def_id": "...",
    "change_plan_id": "..."
  },
  "diff_ref": "artifact:diff:...",
  "gate_ref": "gate_result_id",
  "status": "success|failed|blocked",
  "created_at": "..."
}
```

---

## 5. 硬 SKILL（Hard Skill）执行机制

## 5.1 执行前流程
1. 读取 SKILL.md + SkillSpec。
2. 编译为 Workflow IR（节点、边、守卫、重试、超时、退出条件）。
3. 静态验证：
   - 必须存在至少一个可达退出路径。
   - 每个循环必须有边界（max_iterations 或 timeout）。
   - 每个关键失败节点必须定义 fallback。
   - 不允许“FinishTool 直接短路”绕过关键验收节点。
   - 必须绑定 `change_plan_id` 且存在可执行范围（非空 allowed_paths）。
4. 干跑（dry-run）验证：
   - 用 mock adapter 跑最短路径。
   - 用 fault injection 跑至少一条 fallback 路径。
   - 注入一次越界写入，验证 Scope Guard 会拦截并能进入兜底路径。
5. 通过后再进入真实执行。

## 5.2 运行时保障
- 强制状态机守卫：禁止无验收直接完成。
- 关键节点双重确认：
  - 机器验收（测试/规则）
  - 策略验收（exit conditions）
- 超时与熔断：每个节点都有 deadline，超过触发 fallback 或人工干预。
- Diff 守卫：每次写入必须通过 `ScopeControlPort.validate_write()`。
- 完成态守卫：未绑定关键步骤历史（diff + gate）不得进入 completed。
- 纠结态守卫：满足“绕圈阈值”时必须触发 `ResearchAgentPort` 而非继续盲试。
- 阶段守卫：必须先通过 MVP gate 并发出 `mvp_uploaded` 检查点，才能进入 SOTA。

## 5.3 最小实现建议
- 第一版可不做复杂编译器，先做：
  - SkillParser（YAML -> dataclass）
  - SkillGraphBuilder（dataclass -> LangGraph）
  - SkillValidator（静态规则）
  - SkillDryRunner（mock adapters）

---

## 6. 持久化模型（建议扩展）

在现有 `.toyshop/architecture.db` 基础上新增：
- `workflow_definitions`
  - `id`, `name`, `version`, `ir_json`, `created_at`
- `skill_definitions`
  - `id`, `skill_id`, `version`, `skill_spec_json`, `source_path`, `created_at`
- `skill_validation_reports`
  - `id`, `skill_def_id`, `passed`, `issues_json`, `dry_run_report_json`, `created_at`
- `runs`
  - `id`, `pipeline_type`, `workflow_def_id`, `skill_def_id`, `state`, `started_at`, `ended_at`
- `run_events`
  - `id`, `run_id`, `seq`, `event_type`, `event_json`, `created_at`
- `run_artifacts`
  - `id`, `run_id`, `artifact_type`, `path`, `checksum`, `created_at`
- `change_plans`
  - `id`, `run_id`, `plan_json`, `locked`, `approved_by`, `created_at`
- `scope_violations`
  - `id`, `run_id`, `step_id`, `path`, `symbol`, `reason`, `resolved_by`, `created_at`
- `process_steps`
  - `id`, `run_id`, `seq`, `agent_id`, `stage`, `action`, `status`, `reason_ref_json`, `created_at`
- `code_diffs`
  - `id`, `run_id`, `step_id`, `file_path`, `added`, `deleted`, `patch_text`, `created_at`
- `gate_results`
  - `id`, `run_id`, `step_id`, `gate_type`, `passed`, `report_json`, `created_at`

这样可以支持：
- 完整审计
- 失败回放
- E2E 黄金样本对比
- 硬 SKILL 版本追踪
- 修改范围追踪与越界审计
- 修改过程级别回放（步骤 -> diff -> gate -> 决策）

---

## 7. 关键 E2E 测试复用方案（重点）

## 7.1 目标
- 一套场景定义，复用于多 pipeline、多 adapter、多项目类型。
- E2E 测试关注“流程可靠性和契约一致性”，不绑具体模型输出文本。

## 7.2 设计

### A. Scenario Pack
目录建议：
- `tests/e2e/scenarios/*.yaml`

每个场景包含：
- 输入（需求/变更/初始仓库）
- 预期契约（必须生成哪些 OpenSpec 字段、哪些事件）
- 允许波动范围（例如模块名可变化，但必须有 N 个核心能力）
- 终态断言（run state、测试通过阈值、artifact 完整性）

### B. Harness
目录建议：
- `tests/e2e/harness/`

能力：
- 加载 scenario
- 注入 adapter profile（real/mock/mixed）
- 启动 pipeline
- 捕获 run_events 与 artifacts
- 执行 invariant 断言

### C. Adapter Profiles
- `mock_all`: 全模拟，快速验证流程逻辑。
- `mock_external_real_core`: 核心真实、外部 agent 模拟。
- `real_coding_mock_research`: 编码真实、调研模拟。
- `real_all`（夜间或门禁前运行）。

### D. Invariant Assertions（复用核心）
- `OpenSpecInvariant`：proposal/design/tasks/spec 结构合法。
- `WorkflowInvariant`：不存在跳过关键节点直接完成。
- `HardSkillInvariant`：执行前存在验证记录，且通过。
- `EventInvariant`：关键事件序列完整（start -> ... -> completed/failed）。
- `ArtifactInvariant`：关键产物存在且 checksum 有记录。
- `ScopeInvariant`：所有写入都在 ChangePlan 范围内，越界必须有处理记录。
- `HistoryInvariant`：completed run 必须具备完整 `process_steps + code_diffs + gate_results` 链路。
- `ResearchTriggerInvariant`：仅在 `kickoff_mvp_sota` 或 `deadlock_resolution` 两类触发下允许外部搜索。
- `StageProgressInvariant`：阶段顺序必须为 `mvp -> mvp_uploaded -> sota -> done`（或 `mvp -> mvp_uploaded -> done` in future stop mode）。

## 7.3 必测关键 E2E 场景
1. Greenfield 标准流（需求 -> 架构 -> 编码 -> 测试 -> 持久化）。
2. Brownfield 变更流（snapshot -> impact -> spec evolution -> incremental tdd）。
3. 硬 SKILL 正常路径（主路径完成）。
4. 硬 SKILL fallback 路径（注入失败后正确兜底）。
5. Agent 边界违规场景（写权限越界被拦截并恢复）。
6. 长任务汇报场景（OpenClaw channel 持续收到进度事件）。
7. 调研增强场景（ResearchAdapter 返回上下文并进入设计节点）。
8. 中断恢复场景（run crash 后从持久化状态恢复）。
9. 多项目类型矩阵（python/java/json-minecraft 至少 smoke）。
10. 协议兼容场景（OpenSpec 版本升级下旧场景仍可解析）。
11. 修改范围超限场景（超过文件数/行数预算触发阻断）。
12. 过程历史完整性场景（任意步骤可追溯到 spec/skill/change_plan 与 gate 结果）。
13. 新需求双方案场景（必须同时给出 MVP 与 SOTA，并完成约束评估）。
14. 纠结触发搜索场景（本地连续失败达到阈值后触发外部搜索并收敛）。
15. MVP 中间态场景（完成 MVP 后必须上传并发出中间汇报事件）。
16. 无人工通道续跑场景（未收到人工决策时默认自动进入 SOTA）。

## 7.4 复用策略
- 复用“场景 + 断言 + profile”，不复用具体 prompt 文本。
- 对 LLM 输出采用语义断言，不做逐字 golden。
- 对关键结构（OpenSpec JSON、RunEvents）使用 snapshot/golden。

---

## 8. 单元测试重构策略（必须重做）

## 8.1 原则
- 单元测试按新分层重划边界，不沿用旧模块切分。
- 每个 port 都有 contract tests；每个 adapter 都有 conformance tests。

## 8.2 新测试层次
- `tests/unit/core/`：编排内核、状态机、SkillValidator。
- `tests/unit/protocol/`：OpenSpec/SkillSpec/EventEnvelope 校验与转换。
- `tests/unit/adapters/`：OpenHands/GPTResearcher/OpenClaw 适配器行为。
- `tests/unit/storage/`：run/event/artifact 持久化一致性。

## 8.3 建议淘汰/迁移
- 直接依赖旧 runner 内部私有函数的测试应迁移为：
  - 核心逻辑单测（纯函数）
  - adapter 合约测（mock transport）
  - E2E 场景测（流程完整性）

---

## 9. 实施路线图

## Phase 1: 抽象层落地（1-2 周）
- 定义 ports + event/schema。
- 将现有 `pm/workflows/tdd_pipeline` 包裹到新 `OrchestrationService`。
- 引入 run/event 持久化。

## Phase 2: LangGraph 化（1-2 周）
- requirement/architecture/coding 流转为 graph。
- 保持旧实现可回退（feature flag）。

## Phase 3: 外部 Agent 适配（1-2 周）
- OpenHandsAdapter 重构为标准 port。
- GPTResearcherAdapter 首版接入（至少 quick research + context 注入）。
- OpenClawAdapter 实现事件推送与人工干预接口。

## Phase 4: 硬 SKILL（1-2 周）
- SkillSpec + parser + validator + dry-run。
- 编译到 workflow 并接入执行前门禁。

## Phase 5: 测试体系切换（1-2 周）
- 新 E2E harness 与 scenario packs 上线。
- 单元测试按新分层重建。
- 将 CI 切为：unit(contract)/e2e-smoke/e2e-nightly。

---

## 10. 验收门槛（Go/No-Go）
- Go 条件：
  - 新老 pipeline 在核心场景结果等价。
  - 关键 E2E 16 个场景通过率 >= 95%。
  - 硬 SKILL 覆盖主路径 + fallback 路径。
  - run/event/artifact 可追溯完整。
  - 修改范围策略生效率 100%（越界写入全部被拦截或审批）。
  - 完成态 run 的过程历史完整率 100%。
  - 研究触发策略正确率 100%（仅在两类触发条件下启动外部搜索）。
  - MVP 阶段检查点触发率 100%，且无人工通道时自动续跑 SOTA 成功率 >= 95%。
- No-Go 条件：
  - 仍存在“未经过关键验收节点直接 finish”。
  - 中断后无法恢复 run。
  - 存在未记录原因链路的代码变更（无 step/diff/gate 绑定）。
  - 存在无触发依据的外部搜索，或该触发时未触发搜索。
  - 未完成 `mvp_uploaded` 即进入 SOTA。
  - E2E 必测场景存在不稳定随机失败且无法重放。

---

## 11. 与当前代码的对应改造点（首批）
- `toyshop/workflows/*.py` -> graph node + state schema。
- `toyshop/tdd_pipeline.py` -> 拆为 `core + adapters + policies`。
- `toyshop/pm.py` -> 改为 orchestration facade（不直接耦合具体 agent）。
- `toyshop/bridge.py` -> 增加 run lifecycle API 与 event query API。
- `toyshop/storage/*.py` -> 增加 workflow/skill/run/event/artifact 表。
