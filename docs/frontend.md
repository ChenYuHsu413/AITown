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
| `js/ui.js`, `js/icons.js` | Small shared components (avatars, ...) and the inline line-icon set (no icon dependency). |
| `town3d.html`, `css/glass.css`, `js/town3d/` | The 3-D validation slice at `/town3d-preview` (see below). |
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

## Map depth and density

- **Shadows.** Everything that stands on the ground has two shadows: a soft contact shadow at
  its feet, and a cast silhouette whose length and direction follow the sun. The cast shadow
  points west in the morning, is short at noon, falls long to the east in the late afternoon,
  and disappears at night.
- **Elevation.** The plaza is a raised platform: a lit rim, a shaded stone face on its visible
  side, and steps wherever a path meets it. Door aprons have a two-step front edge. Retaining
  ledges (`ledges` in `map/town.json`) have a lit grass lip, a stone face, and steps where a
  path crosses them. The riverbank sits below the grass, with an earth face.
- **Density.** The open ground has grass-colour variation, tufts, pebbles, clover, flowers and
  footpaths (`trails`). Bushes, tall grass, flower clumps, rocks, stumps and logs are scattered
  through it (`scatter`), and woodland frames the town along the north, east and south edges
  (`forest`).
- **Placement rules.** Scattered items and the woodland are placed procedurally but
  deterministically, so the town looks the same on every load. They stay clear of buildings,
  paths, plazas, props and the spots where residents stand.

## Layout and pixel texture

- **A compact town.** The map is 64 × 46 tiles. The café, the bakery and the market ring the
  fountain plaza. The two homes and the office (a three-storey brick building, the tallest in
  town) form a second ring. The park sits in the south-west corner.
- **Little open ground.** Between buildings there are narrow back lanes and small clumps of
  trees. The only large open ground is the park and the riverbank.
- **A 1 px dark outline on every object.** `outlined()` in `pixelart.js` pads each canvas by
  one pixel and draws the outline into that border.
- **Trees are clusters of leaf clumps.** Each clump is lit from the top left, with four
  steps of light from highlight to shadow and a dark rim where clumps meet. Pines are jagged
  tiers. Bushes use the same clumps.
- **One warm tint over the whole map.** The day/night multiply layer is warm at noon too,
  which costs nothing extra.
- **One 8 px grain.** Plaza stones, roof tiles, bricks and stone aprons all use roughly the same
  8 px module.

## Cutaway interiors

A building with people inside opens like a doll's house. The roof fades to a faint ghost
and the room shows: the back wall, the floor, cut side walls, and a low front wall with a
doorway. Each room has its furniture, and the residents inside are at their spots.

This is a view, not a walkable interior: there is no interior map and no furniture
interaction.

- **Layout lives in the map file.** Each room's furniture and anchors are in
  `map/town.json` under `interiors.<location>`, in the building's own pixel coordinates.
- **Where each resident goes depends on what they are doing:**

  | Action | Spot |
  |---|---|
  | `sleep` | their own bed |
  | `eat`, `rest` | a seat |
  | `work` | the owner's spot first (ㄐㄐ at the café counter, 安哥 at the bakery oven); office workers sit at the desks |
  | anything else | a free standing spot |

  Whoever doesn't fit stands outside.
- **Each home has five beds**, one per resident of that household in the seed (`bedOf`).
  Sleepers lie under a blanket in their own colour.
- **Open rooms are lamp-lit at night.** They and the people in them are drawn above the night
  tint, so a sleeping town shows one lit room per occupied house.
- **When rooms open is a setting.** By default a room opens automatically whenever someone is
  inside. A small switch on the map changes this to opening a building only when you click it.
  The choice is remembered in the browser (`localStorage`).

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

## The 3-D validation slice (`/town3d-preview`)

A standalone page, separate from the live UI at `/`, that tests a Townscaper-style 3-D
direction. It is a validation slice, not the full town: one island with one café and a
corner of the plaza on moving water, plus two residents.

- **Files.** `town3d.html` (its own import map: Vue `3.5.13` and three.js `0.186.1` from
  jsdelivr), `css/glass.css` (the dark "glass observatory" panels, this page only),
  `js/town3d/main.js` (the Vue shell), `js/town3d/world.js` (the scene) and
  `js/town3d/slice.json` (the scene description).
- **Generated, not modelled.** Every mesh is built from `slice.json` (rounded boxes,
  slab roofs, windows, doors, awnings) with vertex colours only: no model files and no
  textures.
- **Clock-driven light.** The sun's angle and colour follow the sim clock; the renderer
  uses ACES tone mapping, soft shadows and GTAO ambient occlusion.
- **Read-only reuse.** It reuses the store (WebSocket, paced dialogue), the zh display
  layer and the live column component without changing the main page.
