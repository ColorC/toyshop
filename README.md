# ToyShop

AI software factory powered by OpenSpec contracts and OpenHands agents.

ToyShop takes a natural-language project description, generates formal interface specifications (OpenSpec), then drives LLM agents through a test-driven development pipeline to produce working code — with built-in anti-cheat verification.

## Architecture

```
User Request
    │
    ▼
┌─────────────┐     ┌──────────────┐
│  PM System  │────▶│ OpenSpec Gen │  ← design.md / api.md / models.md
│  (pm.py)    │     │              │
└─────────────┘     └──────┬───────┘
                           │
                           ▼
                  ┌─────────────────┐
                  │  TDD Pipeline   │
                  │  (tdd_pipeline) │
                  └────────┬────────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
    ┌───────────┐   ┌───────────┐   ┌───────────┐
    │Test Agent │   │Code Agent │   │Anti-cheat │
    │ (write    │   │ (implement│   │  Agent    │
    │  tests)   │   │  + debug) │   │ (verify)  │
    └───────────┘   └───────────┘   └───────────┘
```

## TDD Pipeline Phases

| Phase | What happens |
|-------|-------------|
| 1 | Extract interfaces from OpenSpec docs |
| 2 | Test Agent writes whitebox tests |
| 3 | Code Agent implements (smoke test only) |
| 4 | Automated test run |
| 4.5 | Test Agent fills Debug Forms for failures |
| 4.6 | Code Agent fixes based on Debug Forms |
| 4.7 | Anti-cheat: detect flipped tests, generate variants |
| 5 | Blackbox validation (new tests, no source access) |

## Change Pipeline

For brownfield projects, the change pipeline handles incremental modifications:

1. **change-create** — snapshot current state + describe the change
2. **change-analyze** — impact analysis (which modules/interfaces affected)
3. **change-spec** — evolve OpenSpec documents
4. **tdd modify** — run TDD pipeline on the delta

## Installation

```bash
pip install -e .
```

Requires `openhands-sdk` as a dependency.

## Usage

### As a library

```python
from toyshop import run_tdd_pipeline, TDDResult

result: TDDResult = await run_tdd_pipeline(
    project_dir="/path/to/project",
    spec_dir="/path/to/project/spec",
    workspace_dir="/path/to/project/workspace",
)
```

### Via bridge (JSON-RPC over stdin/stdout)

```bash
python3 -m toyshop.bridge
```

Used by the TypeScript extension layer in [openclaw](https://github.com/ColorC/openclaw-toyshop).

### PM CLI

```bash
python3 -m toyshop.pm_cli --pm-root /path/to/projects
```

## Testing

```bash
# Unit tests
pytest tests/test_openspec.py tests/test_workflows.py tests/test_agent.py -v

# Full suite (requires API keys)
pytest tests/ -v
```

## License

MIT
