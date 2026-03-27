---
type: tech_stack
---

# Tech Stack

- **Language**: TypeScript 5.x (strict mode enabled)
- **Renderer**: PixiJS 8 (latest stable — `pixi.js` npm package)
- **Bundler**: Vite 6
- **Test Runner**: Vitest
- **Package Manager**: npm
- **Target**: Modern browsers (Chrome, Firefox, Safari, Edge — all latest)
- **No backend**: Entirely client-side, static files only
- **No CSS framework**: Minimal CSS, game UI rendered in PixiJS canvas
- **Asset format**: Individual PNG sprites (64×64px tiles from Kenney pack)

## Build Commands

- `npm install` — install dependencies
- `npm run dev` — start Vite dev server
- `npm run build` — production build to `dist/`
- `npm test` — run Vitest
- `npm run lint` — run tsc --noEmit (type checking)

## Project Structure

```
balagan/
  index.html
  package.json
  tsconfig.json
  vite.config.ts
  public/
    assets/          # Kenney sprite PNGs
  src/
    main.ts          # Entry point
    engine/          # Game loop, state machine
    map/             # Grid, tiles, path
    towers/          # Tower types, placement, targeting
    enemies/         # Enemy types, path following
    projectiles/     # Bullets, rockets, hit detection
    evolution/       # Adaptation system
    waves/           # Wave definitions, spawning
    economy/         # Coins, purchase, sell
    rendering/       # PixiJS setup, sprite loading, layers
    ui/              # Sidebar, HUD, announcements
    audio/           # Procedural sound effects
    save/            # localStorage high scores
    __tests__/       # Test files
```
