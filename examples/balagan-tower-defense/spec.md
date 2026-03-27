---
title: Balagan
type: spec
has_ui: true
features: Tower placement and real-time combat, Enemy wave system with 20 waves, Adaptive evolution system where enemies gain resistance to your strategy, Economy with coins and tower purchasing and selling, High score tracking with localStorage
---

# Balagan — Adaptive Tower Defense

A browser-based tower defense game where enemy aircraft attack in waves and **evolve against your strategy**. Built with TypeScript and PixiJS, using Kenney's Tower Defense (Top-Down) asset pack.

## Core Concept

Players place anti-aircraft towers on a grid map. Enemy planes fly along a path in waves. Between waves, enemies **adapt**: if you relied on rocket towers, the next wave gains rocket resistance. If you clustered towers, the next wave spreads out. This forces the player to diversify and rethink each wave.

The aesthetic is clean top-down military — green grass, dirt paths, stone tower platforms, and colorful planes buzzing overhead.

## Technology

- **Language**: TypeScript (strict mode)
- **Renderer**: PixiJS 8 (WebGL-accelerated 2D)
- **Bundler**: Vite
- **Test runner**: Vitest
- **No backend** — everything runs client-side in the browser
- **Assets**: Kenney Tower Defense (Top-Down) pack, pre-placed in `public/assets/`

## Game Rules

### Map
- The map is a grid of 64x64px tiles (the Kenney tile size)
- Map dimensions: 15 tiles wide × 10 tiles tall (960×640px game area)
- Each tile is one of: **grass** (buildable), **path** (enemy route), **rock/water** (blocked)
- The path goes from an entry point on one edge to an exit point on another edge
- The path is predefined per level (not procedurally generated for v1)
- Design one good map with a winding path that has multiple tower placement opportunities

### Towers
Players spend **coins** to place towers on grass tiles adjacent to the path. Towers automatically target and fire at enemies within range.

| Tower | Cost | Damage | Range | Fire Rate | Special | Base Tile | Turret Tile |
|-------|------|--------|-------|-----------|---------|-----------|-------------|
| Machine Gun | 50 | 5/shot | 3 tiles | 4 shots/sec | Fast, low damage | tile180 | tile249 (green turret) |
| Rocket Launcher | 100 | 40/shot | 4 tiles | 0.5 shots/sec | Slow, high damage, splash (1 tile radius) | tile181 | tile206 (single rocket) |
| Dual Rocket | 175 | 30×2/shot | 4 tiles | 0.7 shots/sec | Two rockets per volley | tile182 | tile204 (dual rocket) |
| Triple Rocket | 250 | 25×3/shot | 5 tiles | 0.5 shots/sec | Three rockets, largest splash | tile183 | tile205 (triple rocket) |
| Rapid Fire | 150 | 3/shot | 2.5 tiles | 8 shots/sec | Very fast, short range | tile180 | tile250 (red turret) |

**Tower mechanics:**
- Towers rotate to face their current target (smooth rotation, not snapping)
- Towers target the enemy closest to the exit (furthest along the path)
- Towers can be sold for 60% of their purchase price
- Towers cannot be placed on the path or on occupied tiles
- When placed, the base tile renders underneath and the turret tile renders on top, rotatable

### Enemies
All enemies are aircraft that follow the path. Each enemy type has a sprite, health, speed, and coin reward.

| Enemy | Health | Speed | Reward | Sprite |
|-------|--------|-------|--------|--------|
| Scout | 30 | 3 tiles/sec | 10 coins | tile270 (green plane) |
| Fighter | 75 | 2 tiles/sec | 20 coins | tile271 (gray/red plane) |
| Bomber | 200 | 1 tile/sec | 40 coins | tile294 (gray heavy plane) |
| Ace | 120 | 3.5 tiles/sec | 30 coins | tile293 (white plane) |

**Enemy mechanics:**
- Enemies follow the path from entry to exit
- Enemies that reach the exit cost the player 1 life
- Enemies rotate to face their movement direction
- When destroyed, enemies play a brief explosion effect (fire tiles 295-298) and drop coins
- Health bars render above each enemy (thin colored bar)

### Evolution System (Core Differentiator)
After each wave, the game analyzes **how enemies died** in the previous wave:

1. **Damage type tracking**: Track what percentage of total damage came from each tower type
2. **Resistance generation**: The next wave's enemies gain resistance proportional to what killed the previous wave
   - If 70% of damage was from rockets, next wave gets 35% rocket damage reduction
   - If 50% of damage was from machine guns, next wave gets 25% bullet damage reduction
   - Resistance caps at 50% for any single type
3. **Behavioral adaptation**: If towers were clustered in one area, next wave enemies get a 20% speed boost through that zone
4. **Visual indicator**: Resistant enemies get a tinted overlay matching their resistance (red tint = rocket resistant, green tint = bullet resistant)

The evolution rules are **displayed to the player** before each wave starts: "Wave 5: Enemies evolved rocket resistance (35%). They're faster through the south corridor."

### Waves
- 20 waves total
- Each wave specifies: enemy types, count, spawn interval, and any evolution modifiers
- Wave 1: 8 Scouts, 1 second apart
- Waves 2-5: Gradually introduce Fighters, increase count
- Waves 6-10: Introduce Bombers, evolution kicks in noticeably
- Waves 11-15: Introduce Aces, mixed compositions, strong evolution
- Waves 16-19: Large mixed waves, heavy evolution
- Wave 20 (Final): Boss wave — 3 Bombers with 5× health, escorted by Aces
- Between waves: 10 second countdown timer, player can place/sell towers
- Waves auto-start after countdown (no manual start button needed, but a "Send Now" button skips the timer)

### Economy
- Starting coins: 200
- Coins earned: killing enemies (see reward table) + 25 bonus coins per completed wave
- Selling towers returns 60% of cost
- No interest or income between waves beyond the completion bonus

### Player State
- Lives: 10 (enemies reaching the exit cost 1 life each)
- Coins: spent on towers, earned from kills
- Score: total coins earned (not spent) across the game — high score tracking in localStorage
- Game over when lives reach 0
- Victory when wave 20 is cleared with lives > 0

## UI Layout

### Game Screen (960×640 game area + sidebar)
- **Left**: 960×640 game grid
- **Right sidebar** (200px wide): Tower selection panel
  - Show each tower type with icon, name, cost
  - Selected tower highlights
  - Click tower in sidebar, then click grid tile to place
  - Show tower stats on hover
- **Top bar**: Wave number, lives (heart icon), coins (coin icon tile272), score
- **Bottom**: Evolution report between waves ("Enemies evolved: 35% rocket resistance")

### Tower Placement
- When a tower is selected in the sidebar, valid placement tiles highlight (green tint)
- Invalid tiles (path, occupied, rocks) show no highlight
- Click to place, deducting coins
- Right-click or click an existing tower to open sell option

### Visual Effects (PixiJS)
- **Projectiles**: Bullets (tile275) travel from tower to enemy. Rockets (tile251/252) travel with a slight arc.
- **Explosions**: On enemy death, spawn a burst of particles using fire sprites (tile295-298), expanding and fading over 0.5 seconds
- **Muzzle flash**: Brief bright flash at the tower barrel on each shot
- **Tower rotation**: Smooth eased rotation toward current target
- **Enemy health bars**: Thin bar above each enemy, green→yellow→red as health decreases
- **Wave announcement**: Large centered text "WAVE 5" that fades in and out at wave start
- **Coin popup**: "+10" text floats up from killed enemies
- **Evolution overlay**: Colored tint on enemies showing their resistances

## Asset Mapping

All assets are in `public/assets/` as individual PNGs named `towerDefense_tile###.png`.

### Terrain Tiles
- **Grass (full)**: tile024
- **Grass/dirt edges**: tile001-023 (various edge/corner combinations)
- **Dirt/sand (full)**: tile062
- **Water**: tile253-254, 207, 239, 244 (edges and fills)
- **Rocks**: tile135, tile136

### Path Tiles
- **Straight horizontal**: tile200
- **Straight vertical**: tile197 (or rotated 200)
- **Curve tiles**: Various in 193-232 range
- **T-junction / crossroads**: tile226, tile227, tile228

### Tower Tiles
- **Bases** (bottom layer, non-rotating):
  - tile180 (stone platform, standard)
  - tile181 (stone platform, variant)
  - tile182 (stone platform, variant)
  - tile183 (stone platform, diamond)
- **Turrets** (top layer, rotates to aim):
  - tile249 (green single barrel — Machine Gun)
  - tile250 (red heavy barrel — Rapid Fire)
  - tile206 (gray single rocket — Rocket Launcher)
  - tile204 (gray dual rocket — Dual Rocket)
  - tile205 (gray triple rocket — Triple Rocket)

### Enemy Tiles
- tile270 (green plane — Scout)
- tile271 (gray/red plane — Fighter)
- tile294 (gray plane — Bomber)
- tile293 (white plane — Ace)

### Projectile & Effect Tiles
- tile275 (gray bullet)
- tile251 (small rocket)
- tile252 (large rocket)
- tile272 (yellow coin)
- tile295 (small fire)
- tile296 (medium fire)
- tile297 (fire drop)
- tile298 (large fire)

### UI Tiles
- tile085-087 (gray buttons: wrench/upgrade, X/sell, target/info)
- tile107-113 (beige button variants)
- tile268 (green panel background)
- tile269 (beige panel background)
- tile133 (green circle — range indicator)

### Number Tiles
- tile276 (0), tile277 (1), tile278 (2), tile279 (3), tile280 (4)
- tile281 (5), tile282 (6), tile283 (7), tile284 (8), tile285 (9)
- tile286 (%), tile287 ($), tile288 (:), tile289 (+), tile290 (.), tile275 (bullet/period)

## Architecture Guidance

### Modules (suggested, architect may adjust)
1. **engine** — Game loop (requestAnimationFrame), state machine (menu → playing → paused → wave-complete → game-over → victory)
2. **map** — Grid representation, tile types, path definition, coordinate conversion (grid ↔ pixel)
3. **towers** — Tower types, placement logic, targeting (nearest-to-exit), rotation, firing
4. **enemies** — Enemy types, path following, health, speed, death handling
5. **projectiles** — Bullet/rocket travel, hit detection, splash damage calculation
6. **evolution** — Damage tracking per type, resistance calculation, behavioral adaptation, report generation
7. **waves** — Wave definitions, spawn timing, wave state machine
8. **economy** — Coin management, tower purchase/sell, rewards
9. **rendering** — PixiJS setup, sprite loading, layer management (terrain → bases → enemies → turrets → projectiles → UI)
10. **ui** — Sidebar, HUD (top bar), tower selection, placement preview, wave announcements, evolution report
11. **audio** — Web Audio API procedural sounds (shoot, explode, place, wave-start, game-over)
12. **save** — localStorage high scores

### Key Technical Decisions
- Use PixiJS Container hierarchy for render layers (terrain below everything, UI above everything)
- Game logic runs at fixed timestep (60 updates/sec), rendering interpolates
- Path following uses waypoints — enemies move toward next waypoint, switch when close enough
- Tower targeting recalculates each frame — find enemies in range, pick the one furthest along the path
- Projectiles are real objects that travel and can miss if the enemy moves (not hitscan)
- Splash damage checks all enemies within radius of impact point

### File Naming Convention
- Source files in `src/` with subdirectories matching modules
- e.g. `src/towers/tower-types.ts`, `src/enemies/enemy.ts`, `src/evolution/tracker.ts`
- Tests in `src/__tests__/` or colocated as `*.test.ts`
- Entry point: `src/main.ts`
- HTML: `index.html` at root (Vite convention)

## What Success Looks Like

1. The game loads in a browser with no errors
2. A map renders with a visible winding path through grass terrain
3. The sidebar shows tower options with costs
4. Clicking a tower and clicking the map places it (with coin deduction)
5. Pressing "Start" (or wave auto-starting) sends enemies along the path
6. Towers rotate, fire projectiles, and destroy enemies
7. Dead enemies show explosion effects and drop coins
8. Between waves, the evolution report shows what resistances enemies gained
9. The game is winnable (wave 20 clearable with good strategy) and losable (poor tower placement)
10. Score saves to localStorage

## What Failure Looks Like

- Enemies walking off the path or through towers
- Towers not firing or targeting empty space
- Projectiles flying in wrong directions
- Economy broken (infinite money, or can never afford anything)
- Evolution producing invincible enemies by wave 10
- Game freezing or FPS dropping below 30
- Sprites not loading or rendering as white rectangles
