# Deploying AI Town

AI Town is a **single long-lived process**: FastAPI serves the UI and a
WebSocket feed while a background `asyncio` loop advances the simulation. It must
run on an **always-on container platform** (Railway, Fly.io, Render, a VPS) —
**not** a serverless/function platform, which would freeze or recycle the loop
between requests.

Cost stays low on free/hobby tiers because the sim **auto-suspends when no
browser is connected** (the clock freezes ~10 s after the last WebSocket
disconnects and resumes instantly on the next connection). Health probes and
REST polling never wake it — only a real WebSocket client does.

The container needs **no API keys and no database** to run. The first deploy
uses the built-in deterministic `MockProvider`: fully functional UI, **zero LLM
cost**. Real models and persistence are opt-in env vars you add later.

---

## What the image contains

- Base `python:3.12-slim`, dependencies from `requirements.txt` only.
- Runs as a non-root user.
- Startup respects the platform-injected `PORT`:
  `uvicorn backend.app.server:app --host 0.0.0.0 --port ${PORT:-8000}`.
- Health endpoint: `GET /healthz` → `{"ok": true}` (does not touch sim state).

Local sanity check before deploying (needs Docker):

```bash
docker build -t aitown .
docker run -p 8000:8000 aitown
# open http://localhost:8000  ·  curl http://localhost:8000/healthz
```

Or bring the app up together with the (future) database in one command:

```bash
docker compose up --build
```

> **This is container testing, not day-to-day dev.** Local development stays on
> `uvicorn backend.app.server:app` as always. Don't run the dev server and the
> container at the same time — they both bind `:8000` (and Compose also binds
> `:5432`) and will clash. By default the Compose `app` runs **stateless**
> (in-memory, mock LLM), identical to `docker run`; the Postgres service starts
> ready, and you opt into persistence by uncommenting `AI_TOWN_DB_URL` in
> `docker-compose.yml` (see "Adding Postgres later").

---

## Environment variables

All optional. **Set them only in the platform's env settings — never commit them
to the repo.** With none set, the app runs on the mock provider with no DB.

| Variable | Purpose | First deploy |
|---|---|---|
| `PORT` | Injected by the platform; the container already honors it | (automatic) |
| `AI_TOWN_LIVE` | `1` = use real LLM providers instead of mock | leave unset |
| `OPENAI_API_KEY` | OpenAI models (used when `AI_TOWN_LIVE=1`) | leave unset |
| `GEMINI_API_KEY` | Gemini models (used when `AI_TOWN_LIVE=1`) | leave unset |
| `AI_TOWN_DB_URL` | PostgreSQL+pgvector persistence; unset = in-memory | leave unset |

> Note: the live/mock split for the **server** is driven by `AI_TOWN_LIVE`; the
> `run_day.py` script uses a `--live` flag instead. Either way, mock is the
> default and costs nothing.

---

## Railway

1. Push this repo to GitHub.
2. On [railway.app](https://railway.app): **New Project → Deploy from GitHub
   repo** and pick the repo. Railway detects the `Dockerfile` and builds it.
3. Railway injects `PORT` automatically — no start-command override needed (the
   `Dockerfile` `CMD` already binds `0.0.0.0:${PORT}`).
4. Under the service's **Settings → Networking**, click **Generate Domain** to
   get a public HTTPS URL. Open it — the town loads over `https`, and the
   frontend automatically uses `wss://` for the WebSocket (see below).
5. (Optional) **Variables** tab → add any env vars from the table above. Redeploy
   to apply. For the first deploy, add nothing.

Health checks: Railway will hit `/` by default; you can point its healthcheck at
`/healthz` under Settings if you prefer a state-free probe.

---

## Fly.io

1. Install [`flyctl`](https://fly.io/docs/hands-on/install-flyctl/) and
   `fly auth login`.
2. From the repo root:

   ```bash
   fly launch --no-deploy
   ```

   Accept app name/region when prompted. Fly detects the `Dockerfile`. Say **no**
   to adding a database for now. This writes a `fly.toml`.
3. In `fly.toml`, make sure the internal port matches what the app binds. Fly
   sets `PORT=8080` by default, and the container reads it, so use:

   ```toml
   [http_service]
     internal_port = 8080
     force_https = true
     auto_stop_machines = true
     auto_start_machines = true
     min_machines_running = 0

   [[http_service.checks]]
     method = "get"
     path = "/healthz"
     interval = "30s"
     timeout = "5s"
   ```

   (Or set `internal_port = 8000` **and** add `[env] PORT = "8000"` — just keep
   the two in agreement.)
4. Deploy:

   ```bash
   fly deploy
   ```

5. `fly open` opens the public HTTPS URL. Set secrets later with
   `fly secrets set OPENAI_API_KEY=... AI_TOWN_LIVE=1` (secrets are stored by Fly,
   never in the repo).

> Fly's `auto_stop_machines`/`auto_start_machines` can idle the **machine** when
> there's no traffic and cold-start it on the next request — this is separate
> from the app's own in-process idle-suspend. Both are fine for a demo; see
> "Free-tier sleep" below for what a cold start means for sim state.

---

## WebSocket

Both Railway and Fly natively proxy WebSockets over TLS — nothing to configure.
The frontend already picks the right scheme:

```js
const ws = new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/ws');
```

So on an `https://` deployment it connects with `wss://` automatically. No code
change is needed for either platform.

---

## Adding Postgres later (persistence)

The first deploy is stateless (in-memory). To turn on event/memory persistence
and pgvector memory retrieval:

1. Add a Postgres service:
   - **Railway:** *New → Database → PostgreSQL* in the same project. Enable the
     `vector` extension (the app expects pgvector; use a pgvector-capable image
     if your provider doesn't ship the extension — locally that's
     `pgvector/pgvector:pg16`, see `docker-compose.yml`).
   - **Fly:** `fly postgres create` then `fly postgres attach <db-app>`.
2. Set `AI_TOWN_DB_URL` on the service, using the **asyncpg** driver form:

   ```
   postgresql+asyncpg://USER:PASSWORD@HOST:5432/DBNAME
   ```

3. Install the persistence deps if you slim them out later — they're already in
   `requirements.txt` (`sqlalchemy`, `asyncpg`, `pgvector`).
4. Redeploy. On boot the server creates the `events`, `memories`, `llm_calls`,
   `simulation_runs` tables and switches memory retrieval to pgvector cosine
   search. Without `AI_TOWN_DB_URL` it stays fully in-memory, exactly as before.

---

## Free-tier sleep behavior (known & acceptable)

On free/hobby tiers the platform (and the app's own idle-suspend) will pause when
no one is watching. Simulation state currently lives **in memory only**, so a
**cold start restarts the town from Day 1 06:00**. This is expected for the
stateless first deploy.

Proper state recovery (resuming mid-day after a restart) arrives once Postgres is
attached — the event/memory tables are the foundation for replay and rehydration.
Until then, treat each cold start as a fresh day.
