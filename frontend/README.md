# Frontend (integration notes only)

Your existing frontend isn't included here — drop it into this folder as-is.
It needs to talk to the backend using the API contract below.

**Base URL:** `http://localhost:8000` (or wherever the backend is deployed)

## Auth
- `POST /api/auth/register` — `{ name, email, password }` → `{ token, user }`
- `POST /api/auth/login` — `{ email, password }` → `{ token, user }`
- `GET /api/auth/me` — header `Authorization: Bearer <token>` → current user
- `POST /api/auth/logout` — clears client-side token (stateless JWT)

Store the returned `token` (e.g. in memory or an httpOnly-equivalent storage
strategy on your platform of choice) and send it as
`Authorization: Bearer <token>` on every subsequent call.

## Documents
- `POST /api/upload` — multipart form: `file`, `session_id` (+ auth header)
  → `{ status, total_chunks_indexed }`

## Chat
- `POST /api/chat` — form fields: `query`, `session_id` (+ auth header)
  → `{ status, response, sub_queries_used }`
- `GET /api/chat/history/{user_id}/{session_id}` → `{ history: [...] }`
- `DELETE /api/session/{session_id}` (+ auth header) → clears vectors + history
  for that session

`session_id` is any string your frontend generates per chat thread (e.g. a
UUID created when the user starts a new chat). It scopes both document
retrieval and history, so two sessions for the same user never see each
other's documents.
