# Notion Archive — Live Smoke (Phase 1 push)

Manual smoke test for the Notion push feature. **No live Notion runs in CI** — this is run by hand against a scratch subject page once the integration token exists.

## Prerequisites (operational — do once)
1. Create a Notion internal integration at https://www.notion.so/my-integrations → copy the token (`ntn_...` or `secret_...`).
2. Share the lesson tree with the integration: open the top page (`Class A Creative`) → `•••` → Connections → add the integration (sharing the root cascades to descendants). For the first run, use a **scratch** subject page you control.
3. Get the scratch subject page's ID (from its URL).

## Steps
1. In `.env` set:
   ```
   NOTION_ENABLED=true
   NOTION_API_KEY=ntn_...
   NOTION_SUBJECT_PAGES={"geometriya-g7-11|8":"<scratch-subject-page-id>"}
   ```
2. `uv run alembic upgrade head` (applies `0016` / `c9e3f1a07b62` if not already).
3. Upload a Geometriya-8 book via the web UI with **grade = "8"** (or, on an existing book, set the grade by SQL:
   `docker exec edu-postgres psql -U edu -d edu_homework -c "update books set grade='8' where id='<book_id>';"`).
4. Generate homework for one section (provider = claude).
5. On job done, open the scratch subject page in Notion. **Confirm:**
   - a lesson page titled `"{section_number} {section_title}"` was created,
   - a `Homework` sub-page under it,
   - the Homework page shows rendered content **plus** `homework.md` + `content.json` attachments.
6. **Re-run** generation for the SAME section. **Confirm:**
   - NO duplicate lesson/Homework page (find-or-create reused them),
   - the Homework content was NOT written twice (the `page_has_content` guard skipped the write).
7. **Negative test:** set `NOTION_SUBJECT_PAGES={}` and run a job → the job still completes `done` and downloads normally; the log shows `notion: no subject-page mapping ... skipping`. (Archive is non-fatal.)
8. Set `NOTION_ENABLED=false` again when finished.

## Watch-items flagged during code review (verify here)
- **Page-title format:** `create_page` sends `properties.title` as `[{"text":{"content":...}}]` (no explicit `"type":"text"`). The reference repo uses this exact form against this same tree, so it should work — but if page creation returns a **400**, add `"type": "text"` to the title dict in `app/services/notion/client.py` `create_page`.
- **File upload:** uses the 2-step `/v1/file_uploads` → `/send` flow with `Notion-Version: 2022-06-28`. If the `send` step 400s, confirm the installed `notion-client` version's expected upload flow.

## Acceptance
- All 5/6/7 confirmations above pass.
- `uv run python -m pytest tests/ -q` → full suite green.
