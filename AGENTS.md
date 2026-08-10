# Agent Guidelines

## Keeping the repo clean (IMPORTANT)

This project must stay a clean, commit-ready git repo at all times. Follow these rules:

1. **All scratch work goes in `temp/`** — test scripts, test fixtures (fake .zip files, chat .txt samples), logs, scratch code, downloaded test artifacts, and any throwaway files MUST be created under the repository-root `temp/` folder (already git-ignored, see `.gitignore`).
   - Example: `temp/test_chat.txt`, `temp/test_chat.zip`, `temp/test_import.json`, `temp/scratch_script.py`.
2. **Reuse the single `temp/` folder** — do NOT create new scratch directories elsewhere in the repo. When a task is done, remove any large or stale scratch files you created in `temp/`.
3. **Never commit runtime artifacts** — `data/` (audio, music, transcripts, reports, imports, app.db), `temp/`, `frontend/node_modules/`, `frontend/dist/`, `.env` are git-ignored. Do not `git add -f` them, ever.
4. **Always verify at the end of a task**:
   ```powershell
   git status --short
   ```
   If anything appears that is not an intentional source-code change, delete it or ignore it. The final `git status` should only show intended source edits.
5. **Test runs** — always use a throwaway/isolated database and directories under `temp/` (e.g. `temp/test_data/`, `DB_PATH=./temp/test.db`) so real user data in `data/` is never touched by tests.

## Project layout

- `backend/` — FastAPI app: `main.py` (routes), `database.py` (SQLAlchemy + SQLite), `downloader.py` (yt-dlp subprocess loop), `ai_processor.py` (faster-whisper + Gemini/Ollama classification), `whatsapp_parser.py`, `url_extractor.py`, `excel_service.py`, `organizer.py`, `log_streamer.py`, `websocket_manager.py`, `config.py`.
- `frontend/` — React + Vite + TypeScript app (`src/pages/` has ImportPage, DownloadsPage, LinksPage).
- `data/` — runtime data (audio, transcripts, reports, app.db). Git-ignored.
- `temp/` — scratch, test fixtures, extracted zips. Git-ignored.

## Conventions

- Backend: Python, SQLAlchemy ORM on SQLite. Do not add schema changes without migration support in `database.py`.
- Frontend: run `npm run build` and `npx oxlint` in `frontend/` to verify TypeScript and lint before finishing.

## Git: never commit or push without explicit permission (NON-NEGOTIABLE)

- **NEVER run `git commit`** unless the user explicitly tells you to commit.
- **NEVER run `git push`** — not after a commit, not to "sync", not on request that
  mentions committing only, not even if the user says "finish up". Only push if the
  user gives a direct, explicit instruction to push (e.g. "push it" / "git push").
- "Commit" and "push" are different actions. Permission to commit is NOT permission to push.
- The remote is `origin` (https://github.com/Babar-Meet/SCALETECH_TASK.git). Never
  force-push, never amend, never rebase shared history.
- Do not fetch/merge/pull/rebase on the user's behalf without asking — the repo may be
  deliberately kept behind upstream.
