# ToyShop Development Workflow

## 伪自举开发模式 (Pseudo-Bootstrap Development)

ToyShop 的后续开发采用"伪自举"模式：不再由 Claude 直接编写代码，而是通过 PM pipeline 驱动 OpenHands 完成代码生成和测试，Claude 作为 UX Agent 监督每个环节。

### 角色分工

- **Claude (UX Agent)**: 写需求、review openspec 文档、review 生成代码、监督管线健康度
- **PM Pipeline**: 需求澄清 → 架构设计 → 文档生成（自动）
- **OpenHands (Coding Agent)**: 代码生成、测试编写、TDD 验证（自动）

### 开发流程

```
1. create  — Claude 编写需求，创建 batch
2. spec    — Pipeline 生成 openspec (proposal/design/tasks/spec)
   ← Claude review openspec 文档，必要时手动修改后重跑
3. tasks   — 解析任务列表（仅展示）
4. tdd     — OpenHands 生成代码 + 测试，TDD 验证
   ← Claude review 生成的代码质量和测试覆盖
5. 通过后手动集成回 toyshop 代码库
```

### CLI 命令

```bash
python3 -m toyshop.pm_cli create --name <name> --input <req>
python3 -m toyshop.pm_cli spec   --batch <dir>
python3 -m toyshop.pm_cli tasks  --batch <dir>
python3 -m toyshop.pm_cli tdd    --batch <dir>
python3 -m toyshop.pm_cli status --batch <dir>
```

### 关键原则

- 每个阶段之间有人工 review 断点，不是全自动一口气跑完
- Claude 重点关注管线健康度和系统整体表现，而非手写代码
- 生成的代码是独立项目，review 通过后再集成回 toyshop
- 这不是真正的自举（没有修改自身代码），但改动的代码最终会应用到自身体系
- 效率和质量并进：OpenHands 负责量产，Claude 负责质控
