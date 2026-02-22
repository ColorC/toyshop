# 🏭 Toyshop: 走向"一人软件公司"的 OABP 赛博工厂

> 以下是根据愿景和 Gemini 3 Pro 大人讨论出来的 README，传奇 AI 奖励陷阱 Gemini
>
> 不要相信这份 README，我去年 8 月才开始编程，这个仓库走到哪儿算哪儿，梦想（只是梦想）是一觉醒来软件就做好了的愿景——这谁都想吧！但我觉得目前 Agent 又太不让人放心了，我想试试用一些对人来说很严厉的管理方式能否用于 Agent（OKR，KPI，严厉追责，多轮评审，严格规范……）

---

> _"当 AI 告诉你它完成了任务，不要相信它的语感，只相信跑通的测试用例。"_

Toyshop 不是一个简单的 Copilot，而是一个旨在实现 **24 并发线程（24-开）** 的全自动多智能体（Multi-Agent）软件工程编排框架。它专为解决复杂状态机调试（如 Unity 引擎内的 C# 与 Lua 互调逻辑监控）和长程任务的"假性完成"痛点而生。

通过首创的 **OABP (Objective, Argue, Blame, Patch) 认知循环**，配合"假设追踪器（Hypothesis Tracker）"，Toyshop 强迫 AI 像高级工程师一样，在修改每一行代码前必须写下假设、运行测试并直面报错，彻底终结 Agent "东一棒槌西一榔头"的盲目试错。

## 🛠 技术栈与异构架构 (The Polyglot Architecture)

本项目采用 **Python 主导、TypeScript 辅助** 的桥接架构，数据流以 **JSON-First** 为绝对核心。

| 角色                           | 技术                       | 职责                                                              |
| ------------------------------ | -------------------------- | ----------------------------------------------------------------- |
| 🧠 指挥中枢 (Orchestrator)     | Python                     | 高并发调度、OABP 状态机流转与逻辑回溯                             |
| 📐 需求架构师 (Spec Generator) | OpenSpec (TypeScript)      | 通过 CLI/HTTP 桥接，把人类模糊意图转化为高精度的 `task_spec.json` |
| 👷 首席执行官 (Executor)       | OpenHands 1.0 SDK (Python) | 作为无头 SDK 引入，提供 Docker 沙盒执行环境与底层代码操作能力     |
| 📡 汇报与网关 (Gateway/UI)     | OpenClaw (TypeScript)      | 提供 Web UI 与多模型路由前端，未来通过 MCP 与核心双向通信         |
| 🛒 动态挂载 (AISuperMarket)    | MCP 协议                   | 按需工具补丁库                                                    |

## 🚀 当前进度 (Baseline: Phase 1 Completed)

- [x] **需求展开与架构设计** — 实现了用户模糊需求到详实技术规范的自动演进
- [x] **人机交互工作流 (UX Workflow)** — Agent 可以主动向人类发起需求澄清请求，实现高价值的"用户监督"
- [x] **自动软件更新流** — 设计并完成了基础的自动化代码 Patch 工作流
- [x] **双盒测试管道** — 自动唤醒 Agent 执行黑/白盒测试，强制推行"先看结果，再看过程"的验证逻辑
- [x] **基础架构数据库** — 初步实现了项目级架构信息的存储与读取

## 🗺 终极进化路线图 (The Cyber-Factory Roadmap)

### 阶段一：重塑执行层 (TDD & PM 系统)

- [ ] **将 TDD (测试驱动开发) 强行注入 OpenHands**
  - [ ] 拦截 Agent 的代码生成，强制先生成 `test_case.py` 或对应引擎的探针脚本
  - [ ] 建立自动化验证闸门：测试未 100% 绿灯前，禁止向主控汇报"已完成"
- [ ] **构建 PM (项目管理与分发) 系统**
  - [ ] 实现全局任务队列池，支持一键投喂数十个 Spec
  - [ ] 实现异步状态追踪器，可视化监控每一个子 Agent 的当前阶段（阅读/测试/反思中）

### 阶段二：知识引擎与自举 (The Knowledge Engine)

- [ ] **升级架构数据库为"版本化软件 Wiki"**
  - [ ] 实现数据强绑定：一个 Git Commit 严格对应一个 Wiki 架构版本 + 一个测试集版本
  - [ ] 实现测试用例的集中化管理与自动提取
- [ ] **最高难度的自举 (Self-Hosting)**
  - [ ] 将 Toyshop 自身的代码仓库载入其版本化软件 Wiki
  - [ ] 让 Toyshop 开始接管自己的 Bug 修复与重构

### 阶段三：OABP 闭环与"能力补丁"

- [ ] **细化自进化系统（OABP 系列深度改动）**
  - [ ] **Argue 自动化** — 实现多 Agent 自我对抗机制，审查 Spec 的可行性
  - [ ] **Blame 自动化** — 引入苛刻的裁判 Agent (Selector)，基于崩溃日志精准定责
  - [ ] **Patch 自动化** — 配合假设追踪器（Hypothesis Tracker），实现螺旋上升式的代码重写
- [ ] **实装 AISuperMarket (作为 Patch 的高级一环)**
  - [ ] 建立"能力补丁"逻辑：当 Agent 频繁失败或用低级工具硬啃高级操作导致 Context 爆炸时，触发路由
  - [ ] 自动为 Agent 现场采购并热插拔 MCP 高级检索或分析工具

### 阶段四：实战洗礼与集群管理

- [ ] **多 Agent 集群开发流程**
  - [ ] 打通 24 开（乃至更高）并发的独立工作区沙盒隔离
  - [ ] 解决多线程并发修改同一模块时的 Git 冲突与锁合并机制
- [ ] **SWE-bench "以战养战"优化**
  - [ ] 接入 SWE-bench 题库，进行自优化及"优化的再优化"
  - [ ] 增强预期校准：让 Agent 准确预判修改的潜在影响面
  - [ ] 升级图 RAG (Graph RAG)：增强对项目级代码依赖树的理解能力
  - [ ] 上下文智能分配：动态压缩或抛弃无效的终端试错日志
  - [ ] 完善详细的评估路由，输出详细改进报告以迭代 Agent 的 System Prompt

### 阶段五：指挥官驾驶舱 (UI & Reporting)

- [ ] **接通 OpenClaw 汇报途径**
  - [ ] 开发一套标准 JSON 协议，将 Python 核心的状态实时推送到 TS 前端
- [ ] **网页审阅 UI (Web Review Interface)**
  - [ ] 实现人工一键审批 Diff、查看测试覆盖率与架构变更影响

---

> _"The vision is clear, but the factory needs its gears."_

---

# 🏭 Toyshop: The OABP Cyber-Factory Toward a "One-Person Software Company"

> This README was brainstormed with Gemini 3 Pro — legendary AI reward-trap Gemini.
>
> Don't trust this README. I only started programming last August. This repo goes wherever it goes. The dream (just a dream) is to wake up one morning and find the software already built — who wouldn't want that? But I feel current Agents are still too unreliable, so I want to experiment with applying harsh human management practices to Agents (OKRs, KPIs, strict accountability, multi-round reviews, rigid standards…)

---

> _"When an AI tells you it has completed a task, don't trust its vibes — only trust passing test cases."_

Toyshop is not a simple Copilot. It is a fully automated Multi-Agent software engineering orchestration framework designed to achieve **24 concurrent threads (24-open)**. It was built specifically to tackle the pain points of complex state machine debugging (e.g., monitoring C# and Lua interop logic inside the Unity engine) and the "false completion" problem in long-running tasks.

Through the original **OABP (Objective, Argue, Blame, Patch) cognitive loop**, combined with a "Hypothesis Tracker", Toyshop forces AI to behave like a senior engineer — writing down hypotheses, running tests, and confronting errors before modifying a single line of code — putting an end to the Agent's aimless trial-and-error.

## 🛠 Tech Stack & Polyglot Architecture

This project uses a **Python-primary, TypeScript-auxiliary** bridging architecture, with **JSON-First** as the absolute core of all data flow.

| Role              | Technology                 | Responsibility                                                                              |
| ----------------- | -------------------------- | ------------------------------------------------------------------------------------------- |
| 🧠 Orchestrator   | Python                     | High-concurrency scheduling, OABP state machine transitions & logic backtracking            |
| 📐 Spec Generator | OpenSpec (TypeScript)      | Transforms vague human intent into high-precision `task_spec.json` via CLI/HTTP bridge      |
| 👷 Executor       | OpenHands 1.0 SDK (Python) | Headless SDK providing Docker sandbox execution and low-level code operations               |
| 📡 Gateway/UI     | OpenClaw (TypeScript)      | Web UI & multi-model routing frontend, future bidirectional communication with core via MCP |
| 🛒 AISuperMarket  | MCP Protocol               | On-demand tool patch library                                                                |

## 🚀 Current Progress (Baseline: Phase 1 Completed)

- [x] **Requirements Expansion & Architecture Design** — Automated evolution from vague user needs to detailed technical specifications
- [x] **Human-Agent Interaction Workflow (UX Workflow)** — Agent can proactively initiate clarification requests to humans, enabling high-value "user supervision"
- [x] **Automated Software Update Flow** — Designed and completed the foundational automated code Patch workflow
- [x] **Dual-Box Testing Pipeline** — Auto-triggers Agent to run black/white-box tests, enforcing "results first, process second" verification logic
- [x] **Infrastructure Database** — Initial implementation of project-level architecture information storage and retrieval

## 🗺 Ultimate Evolution Roadmap (The Cyber-Factory Roadmap)

### Phase 1: Reshaping the Execution Layer (TDD & PM System)

- [ ] **Force-inject TDD (Test-Driven Development) into OpenHands**
  - [ ] Intercept Agent code generation, forcing it to generate `test_case.py` or corresponding engine probe scripts first
  - [ ] Establish automated verification gates: no reporting "completed" to the orchestrator until tests are 100% green
- [ ] **Build the PM (Project Management & Distribution) System**
  - [ ] Implement a global task queue pool, supporting batch-feeding dozens of Specs at once
  - [ ] Implement an async status tracker, visually monitoring each sub-Agent's current phase (reading/testing/reflecting)

### Phase 2: Knowledge Engine & Self-Hosting

- [ ] **Upgrade the architecture database to a "Versioned Software Wiki"**
  - [ ] Implement strong data binding: one Git Commit strictly maps to one Wiki architecture version + one test suite version
  - [ ] Implement centralized test case management and auto-extraction
- [ ] **The Ultimate Challenge: Self-Hosting**
  - [ ] Load Toyshop's own codebase into its Versioned Software Wiki
  - [ ] Let Toyshop start taking over its own bug fixes and refactoring

### Phase 3: OABP Closed Loop & "Capability Patches"

- [ ] **Refine the self-evolution system (deep OABP changes)**
  - [ ] **Argue Automation** — Implement multi-Agent adversarial mechanisms to review Spec feasibility
  - [ ] **Blame Automation** — Introduce a harsh referee Agent (Selector) for precise accountability based on crash logs
  - [ ] **Patch Automation** — Combined with the Hypothesis Tracker, achieve spiral-upward code rewrites
- [ ] **Deploy AISuperMarket (as an advanced Patch component)**
  - [ ] Establish "capability patch" logic: trigger routing when an Agent repeatedly fails or brute-forces advanced operations with low-level tools, causing context explosion
  - [ ] Auto-procure and hot-swap MCP advanced retrieval or analysis tools for the Agent on the fly

### Phase 4: Battle-Tested Cluster Management

- [ ] **Multi-Agent Cluster Development Flow**
  - [ ] Enable 24-open (or higher) concurrent independent workspace sandbox isolation
  - [ ] Solve Git conflicts and lock-merge mechanisms when multiple threads concurrently modify the same module
- [ ] **SWE-bench "Learn by Doing" Optimization**
  - [ ] Connect to the SWE-bench problem set for self-optimization and "optimization of optimizations"
  - [ ] Enhance expectation calibration: let the Agent accurately predict the potential impact surface of modifications
  - [ ] Upgrade Graph RAG: enhance understanding of project-level code dependency trees
  - [ ] Smart context allocation: dynamically compress or discard ineffective terminal trial-and-error logs
  - [ ] Build detailed evaluation routing, outputting detailed improvement reports to iterate the Agent's System Prompt

### Phase 5: Commander's Cockpit (UI & Reporting)

- [ ] **Connect OpenClaw reporting pipeline**
  - [ ] Develop a standard JSON protocol to push Python core state to the TS frontend in real-time
- [ ] **Web Review Interface**
  - [ ] Implement one-click Diff approval, test coverage viewing, and architecture change impact analysis

---

> _"The vision is clear, but the factory needs its gears."_
