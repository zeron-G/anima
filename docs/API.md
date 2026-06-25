# ANIMA API Reference (contract)

The backend (`anima/dashboard/server.py`, default port **8420**) exposes a
versioned REST surface under `/v1`, a WebSocket feed at `/ws`, and a few legacy
`/api/*` endpoints. This is the contract a standalone frontend or integration
builds against. Source of truth: `anima/api/router.py` + `anima/api/*.py`.

> Contract version: **`1`** (see `GET /v1/version` → `data.api`). Bump on any
> breaking change to `/v1`.

---

## Auth

One scheme gates the whole protected surface (`/v1`, `/api`, `/ws`) via a single
middleware (`DashboardServer.auth_middleware`):

- **Enabled iff `dashboard.auth.password` is set** (in `local/env.yaml`). Blank →
  auth disabled, everything open (dev default).
- **Login**: `POST /v1/auth/login {"password": "..."}` → `{"token": "<jwt>", "expires_at": <epoch>}`.
  The JWT is HMAC-SHA256 signed with `dashboard.auth.token` (set a stable secret
  so tokens survive restarts; blank → random per-process).
- **Send the token** on every other request:
  - REST: header `Authorization: Bearer <jwt>`.
  - WebSocket: query string `/ws?token=<jwt>` (browsers can't set WS headers).
- **Public paths (no auth)**: `POST /v1/auth/login`, `GET /v1/health`, `GET /v1/version`.
  The SPA shell (`/`, `/assets/*`, `/static/*`, `/desktop`) is also public.
- Unauthorized → **401** `{"ok": false, "error": "unauthorized"}`.

Rate limit: login is limited to 5 attempts / 60s per client IP (429 on exceed).

---

## Response & error format

The **standard envelope** (used by new endpoints — `/v1/health`, `/v1/version` —
and all error responses; see `anima/api/responses.py`):

```jsonc
// success
{ "ok": true,  "data": <payload> }
// error
{ "ok": false, "error": "<message>" }   // + HTTP status
```

> **Migration note:** many existing `/v1` handlers still return their historical
> shapes (bare resource dicts, `{"success": true}`, `{"status": "..."}`, or
> per-resource keys like `messages`/`results`/`nodes`). Those are documented
> per-endpoint below. The `{ok,data}` envelope is the forward standard; new and
> refactored handlers use it. Errors are already uniform: `{"error": "..."}`
> (the middleware emits `{"ok": false, "error": ...}`).

**Status codes**: `400` bad/missing input · `401` unauthorized · `404` not found
· `409` conflict (e.g. node offline) · `429` rate limited · `501` not implemented
· `503` subsystem not ready · `500` unexpected (logged server-side, body is a
generic `internal error` — never leaks internals).

---

## Endpoints (`/v1`)

### Meta (public)
| Method | Path | Returns |
|---|---|---|
| GET | `/v1/health` | `{ok,data:{status:"ok"}}` — liveness probe |
| GET | `/v1/version` | `{ok,data:{server:"<semver>", api:<int>}}` |

### Auth
| Method | Path | Body / notes |
|---|---|---|
| POST | `/v1/auth/login` | `{password}` → `{token, expires_at}` (public) |
| POST | `/v1/auth/change-password` | `501` (edit `local/env.yaml`) |

### Chat
| Method | Path | Notes |
|---|---|---|
| POST | `/v1/chat/send` | `{message, session_id?}` → `{status:"queued", correlation_id}` |
| POST | `/v1/chat/stream` | `{message}` → **SSE** stream (`text/event-stream`); events `event: <type>\ndata: <json>` |
| GET | `/v1/chat/history` | `?page&limit&session_id` → `{messages[], page, limit, total}` |
| GET | `/v1/chat/sessions` | `{sessions[]}` |
| POST | `/v1/chat/golden` | `{scene,user_text,eva_reply,score}` mark a golden reply |

### Soulscape (persona / identity / memory surface)
`GET/PUT /v1/soulscape/{emotion,persona,personality,relationship}` ·
`GET /v1/soulscape/{growth-log,golden-replies,style-rules,boundaries,drift}` ·
`PUT /v1/soulscape/style-rules` · `DELETE /v1/soulscape/golden-replies/{id}`.
(See `anima/api/soulscape.py` for per-field shapes.)

### Evolution
`GET /v1/evolution/{status,history,governance}` · `PUT /v1/evolution/governance/mode {mode}`.

### Memory
`GET /v1/memory/{search?q,recent,stats,documents,documents/search?q}` ·
`POST /v1/memory/documents/import` · `DELETE /v1/memory/documents/{id}`.

### Network
`GET /v1/network/{nodes,channels}` · `GET /v1/network/nodes/{id}/conversation` ·
`POST /v1/network/nodes/{id}/chat`.

### Robotics (PiDog nodes)
`GET /v1/robotics/nodes` · `GET /v1/robotics/nodes/{id}` ·
`POST /v1/robotics/nodes/{id}/{command,nlp,speak,refresh,exploration/start,exploration/stop}`.
`command` requires non-empty `command`; `nlp`/`speak` require non-empty `text`.

### Settings
`GET /v1/settings/{config,skills,system,usage,traces}` · `PUT /v1/settings/config` ·
`POST /v1/settings/{skills/install,restart,shutdown}` · `DELETE /v1/settings/skills/{name}`.

---

## Legacy `/api/*` (unversioned, auth-gated, same scheme)

`GET /ws` (WebSocket) · `POST /api/upload` · `GET /api/uploads` ·
`POST /api/{debug,tts,stt}` · `GET /api/voice/{filename}`. These predate `/v1`
(multipart / streaming / audio). Folding them under `/v1/voice` & `/v1/files` is
tracked as future work.

---

## WebSocket — `/ws?token=<jwt>`

On connect the server sends a **full snapshot**, then pushes updates every ~2s.
Messages are JSON. Two shapes coexist (the client sniffs them):

- **Typed event**: `{ "type": <T>, "data": {...} }` where `T` ∈
  `heartbeat | stream | tool_call | activity | thinking | proactive | evolution | emotion_shift | node_event`.
- **Legacy snapshot**: a bare object containing `emotion`, `uptime_s`,
  `chat_history`, `activity`, etc. (the client converts it to a `heartbeat` event).

`stream` events carry `{correlation_id, text, done}` for incremental chat output.
The client (`eva-ui/src/api/websocket.ts`) auto-reconnects with backoff. Retiring
the legacy snapshot in favour of typed events only is future work.

---

## CORS (cross-origin / split frontend)

Same-origin (SPA served by this backend) needs no CORS. For a frontend on a
different origin, add it to `dashboard.cors.allow_origins` (default ships only
the Vite dev ports). `dashboard.cors.allow_all: true` reflects any origin —
**dev only**. See `docs/DEPLOYMENT.md`.
