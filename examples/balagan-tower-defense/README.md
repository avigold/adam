# Example: Balagan — Adaptive Tower Defense

This directory contains the exact context files used to generate [Balagan](https://meetadam.app/play), a browser-based tower defense game where enemies evolve against your strategy.

Adam produced 130 TypeScript files across 11 modules from these two files and a folder of sprites — with no human intervention after approving the architecture.

## Files

- **`spec.md`** — Complete game design document. Tower types with stats, enemy types with behaviors, the evolution mechanic, economy model, wave progression, UI layout, and a full mapping of every sprite in the Kenney asset pack to its role in the game.

- **`tech-stack.md`** — Technology choices: TypeScript, PixiJS 8, Vite, Vitest. Includes exact build commands and the expected project structure.

## How to use

```bash
mkdir my-tower-defense && cd my-tower-defense
mkdir -p context/assets

# Copy these spec files
cp path/to/spec.md context/
cp path/to/tech-stack.md context/

# Download the Kenney Tower Defense (Top-Down) asset pack from:
# https://kenney.nl/assets/tower-defense-top-down
# Extract the PNGs into context/assets/

# Run Adam
adam
```

Adam will:
1. Read the spec and tech stack — skip most bootstrap questions
2. Design the architecture (you approve it)
3. Scaffold the project (package.json, tsconfig, vite config)
4. Copy 299 sprites to `public/assets/`
5. Install npm dependencies
6. Implement all files, running critics after each one
7. Generate tests
8. Run integration audit
9. Attempt visual inspection (if Playwright is installed)
10. Report completion status

## What makes this a good spec

The spec is effective because it's **specific where it matters**:

- Exact tower stats (damage, range, fire rate, cost) — the implementer doesn't have to guess
- Exact sprite mappings (tile180 = tower base, tile249 = machine gun turret) — no ambiguity about which asset to use
- The evolution formula in prose: "resistance = min(0.5, damageShare × 0.5)" — implementable without interpretation
- UI layout described spatially: "960×640 game area + 200px sidebar"
- Success and failure criteria: what "working" and "broken" look like

And **vague where it doesn't**:

- No pixel-perfect UI mockups — the implementer decides layout within the sidebar
- No specific pathfinding algorithm mandated — A* or waypoints, whatever works
- No class hierarchy prescribed — the architect chooses the patterns
- Wave composition for waves 2-19 sketched, not specified enemy-by-enemy

This balance — precise requirements, flexible implementation — is what lets Adam make good decisions without over-constraining it.
