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
  - three.js `0.186.1` (`build/three.module.js` + `examples/jsm/` addons), from jsdelivr, for the
    3-D town (village and replay).
  - Fonts from Google Fonts: Inter, Noto Sans TC and JetBrains Mono, all under the SIL OFL.

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
| `js/town3d/townmap.js` | The 3-D town as the village / replay map: who stands where, walking, cutaways, the DOM overlay (tags, bubbles, place labels, markers, talk lines). Same interface as the old pixel map. |
| `js/town3d/scene.js` | The renderer: island, water, sky, plaza, paths, props, buildings, figures, camera, fx, degrade step. |
| `js/town3d/buildings.js`, `kit.js`, `figure.js`, `clay.js` | Parametric buildings, the furniture/prop kit, the chibi residents, shared clay helpers. |
| `js/town3d/town.json`, `js/town3d/residents.json` | All 3-D tuning: layout, buildings, interiors, palette, camera, render settings; resident looks and motion. |
| `js/town3d/portraits.js` | Resident portraits for the UI, rendered once from the 3-D figures. |
| `js/threads.js` | Conversation threads (one conversation = one card) for the live column and the chronicle. |
| `js/ui.js`, `js/icons.js` | Small shared components (avatars, ...) and the inline line-icon set (no icon dependency). |

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

## The 3-D town

The village and replay views render the whole town in three.js (Townscaper-like clay
miniature). The 2-D pixel map (Pixi) is retired and its files are gone (git history keeps
them).

- **Generated, not modelled.** Every mesh is built from `js/town3d/town.json` (island,
  plaza, paths, buildings, interiors, props) and `js/town3d/residents.json` (the ten
  residents' looks and motion), with vertex colours only: no model files, no textures.
  All tuning lives in those two files.
- **Clock-driven light.** Sun, sky and water follow the sim clock (day / dusk / night);
  ACES tone mapping, soft shadows, GTAO ambient occlusion. Transparent things (smoke
  sprites) are kept out of the AO pass.
- **Cutaways.** When someone is inside, a building's roof and upper floor lift away to
  show the room; residents sit at their anchors (bed, chair, counter). A toggle forces
  them open.
- **Residents.** Chibi figures with jointed limbs and one occupation prop each; walk,
  idle, talk, sit and sleep poses. UI portraits are rendered once from these figures.
- **Camera.** Drag to pan, wheel / pinch to zoom, right-drag (or Shift+drag, two
  fingers) to orbit: 360 degrees around, pitch clamped to 20-70 degrees, damped.
  WASD / arrow keys pan relative to the current heading (Shift = faster), bounded to
  the island like drag-pan, and ignored while a text field has focus. Clicking a name
  tag follows that resident; any manual move ends it; double-click returns to the
  overview.
- **Cast card.** Clicking a resident opens one floating card on the map next to them;
  it can be dragged and closed, never covers the live column, and keeps its position
  for the session.
- **Event layer.** A major beat marks the people involved and raises a glass event
  card; a conversation draws a line between its two speakers; clicking a live-column
  item moves the camera to them.
- **Performance.** If the frame rate stays under 45 fps for 3 seconds, AO is switched
  off and the pixel ratio drops to 1.
