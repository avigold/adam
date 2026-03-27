# CLAUDE.md

## Project: Adam — Orchestrated Long-Form Software Engineering

This document specifies a system for generating, testing, inspecting, and iterating complete software projects using a hierarchy of specialised agents, explicit project state, multi-pass critique, and repair loops. The architecture is derived from the Postwriter novel generation system, adapted for code. The core insight is the same: one-pass generation is inadequate for serious engineering, and the solution is an orchestrated pipeline of narrow agents operating in act→observe→verify→repair cycles.

The target is not code completion or snippet generation. The target is a system that autonomously builds entire software projects — from architecture through implementation through testing through visual inspection — and does not stop until critic agents have high confidence the project meets its specification.

---

## 1. Cognitive Model

The system models software engineering as a layered cognitive process, analogous to how the human brain uses different subsystems for different tasks:

### Layer 1: Pattern Recognition (Haiku tier)
Fast, cheap, mechanical checks. Syntax validation, linting, type checking, import resolution, file existence verification. These are the "looks like rain" judgements — no deep reasoning required.

### Layer 2: Trained Intuition (Sonnet tier)
Implementation decisions that an experienced engineer makes without deep deliberation. Choosing data structures, naming conventions, file organisation, API design patterns, error handling strategies. The workhorse layer — most code is written here.

### Layer 3: Architectural Reasoning (Opus tier)
High-level design decisions requiring broad context and deep reasoning. System architecture, technology selection, interface design, dependency management, performance strategy, security model. Used sparingly but critically — at project inception and at major structural decision points.

### Layer 4: Verification and Critique (mixed tiers)
Observation of the system's own output. Running tests, reading error messages, viewing rendered UI, checking accessibility, measuring performance, evaluating code quality. This is the feedback loop that makes the system self-correcting.

---

## 2. Objective

Build an orchestration framework for software engineering that:

- plans at multiple scales (architecture → modules → files → functions)
- implements at the file level with awareness of the full project
- maintains explicit canonical project state outside the code
- tests continuously and automatically
- visually inspects rendered output using vision-capable models
- audits code quality, security, accessibility, and performance
- performs constrained repair loops when tests fail or critics flag issues
- supports iterative refinement based on human feedback
- knows when to stop — when critic confidence is high and tests pass

The system should optimise for:

- correctness (tests pass, no runtime errors)
- completeness (all specified features implemented)
- code quality (readable, maintainable, idiomatic)
- visual fidelity (UI matches specification or reasonable defaults)
- security (no obvious vulnerabilities)
- performance (no egregious inefficiencies)

---

## 3. Non-goals

This system is not a code autocompleter.
This system is not a chatbot that answers programming questions.
This system is not a linter or static analysis tool.
This system is not limited to a single language or framework.

---

## 4. Core Design Principle

Treat the project as four linked representations:

1. **Code layer** — the source files themselves
2. **Project-state layer** — what has been implemented, what remains, what depends on what, what has been tested, what has been visually verified
3. **Quality layer** — linting results, type checking results, test results, critic scores, security audit results
4. **Specification layer** — what was asked for, acceptance criteria, constraints, user preferences

No important reasoning should depend on code alone if it can instead depend on structured state.

---

## 5. Operating Model

The system works hierarchically:

- project specification
- architecture and technology decisions
- module decomposition
- file-level implementation
- function-level implementation
- test writing and execution
- visual inspection (for UI projects)
- quality audit
- repair of failures

Each level has:
- its own representation
- its own goals
- its own validator types
- its own repair loop
- explicit dependency links upward and downward

A file-level implementer should not be forced to infer the entire architecture.
A quality auditor should not flatten local implementation decisions.

---

## 6. Architecture Overview

### 6.1 Primary Subsystems

- **Orchestrator** — task decomposition, ordering, state coordination, stop conditions
- **Project Store** — canonical state: what exists, what works, what's pending
- **Planning Layer** — architect, module planner, file planner, dependency resolver
- **Implementation Layer** — file writer, function writer, test writer
- **Execution Layer** — test runner, build runner, dev server launcher
- **Observation Layer** — output reader, screenshot taker, vision analyser
- **Validation Layer** — hard validators (tests pass, types check, lints clean) and soft critics (code quality, security, performance, accessibility, visual fidelity)
- **Repair Layer** — error diagnosis, targeted fix generation, regression prevention
- **Context Loader** — reads spec files from the project directory

### 6.2 Execution Phases

1. Project bootstrap (read specs, ask user questions)
2. Architecture design (Opus)
3. Module and file planning (Sonnet)
4. Iterative implementation loop:
   a. Implement next file/module
   b. Run tests
   c. If tests fail → diagnose → repair → re-test (up to N rounds)
   d. Run soft critics
   e. If critics flag issues → repair → re-validate
   f. Visual inspection (if UI project)
   g. Mark module complete
5. Integration testing
6. Full quality audit
7. Visual audit (screenshot every page/state, evaluate with vision)
8. Final repair pass
9. Declare done — only when all tests pass AND all critics score above threshold

---

## 7. Canonical Data Model

### 7.1 Project Model

- title
- description
- specification (from context files + user answers)
- technology stack
- architecture decisions
- status (planning, implementing, testing, auditing, complete)

### 7.2 Module Model

- name
- purpose
- dependencies (other modules)
- files
- status (pending, implementing, tested, complete)
- test coverage

### 7.3 File Model

- path
- purpose
- language
- dependencies (other files, external packages)
- status (pending, written, tested, reviewed)
- quality scores

### 7.4 Test Model

- path
- type (unit, integration, e2e, visual)
- target files/modules
- status (pending, passing, failing)
- last run output
- failure diagnosis

### 7.5 Task Model (Obligation Ledger)

Similar to Postwriter's promise model. Tracks:
- what was specified
- what has been implemented
- what has been tested
- what remains
- what is blocked and by what

---

## 8. Agent Roles

### 8.1 Architect (Opus)
- Reads specification
- Chooses technology stack
- Designs module structure
- Defines interfaces between modules
- Identifies critical path
- Makes build/deploy decisions

### 8.2 Module Planner (Sonnet)
- Breaks modules into files
- Defines file purposes and interfaces
- Orders implementation by dependency
- Identifies what needs tests

### 8.3 File Implementer (Sonnet)
- Writes a single file from its specification + context
- Has access to: file spec, module spec, interfaces of dependencies, project conventions
- Does NOT see the entire codebase — only what's relevant (context slicing)

### 8.4 Test Writer (Sonnet)
- Writes tests for implemented files
- Has access to: the implementation, the spec, the module interfaces
- Writes unit tests, integration tests, and (for UI) visual test specifications

### 8.5 Test Runner (Haiku + shell execution)
- Executes test suites
- Parses output
- Classifies failures (syntax error, logic error, missing dependency, flaky test)
- Reports structured results

### 8.6 Visual Inspector (Opus with vision)
- Takes screenshots of rendered UI (via headless browser)
- Evaluates visual output against specification
- Flags: layout issues, missing elements, broken styling, accessibility problems
- Provides structured feedback for repair

### 8.7 Code Quality Critic (Sonnet)
- Reviews code for: readability, maintainability, idiomaticity
- Checks for: dead code, unnecessary complexity, poor naming, missing error handling
- Scores and provides specific repair suggestions

### 8.8 Security Critic (Sonnet)
- Reviews for: injection vulnerabilities, exposed secrets, insecure defaults, missing input validation
- Checks dependencies for known vulnerabilities

### 8.9 Performance Critic (Sonnet)
- Reviews for: N+1 queries, unnecessary re-renders, missing indexes, unbounded loops, memory leaks
- Provides specific suggestions

### 8.10 Error Diagnostician (Sonnet)
- Receives: test failure output, stack trace, relevant source code
- Produces: diagnosis (root cause), proposed fix (specific code change), confidence level

### 8.11 Repair Agent (Sonnet)
- Receives: diagnosis + proposed fix + preserve constraints
- Applies the minimum change needed
- Does not refactor beyond the fix
- Does not introduce new features

### 8.12 Integration Auditor (Opus)
- Runs after all modules are individually complete
- Tests cross-module interactions
- Identifies integration issues
- Proposes structural fixes if needed

---

## 9. The Implementation Loop

This is the core cycle. For each file or module:

```
implement(file_spec, context):
    code = file_implementer.write(file_spec, context)
    save(code)

    for round in range(max_rounds):
        test_results = test_runner.run(relevant_tests)

        if test_results.all_pass:
            critics = run_critics(code)
            if critics.all_above_threshold:
                return ACCEPT
            else:
                diagnosis = identify_weakest_dimension(critics)
                code = repair_agent.fix(code, diagnosis)
                save(code)
                continue

        diagnosis = error_diagnostician.diagnose(test_results)
        code = repair_agent.fix(code, diagnosis)
        save(code)

    return ACCEPT_WITH_WARNINGS  # best effort after max rounds
```

For UI projects, add after critic pass:
```
    screenshot = take_screenshot(relevant_pages)
    visual_eval = visual_inspector.evaluate(screenshot, spec)
    if visual_eval.issues:
        code = repair_agent.fix(code, visual_eval)
        save(code)
```

---

## 10. Context Slicing

Each agent receives only what it needs:

- **Architect**: full spec, technology constraints, user preferences
- **File implementer**: file spec, module interface, dependency interfaces, project conventions, 2-3 related files for style reference
- **Test writer**: the implementation, the spec, the module interface
- **Repair agent**: the failing code, the error, the diagnosis, preserve constraints
- **Visual inspector**: the screenshot, the spec for that page/component

Token budget awareness: trim oldest/least-relevant context when approaching limits.

---

## 11. Context Files

Users can place files in a `context/` directory:

- `spec.md` — project specification
- `architecture.md` — architectural preferences or constraints
- `style.md` — coding style preferences
- `tech-stack.md` — technology requirements
- `reference/` — example code, API documentation, design mockups
- `*.png`, `*.jpg` — UI mockups (processed by vision)

All optional. If present, they inform the planning agents and reduce the number of bootstrap questions.

---

## 12. Stop Conditions

The system declares the project complete when ALL of:

1. All specified features have corresponding implementations
2. All tests pass
3. No hard validator failures (lint, types, build)
4. All soft critic scores above threshold
5. Visual inspection passes (if UI project)
6. The obligation ledger has no unresolved items

If any condition cannot be met after max repair rounds, the system reports what remains unresolved and asks for human guidance.

---

## 13. Validation Model

### 13.1 Hard Validators (pass/fail, block acceptance)

- Tests pass
- TypeScript/mypy/equivalent type check passes
- Linter passes (or only warnings, no errors)
- Build succeeds
- No import errors
- No runtime crashes on startup

### 13.2 Soft Critics (scored, influence repair priority)

- Code readability (0-1)
- Maintainability (0-1)
- Idiomaticity (0-1)
- Security (0-1)
- Performance (0-1)
- Accessibility (0-1, for UI projects)
- Visual fidelity (0-1, for UI projects)
- Test coverage adequacy (0-1)
- Error handling completeness (0-1)

---

## 14. Visual Inspection

For projects with UI:

1. Launch a headless browser (Playwright)
2. Navigate to each page/state defined in the spec
3. Take screenshots
4. Send screenshots to Opus with vision
5. Opus evaluates: layout correctness, visual completeness, responsiveness, obvious bugs
6. Structured feedback fed back to repair agent

This is the "viewing the result" that distinguishes Adam from blind code generation.

---

## 15. Model Tiering

| Tier | Role | Used for |
|------|------|----------|
| Opus | Architectural reasoning, visual inspection | Project design, major decision points, screenshot evaluation |
| Sonnet | Implementation, critique, repair | File writing, test writing, all critics, error diagnosis, fixes |
| Haiku | Mechanical validation | Parsing test output, linting, type checking, file existence, dependency resolution |

---

## 16. Repair Philosophy

Identical to Postwriter:

- Narrow: fix only what's broken
- Ordered: highest-priority issues first
- Traceable: every fix linked to a diagnosis
- Reversible: git commits after each accepted change
- Minimally destructive: don't refactor what works

---

## 17. Failure Modes to Guard Against

- **Over-engineering**: building abstractions before they're needed
- **Repair flattening**: too many fix rounds making code worse
- **Test gaming**: writing code to pass tests rather than meet the spec
- **Critic monoculture**: all critics converging on the same bland style
- **Context drift**: losing track of the project's purpose in the details
- **Dependency hell**: pulling in packages to solve problems that don't exist

---

## 18. Key Differences from Postwriter

| Aspect | Postwriter | Adam |
|--------|-----------|------|
| Output | Prose | Code |
| Verification | Critics score text | Tests pass or fail |
| Observation | Soft quality metrics | Hard test results + visual inspection |
| Branching | Multiple rhetorical strategies | Possibly multiple implementation strategies for critical components |
| Canon | Character states, promises | Module states, obligation ledger |
| Repair trigger | Low scores, hard validation failure | Test failure, critic flags, visual bugs |
| Stop condition | Composite score threshold | All tests pass + all critics satisfied |

---

## 19. Implementation Principles

- Tests are first-class citizens, not afterthoughts
- Every file gets committed to git after acceptance
- Visual inspection is not optional for UI projects
- The system should be honest about what it cannot verify
- Human review is available at any point but should rarely be needed
- The obligation ledger is the source of truth for completeness
- Context slicing is critical — agents must not see the whole project when they don't need to
- Repair rounds have hard limits to prevent infinite loops

---

## 20. Operating Assumptions

- One-pass code generation is unreliable for projects beyond a few files
- Tests catch bugs that critics miss; critics catch quality issues that tests miss
- Visual inspection catches UI bugs that no amount of code review will find
- The repair loop converges for most issues within 3-5 rounds
- Explicit project state prevents the drift that kills long-horizon generation
- Human engineering judgement remains necessary for taste, priority, and ambiguous requirements

---

## 21. CLI Behaviour

Running `adam` in any directory should:

1. Check for a `.adam` project state file
2. If none exists: start a new project
   - Load context files from `context/` if present
   - Ask the user questions about the project (skipping what context files answer)
   - Design architecture
   - Implement iteratively
3. If a project exists and is in progress: offer to resume
4. If a project exists and is complete: offer to revise, extend, or start new

The system should produce clear, real-time progress output showing what it's doing, what's passing, what's failing, and how far along it is.
