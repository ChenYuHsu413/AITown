# Frontend

The town UI is served as static files by the FastAPI server: `/` is `frontend/index.html`, and
everything else under `frontend/` is served at `/static/` (for example `frontend/js/app.js` is
`/static/js/app.js`). There is no build step.

## Why there is no Next.js and no build toolchain

- **The page is small, and the server is Python.** It has four views and one inspector, all fed
  by an existing JSON/WebSocket API. A Node toolchain (bundler, transpiler, `node_modules`,
  lockfile, CI build) would be a second runtime to install, pin and keep secure, and that buys
  nothing a page this size needs.
- **Editing a file is the whole deploy.** Static files are read from disk on every request. Edit a
  module, reload the browser, and the change is live. The same files run in development and in
  production, so there is no build output to drift from the source.
- **SSR and routing don't apply.** The UI is a live operator console behind a WebSocket, not a
  content site. It has no SEO or first-paint requirement that server rendering would serve. The
  views are chosen with a `#hash`.
- **Dependencies are few and pinned.** The page loads them through an import map in
  `index.html`, each at an exact version:
  - Vue `3.5.13`, ESM browser build, from unpkg. This includes the template compiler, so
    components are plain objects with string templates.
  - PixiJS `8.22.0`, `pixi.min.mjs`, from jsdelivr, for the village map (WebGL).
  - Fonts from Google Fonts: Huninn, Noto Sans TC and JetBrains Mono, all under the SIL OFL.

  Upgrading means editing one URL and checking the page.

## Layout

| Path | What it is |
|---|---|
| `css/tokens.css` | The only place colors, fonts, radii and motion timings are defined. Day/night comes from two numbers on `:root` (`--n`, `--dusk`) written from the sim clock. |
| `css/app.css` | Layout and components, all in terms of tokens. |
| `js/app.js` | Root component: header, view switcher, day/night driver, heartbeat. |
| `js/store.js` | Shared state, the WebSocket, fetch helpers, speed tiers, the 精簡/詳細 mode. |
| `js/names.js` | zh-TW display layer: names, placeholder resolver, event phrasing, the day/night phase. |
| `js/views/*.js` | 鎮誌 (chronicle), 小鎮 (village + live column), 回放 (replay), 關係 (relations). |
| `js/inspector.js`, `js/ops.js` | Resident cast sheet (operator view) and the operator panel. |
| `js/pixmap.js` | The village map on PixiJS: camera, residents, lights, weather, bubbles. |
| `js/pixelart.js` | Programmatic pixel art: residents, buildings, props, critters, ground. No third-party assets. |
| `js/threads.js` | Conversation threads (one conversation = one card) for the live column and the chronicle. |
| `map/town.json` | The tile world: the fountain plaza, building doors, curved paths (also the walking routes), anchors, aprons, ground patches, props, lamps. |

## One event source

The live village map and the live column both read the same stream: the store's
`onTick`. They get the same events, in the same order, at the same moment.

The engine generates a conversation's lines in the background and delivers them in one burst.
The store releases them one line at a time, at a speaking pace, per pair of residents. A map
speech bubble and the matching line in the column therefore appear together.

## Conversation threads

The engine stamps a conversation's `talk_start` (and any `confide` or `confronted` from it) at
the minute the conversation began. It stamps each `say` line at that minute plus the line's
index. A thread's key is therefore (unordered pair, `talk_start` minute), and a `say` belongs to
the latest thread of its pair that started at or before the line's minute.

## Map performance

The static ground is baked once into a single texture. Each building and prop is one sprite.
What moves each frame:

- ten residents;
- speech bubbles;
- about 25 river glints;
- rain, when it is raining;
- a few slow touches: now and then a tree's canopy sways, chimneys of occupied buildings
  smoke, petals fall along the cherry lane, a bird crosses every 15–40 seconds, and a
  butterfly sometimes visits a flower bed.

Each of these is capped (at most 24 smoke puffs and 26 petals at a time).

If the frame rate stays under 45 fps for 3 seconds, the map switches to low quality:

- it drops the decorative motion (river glints, idle bob, half the rain, the installation's
  shimmer, swaying trees, smoke, petals, birds, butterflies);
- it caps the map at 30 fps.

After 20 seconds it tries full quality again. If the frame rate drops a second time, it stays
in low quality. The current tier is shown on the canvas as `data-quality`.

The map's ticker stops while the village view is hidden. Users who set "reduce motion" in their
system keep the walking, but lose the decorative motion.
