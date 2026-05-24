# Book Condenser

Book Condenser is a web app for compressing EPUB, PDF, and TXT books chapter by chapter with MiniMax. It analyzes the book first, lets you choose how many chapters to condense, tracks progress, retries failed chapters, preserves EPUB images where possible, and exports completed chapters as EPUB.

The app is designed to run in Docker on port `9121`.

## Features

- Upload EPUB, PDF, or TXT books.
- Analyze chapter structure and completeness before condensation starts.
- Choose to condense 1 chapter, 10 chapters, selected chapters, failed chapters, or the whole book.
- Use MiniMax domestic (`minimax.cn / minimaxi.com`) or global (`minimax.io`) API regions.
- Store a valid MiniMax API key on the server after the first successful validation.
- Show total progress, elapsed condensation time, and ETA based on completed chapter throughput.
- Stop the current condensation batch and continue later.
- Retry one failed chapter or all failed chapters.
- Export all completed chapters or selected completed chapters as EPUB.
- Preserve EPUB images by converting them to stable placeholders during condensation and restoring them during EPUB export.
- Build multi-architecture Docker images for `linux/amd64` and `linux/arm64` through GitHub Actions.

## Run Locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 9121
```

Open <http://localhost:9121>.

If `MINIMAX_API_KEY` is not set, the page asks for a key on first use. After the key passes validation, it is saved under the app storage directory and reused automatically.

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

Runtime data, uploaded books, exports, and saved API credentials live in the storage directory. They are intentionally excluded from git.

## Notes

- Closing the browser page does not stop a running condensation task. The task continues as long as the server or Docker container keeps running. The same browser remembers the current job and restores progress when reopened.
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
