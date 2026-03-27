# Adam

**Orchestrated long-form software engineering.**

Adam takes a project specification and autonomously builds the entire codebase — architecture, implementation, testing, code review, visual inspection, and iterative repair — without human intervention. It replaces you as the orchestrator so you can focus on the big ideas.

```
$ pip install adam-eng
$ cd my-project
$ mkdir context && vi context/spec.md
$ adam
```

## What it does

You describe what you want to build in a `context/spec.md` file. Adam reads it, asks only the questions the spec doesn't answer, and then:

1. **Architects** the project — technology stack, module structure, interfaces, conventions (Opus)
2. **Plans** each module — decomposes into files with interfaces, dependencies, and implementation order (Sonnet)
3. **Scaffolds** the project — creates directories, config files, installs dependencies
4. **Implements** every file — from its spec, with awareness of dependency source code and project conventions (Sonnet)
5. **Validates** each file — runs tests, linter, type checker, build. Soft critics evaluate code quality, security, and performance with file-type-aware prompts
6. **Repairs** failures — diagnostician identifies root cause (including cross-file issues), repair agent applies minimum fix, re-validates
7. **Generates tests** — writes tests for each accepted file
8. **Integration audits** — after all modules pass individually, checks cross-module coherence (Opus)
9. **Observes its own output** — screenshots UI via Playwright, smoke-tests API endpoints, runs CLI tools and verifies output
10. **Revises** — if integration or visual issues are found, marks affected files for re-implementation and sweeps again
11. **Stops** when six conditions are met: all obligations resolved, all tests pass, all hard validators pass, soft critics above threshold, visual inspection passes, all files accepted

The entire process runs autonomously. You approve the architecture at the start (or skip with `--no-checkpoints`) and come back to a working project.

## How it works

Adam is derived from [Postwriter](https://postwriter.app), which orchestrates novel generation through multi-pass planning, validation, and repair. Adam applies the same cognitive model to code:

### Cognitive layers

| Layer | Model | Role |
|-------|-------|------|
| Architectural reasoning | Opus | Project design, integration audit, visual evaluation |
| Implementation | Sonnet | File writing, test writing, code review, repair, diagnosis |
| Mechanical validation | Shell | Test runner, linter, type checker, build checker |

### The implementation loop

For each file:

```
implement(file_spec, context) →
    code = FileImplementer.write(spec, dependencies, conventions)
    write to disk
    for round in range(max_repair_rounds):
        run validators (test, lint, type, build)
        if all pass:
            run soft critics (quality, security, performance)
            if above threshold → ACCEPT
        diagnose failure (including cross-file issues)
        repair with minimum fix
        re-validate
    generate tests → write to disk
    link to obligations → git commit
```

### Multi-pass revision

After all files are implemented:

```
for pass in range(max_passes):
    implement all pending files
    integration audit (Opus)
    visual audit (screenshot + evaluate) / API smoke test / CLI verify
    if issues found → mark affected files pending → next pass
    if all stop conditions met → done
```

## Installation

```bash
pip install adam-eng
```

Requires Python 3.12+ and an Anthropic API key. No database, no Docker, no configuration.

On first run, Adam will prompt for your API key and save it to `~/.adam/config`.

### Optional dependencies

```bash
pip install adam-eng[visual]   # Playwright for UI screenshot inspection
pip install adam-eng[postgres]  # PostgreSQL instead of SQLite
pip install adam-eng[all]       # Everything
```

## Usage

### New project

```bash
mkdir my-project && cd my-project
mkdir context

# Describe what to build
cat > context/spec.md << 'EOF'
Build a REST API for task management with:
- User authentication (JWT)
- CRUD operations for tasks and projects
- Role-based authorization
- PostgreSQL database
EOF

# Optionally specify tech preferences
cat > context/tech-stack.md << 'EOF'
Language: Python 3.12
Framework: FastAPI
Database: PostgreSQL
Test runner: pytest
EOF

# Run Adam
adam
```

### Context files

Place any of these in `context/`:

| File | Purpose |
|------|---------|
| `spec.md` | Project specification — what to build |
| `tech-stack.md` | Technology preferences |
| `architecture.md` | Structural constraints |
| `style.md` | Coding conventions |
| `reference/*.md` | API docs, examples |
| `assets/*` | Sprites, images, fonts (copied to project) |

Adam reads everything, infers what it can, and asks about the rest. The more you specify, the less you're asked.

### CLI options

```bash
adam                        # Run with defaults (architecture checkpoint on)
adam --no-checkpoints       # Skip human approval, fully autonomous
adam --profile fast_draft   # Fewer repair rounds, no critics
adam --profile high_quality # Max repair rounds, visual inspection
adam --debug                # Verbose console output
```

### Profiles

| Profile | Repair rounds | Critics | Visual | Use case |
|---------|--------------|---------|--------|----------|
| `standard` | 3 | Yes | No | Default for most projects |
| `fast_draft` | 1 | No | No | Quick prototyping |
| `high_quality` | 5 | Yes | Yes | Production-quality output |
| `budget_conscious` | 1 | No | No | Minimal API usage |

### Resume

If Adam is interrupted, run `adam` again in the same directory. It detects the `.adam/` state directory and resumes from where it left off, skipping files already completed.

## Architecture

```
src/adam/
  agents/          # LLM-powered agents (architect, implementer, repair, etc.)
  orchestrator/    # Implementation loop, planning, multi-pass revision
  validation/      # Hard validators (test/lint/build) and soft critics
  inspection/      # Visual (Playwright), API smoke, CLI verification
  execution/       # Shell runner, dependency manager, dev server
  store/           # Project store (SQLAlchemy), context slicer, events
  context/         # Spec loader, asset manifest, condenser
  llm/             # Anthropic client with tiering, budgets, streaming
  git/             # Git operations (init, commit, rollback)
  repair/          # Repair planner, priority ordering
  cli/             # Click CLI, Rich display, checkpoints
  prompts/         # Jinja2 templates for all agent prompts
  models/          # SQLAlchemy ORM (project, modules, files, obligations)
```

### Agent roles

| Agent | Model | Purpose |
|-------|-------|---------|
| Architect | Opus | Technology stack, module structure, conventions |
| Module Planner | Sonnet | Decompose modules into files with interfaces |
| Scaffolder | Sonnet | Create project skeleton and config files |
| File Implementer | Sonnet | Write a single source file from its spec |
| Test Writer | Sonnet | Generate tests for implemented files |
| Error Diagnostician | Sonnet | Diagnose failures, identify cross-file issues |
| Repair Agent | Sonnet | Apply minimum targeted fix |
| Integration Auditor | Opus | Check cross-module coherence after implementation |
| Route Discoverer | Sonnet | Extract pages/routes for visual inspection |
| Code Quality Critic | Sonnet | Evaluate readability, maintainability, idiomaticity |
| Security Critic | Sonnet | Check for vulnerabilities (file-type-aware) |
| Performance Critic | Sonnet | Check for efficiency issues (file-type-aware) |

### Observation methods

Adam chooses the observation method based on project type:

| Project type | Observation |
|-------------|-------------|
| UI (web app, game) | Playwright screenshots evaluated by Opus vision |
| API (REST, GraphQL) | Curl-based smoke tests against discovered endpoints |
| CLI tool | Run with sample inputs, verify output |

### Stop conditions

Adam declares a project complete when ALL of:

1. All specified features have implementing files (obligations resolved)
2. All tests pass
3. No hard validator failures (lint, types, build)
4. All soft critic scores above threshold
5. Visual inspection passes (if UI project)
6. All files accepted

## Project state

All state lives in `.adam/` in the project directory:

```
.adam/
  project.json    # Phase, project ID, title
  adam.db          # SQLite database (modules, files, obligations, events)
  adam.log         # Full debug log of every LLM call and decision
```

The log file captures every prompt sent, every response received, every validation result, and every repair attempt. If something goes wrong, the diagnosis is in the log.

## Development

```bash
git clone https://github.com/avigold/adam.git
cd adam
uv sync
uv run pytest tests/          # 250 tests
uv run ruff check src/ tests/ # Lint
```

## Credits

- Built with [Claude](https://claude.ai) by Anthropic
- Asset pack by [Kenney](https://kenney.nl) (CC0)
- Derived from [Postwriter](https://postwriter.app)

## License

MIT
