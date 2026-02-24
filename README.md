# ToyShop — AI 全自动软件工厂

> _"当 AI 告诉你它完成了任务，不要相信它的语感，只相信跑通的测试用例。"_

ToyShop 是一个多智能体软件工程编排框架，能从一句话需求全自动产出可编译、可测试的完整项目。核心理念：**TDD 驱动 + 参考源学习 + 真实环境验证**。

不是 Copilot，不是代码补全。是一个完整的 PM → Spec → TDD → Debug → 验证 流水线。

## 当前规模

| 指标 | 数值 |
|------|------|
| 核心模块 | 46 个 Python 模块 |
| 代码量 | ~16,000 行 |
| 单元测试 | 409 个（全部通过） |
| E2E 测试脚本 | 13 个 |
| 支持语言 | Python, Java, Java-Minecraft (Fabric) |

## 已验证的 E2E 流程

### 1. Python 项目全自动开发
```
需求文本 → spec 生成 → TDD pipeline → 可运行代码 + 测试
```
- 从一句话需求到完整 Python 项目，包含 openspec 文档、源码、测试
- 白盒测试 + 黑盒测试双重验证
- Debug Form 自动修复失败测试（最多 3 轮）

### 2. Minecraft Mod 全自动开发（Fabric 1.21.1）
```
需求文本 → 需求分解 → 参考源搜索 → spec 生成 → TDD → Gradle 编译 → MC 服务器 RCON 验证
```
- 自动生成 Fabric 项目脚手架（build.gradle, fabric.mod.json 等）
- 参考源系统：从 Widelands/Wesnoth 等开源游戏学习逻辑，从 Modrinth mods 学习 Fabric 机制
- 真实 MC 服务器启动 + RCON 协议验证 item/block 注册

### 3. 自举（Self-Hosting）
- ToyShop 已完成对自身代码库的 smart bootstrap（46 模块 / 326 接口）
- ModFactory 同样完成 bootstrap（44 模块 / 135 接口）

## 架构

```
用户需求 (一句话)
    │
    ▼
[decompose] 需求分解为多个方面 → decomposition.json
    │
    ├──▶ [ref-scan] 参考源搜索 (grep + Modrinth 反编译) → reference_reports/
    ├──▶ [decide] 新建/修改决策 → decision.json
    │
    ▼
[enrich] 合成富化需求 → enriched_requirement.md
    │
    ▼
[spec] OpenSpec 文档生成 (proposal → design → tasks → spec)
    │
    ▼
[TDD Pipeline]
    Phase 1: 签名提取 → 接口存根
    Phase 1.5: Fabric 脚手架 (MC mod only)
    Phase 2: Test Agent 生成测试
    Phase 3: Code Agent 实现代码 (smoke test)
    Phase 4: 自动白盒测试
    Phase 4.5-4.7: Debug Form 分析 + 修复 + 反作弊
    Phase 5: 黑盒测试 (spec scenarios)
    Phase 6: MC 服务器 RCON 验证 (MC mod only)
```

每个步骤独立可运行，中间 JSON 可人工编辑后重跑下游。

## 核心模块

| 模块 | 职责 |
|------|------|
| `pm.py` / `pm_cli.py` | 项目管理编排 + CLI |
| `tdd_pipeline.py` | TDD 6 阶段流水线 |
| `reference.py` | 参考源配置、grep 扫描、LLM 打分 |
| `decomposer.py` | 需求分解为 typed aspects |
| `decision_engine.py` | 新建 vs 修改已有项目决策 |
| `smart_bootstrap.py` | AST 驱动 + LLM 富化的项目分析 |
| `test_runner.py` | Pytest / Gradle / RCON / Visual 测试运行器 |
| `mc_scaffold.py` | Fabric mod 项目脚手架生成 |
| `mc_test_env.py` | MC 服务器生命周期管理 (build → deploy → start → RCON → stop) |
| `research_agent.py` | 自主研究 agent（含死锁检测） |
| `rollback.py` | Git checkpoint + 回滚 |
| `debug_*.py` / `fault_localize.py` | Debug v2: probe → hypothesis → fault localization |
| `llm.py` | LLM 抽象层（Messages API + Responses API + 网关兼容） |

## 参考源系统

ToyShop 的参考源系统让 AI 不再"凭空想象"，而是基于真实优秀代码学习：

**逻辑参考**（学设计思路）：
- Widelands — RTS 游戏逻辑（战斗、资源、建筑）
- Battle for Wesnoth — 回合制战斗、单位、地形
- OpenRA — 即时战略、武器系统
- Codex / EvoAgentX / SWE-Debate — Agent 架构参考

**机制参考**（学实现方式）：
- Modrinth Mods — 按需下载 + 反编译，搜索 Fabric registry/mixin 模式

配置格式：`references.toml`，每个项目可配置不同参考源。

## 快速使用

```bash
# 全自动（含参考源）
python3 -m toyshop.pm_cli run-with-refs \
  --name my-project \
  --input "创建一个寒冰弓mod" \
  --type java-minecraft \
  --ref-config references.toml

# 分步执行（可人工干预）
python3 -m toyshop.pm_cli create --name my-project --input "需求" --type python
python3 -m toyshop.pm_cli spec --batch <dir>
python3 -m toyshop.pm_cli tdd --batch <dir>

# 独立脚本
python3 -m toyshop.scripts.decompose --input "需求" --type java-minecraft -o decomp.json
python3 -m toyshop.scripts.ref_scan --decomposition decomp.json --config refs.toml -o reports/
python3 -m toyshop.scripts.decide --decomposition decomp.json --projects-dir mods/ -o decision.json
python3 -m toyshop.scripts.enrich --decomposition decomp.json --refs reports/ -o enriched.md
```

## 测试

```bash
# 单元测试（409 个，~9 秒）
python3 -m pytest tests/ -v --ignore=tests/run_*.py

# E2E（需要 LLM API）
TOYSHOP_RUN_LIVE_E2E=1 python3 tests/run_modfactory_create_mod_e2e.py
TOYSHOP_RUN_LIVE_E2E=1 python3 tests/run_ref_source_e2e.py
TOYSHOP_RUN_LIVE_E2E=1 python3 tests/run_complete_e2e.py
```

## 技术栈

| 组件 | 技术 |
|------|------|
| 编排核心 | Python 3.12 |
| Agent 执行 | OpenHands SDK (headless) |
| LLM | Claude (via Anthropic API / 网关) |
| MC 构建 | Gradle + Fabric Loom + JDK 21 |
| MC 验证 | RCON 协议 + 无头服务器 |
| 存储 | SQLite (项目 wiki + 架构版本) |

## 实验状态

这是一个实验项目。作者去年 8 月才开始编程，目标是探索"用严格管理方式驱动 AI Agent"的可行性。

### 最新 E2E 结果（2026-02-24，Frost Bow Mod）

从一句话需求 `"创建一个简单的寒冰弓mod，发射冰霜投射物"` 全自动产出：

| 阶段 | 结果 |
|------|------|
| 需求分解 | 9 个 typed aspects（item/entity/renderer/texture/recipe/lang/particles/loot） |
| 参考源搜索 | 7/9 aspects 命中，29 个代码片段（来自 Widelands + Modrinth mods） |
| 决策 | create（新建项目） |
| 富化需求 | 21,648 字符（含参考代码片段） |
| Spec 生成 | 4 文档（proposal/design/tasks/spec），19 个任务 |
| Fabric 脚手架 | build.gradle, fabric.mod.json, gradlew 等全部自动生成 |
| 测试生成 | 135 个 Python 白盒测试，全部通过 |
| 代码实现 | 8 个 Java 源文件（FrostBow, FrostBowItem, FrostProjectileEntity, ModItems, ModEntityTypes 等） |
| Gradle 编译 | BUILD SUCCESSFUL（Fabric Loom + MC 1.21.1 + Java 21） |
| 白盒验证 | 135 passed, 0 failed |
| 黑盒测试 | Agent 工具调用 JSON 格式错误导致中断（瞬态 LLM 问题，非流水线逻辑缺陷） |

Token 消耗：~5M input tokens，~35K output tokens，约 $25

### 实验结论

- TDD 驱动确实能大幅提升 AI 代码质量，"先写测试再写代码"对 Agent 同样有效
- 参考源系统显著改善了 spec 生成质量，AI 不再凭空编造 API
- Fabric 脚手架确保 Code Agent 生成正确的 Fabric mod（而非 Forge）
- Debug Form 机制（probe → hypothesis → fix → anti-cheat）能自动修复约 70% 的测试失败
- MC mod 全自动开发可行：从需求到可编译 Fabric mod 全程无人干预
- 最大瓶颈：Agent context window 和 token 成本

> _"The vision is clear, and the factory is running."_
