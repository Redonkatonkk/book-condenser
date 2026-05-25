# Book Condenser

Book Condenser is a FastAPI web app for compressing EPUB, PDF, and TXT books chapter by chapter with MiniMax, then exporting the condensed result as EPUB.

The app is designed for long-running book jobs and Docker deployment on port `9121`. It analyzes a book first, lets the user decide whether to condense one chapter, ten chapters, selected chapters, failed chapters, or the whole book, and keeps already completed chapters available for partial export. Login is optional: guests can still upload and condense a book, while logged-in users get a personal library, saved jobs, and their own MiniMax API key.

## Features

- Upload EPUB, PDF, or TXT books.
- Optional email/password accounts with local session cookies. Email verification is not required.
- Personal library for logged-in users, including previous uploads, progress, completed chapters, downloads, continuation, and deletion.
- Store each logged-in user's MiniMax API key locally and use it before any shared/server key.
- Analyze chapter structure and completeness before condensation starts.
- Choose to condense 1 chapter, 10 chapters, selected chapters, failed chapters, or the whole book.
- Use MiniMax domestic (`minimax.cn / minimaxi.com`) or global (`minimax.io`) API regions.
- Keep guest mode available through a server key, a previously stored guest key, or a one-time entered key.
- Show total progress, elapsed condensation time, and ETA based on completed chapter throughput.
- Stop the current condensation batch and continue later.
- Retry one failed chapter or all failed chapters.
- Export all completed chapters or selected completed chapters as EPUB.
- Open any completed chapter in a two-column preview with the original chapter on the left, condensed chapter on the right, and counts for both.
- Preserve EPUB images by converting them to stable placeholders during condensation and restoring them during EPUB export.
- Build multi-architecture Docker images for `linux/amd64` and `linux/arm64` through GitHub Actions.

## Code Structure

```text
.
├── app/
│   ├── main.py              # FastAPI routes, static app mount, export download checks
│   ├── job_manager.py       # Job lifecycle, persistence, batching, stop/retry/export orchestration
│   ├── minimax_client.py    # MiniMax API validation and chapter condensation prompt
│   ├── credentials.py       # Server-side MiniMax key storage and region resolution
│   ├── user_store.py        # Local SQLite users, sessions, and per-user API keys
│   ├── book_parser.py       # EPUB/PDF/TXT parsing, chapter detection, EPUB image placeholders
│   ├── epub_writer.py       # Condensed EPUB writer and image restoration
│   ├── schemas.py           # Dataclasses and job/chapter status enums
│   ├── text_utils.py        # Text normalization, unit counting, filename/image helpers
│   ├── config.py            # Environment-driven runtime configuration
│   └── static/
│       ├── index.html       # Single-page UI shell
│       ├── styles.css       # UI styling
│       └── app.js           # Browser workflow, polling, chapter selection, export controls
├── tests/
│   ├── test_api.py          # API/job behavior and export route tests
│   ├── test_book_parser.py  # Parsing and image placeholder tests
│   └── test_epub_writer.py  # EPUB export and image preservation tests
├── .github/workflows/
│   └── docker.yml           # Test, multi-arch build, and GHCR publish workflow
├── Dockerfile
├── docker-compose.yml       # Local build/run compose file
├── docker-compose.pull.yml  # NAS/server compose file for prebuilt GHCR image
├── requirements.txt
├── requirements-dev.txt
└── pyproject.toml
```

Runtime files are not part of the source tree. Uploaded books, job state snapshots, exports, saved API credentials, local users, sessions, and per-user API keys live under `BOOK_CONDENSER_STORAGE` (`storage` locally, `/data` in Docker) and are git-ignored.

## Architecture Notes For Handoff

- `JobManager` keeps active work in memory and persists each job snapshot to `storage/jobs/<job_id>/job_state.json`. On restart, completed and ready jobs reload; in-flight analysis/condensation is paused or marked failed with a clear message.
- Local users and sessions are stored in SQLite at `storage/app.db`. Passwords use PBKDF2 hashes; session cookies are HttpOnly and stored server-side as token hashes.
- Logged-in jobs carry `user_id`; user-owned jobs require that user's session to read, condense, export, preview, or delete. Guest jobs remain accessible by job id for simple no-login use.
- A browser tab can be closed without stopping an active job, as long as the backend process keeps running. The frontend stores the current job id in `localStorage` and resumes polling when reopened.
- Logged-in users can also resume from the personal library, independent of browser `localStorage`.
- Stopping a batch sets `stop_requested`, clears the active batch, pauses elapsed-time accounting, and prevents stale in-flight chapter results from overwriting a stopped/restarted batch.
- For logged-in users, an entered API key is saved to that user after validation succeeds during upload, or can be managed from the personal page. If a saved user key later fails authentication, that user's key is cleared. Guest-entered keys keep the older shared credential behavior.
- EPUB image preservation works by replacing `<img>` tags with `[[BOOK_CONDENSER_IMAGE:...]]` markers before condensation and restoring referenced images during EPUB export. PDF image extraction is not implemented.
- Chapter progress intentionally uses states rather than fake fine-grained percentages. Total progress is derived from completed/running chapter counts.
- The condensation prompt requires the model to keep at least 20% of the original chapter length by passing a precomputed minimum word/character count.

## API Summary

- `GET /api/health`: health check.
- `POST /api/auth/register`: create a local email/password account and session.
- `POST /api/auth/login`: create a session for an existing local account.
- `POST /api/auth/logout`: clear the current session.
- `GET /api/auth/me`: return the current session user, if any.
- `GET /api/account/api-key`: return whether the current user has a saved API key.
- `PUT /api/account/api-key`: save or clear the current user's API key.
- `GET /api/me/jobs`: list the current user's personal book jobs.
- `GET /api/models`: available MiniMax models, region options, and whether an effective key is available.
- `POST /api/jobs`: upload and analyze a book.
- `GET /api/jobs/{job_id}`: job snapshot for polling.
- `GET /api/jobs/{job_id}/chapters/{chapter_id}`: original and condensed chapter content for preview.
- `POST /api/jobs/{job_id}/condense`: start a batch with mode `one`, `ten`, `all`, `failed`, or `selected`.
- `POST /api/jobs/{job_id}/stop`: request stop for the current batch.
- `POST /api/jobs/{job_id}/exports`: create an EPUB from all completed chapters or selected completed chapters.
- `GET /api/jobs/{job_id}/exports/{export_id}/download`: download a created export.
- `DELETE /api/jobs/{job_id}`: delete a logged-in user's own book job and local files.

## Run Locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 9121
```

Open <http://localhost:9121>.

If `MINIMAX_API_KEY` is not set, guests can enter a key on first use. Logged-in users can save their personal key from the personal page or provide one during upload.

## Run With Docker

Build locally:

```bash
docker compose up --build
```

Open <http://localhost:9121>.

## Pull On NAS

After the GitHub Action publishes the image, a NAS can pull and run it directly:

```bash
docker pull ghcr.io/redonkatonkk/book-condenser:latest
docker run -d \
  --name book-condenser \
  --restart unless-stopped \
  -p 9121:9121 \
  -v book-condenser-data:/data \
  ghcr.io/redonkatonkk/book-condenser:latest
```

Or use the pull compose file:

```bash
export BOOK_CONDENSER_IMAGE=ghcr.io/redonkatonkk/book-condenser:latest
docker compose -f docker-compose.pull.yml up -d
```

Open `http://<NAS-IP>:9121`.

## Configuration

- `MINIMAX_API_KEY`: Optional MiniMax API key. If omitted, the UI asks for a key and saves it after validation.
- `MINIMAX_REGION`: MiniMax region. Defaults to `cn`, using `https://api.minimaxi.com/v1/chat/completions`. Set `global` for `https://api.minimax.io/v1/chat/completions`.
- `MINIMAX_DEFAULT_MODEL`: Defaults to `MiniMax-M2.7`.
- `BOOK_CONDENSER_WORKERS`: Parallel chapter condensation workers. Defaults to `4`.
- `BOOK_CONDENSER_STORAGE`: Runtime storage path. Defaults to `storage` locally and `/data` in Docker.
- `BOOK_CONDENSER_MOCK_AI`: Set to `1` for tests or local dry runs without calling MiniMax.

Runtime data, uploaded books, job metadata, exports, local accounts, sessions, and saved API credentials live in the storage directory. They are intentionally excluded from git.

## Operational Notes

- Closing the browser page does not stop a running condensation task. The task continues as long as the server or Docker container keeps running. The same browser remembers the current job and restores progress when reopened.
- Full-book completion no longer opens preview automatically. The app leaves the user on the condensation page and shows a download button; individual completed chapters can be opened from the chapter table.
- Stopping a batch pauses elapsed condensation time. Requests already sent to MiniMax cannot be forcibly killed, but stale results will not overwrite a stopped or restarted batch.
- Each chapter prompt includes a precomputed minimum length: condensed text must not be shorter than about 20% of the original chapter length.
- EPUB images are preserved only for EPUB input. PDF image extraction is not currently implemented.

## GitHub Actions

The workflow in `.github/workflows/docker.yml` runs tests and builds a Docker image for:

- `linux/amd64`
- `linux/arm64`

On pushes to `main`, it publishes:

- `ghcr.io/redonkatonkk/book-condenser:latest`
- branch tags
- commit SHA tags

## Tests

```bash
pip install -r requirements-dev.txt
BOOK_CONDENSER_MOCK_AI=1 pytest -q
```

## Suggested Next Improvements

- Add provider-side rate-limit handling with adaptive cooldown and checkpoint resume for very large full-book runs.
- Add optional per-chapter quality checks for extremely short model outputs.
- Add PDF image extraction if image-heavy PDF books are important.
- Consider a small admin page for storage cleanup and credential reset.
