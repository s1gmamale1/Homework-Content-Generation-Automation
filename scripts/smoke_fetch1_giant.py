"""fetch-1 acceptance smoke — prove a >50MB book ingests + reads bounded.

No LLM, no DB, no money. Builds a synthetic >50MB PDF (the largest local book
appended to itself), then asserts:
  1. the shipped default ingest cap ACCEPTS it where the old 50MB cap rejected;
  2. every bounded-read path handles a >50MB file without OOM and returns
     bounded output (the actual safety surface for raising the cap) — TOC text
     excerpt, whole-book text budget, lesson page window, TOC vision window.

Usage:
  PYTHONPATH=. DATABASE_URL=postgresql+asyncpg://x \
    uv run python scripts/smoke_fetch1_giant.py [source.pdf]

With no argument it discovers the largest var/books/*/source.pdf to double.
"""
import resource
import sys
import time
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from app.config import Settings, settings
from app.services import agent

OUT = Path("var/_smoke_fetch1_giant.pdf")


def _rss_mb() -> int:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r // (1024 * 1024) if r > 10**7 else r // 1024  # macOS bytes / Linux KB


def _largest_book() -> Path:
    cands = sorted(Path("var/books").glob("*/source.pdf"),
                   key=lambda p: p.stat().st_size, reverse=True)
    if not cands:
        sys.exit("no var/books/*/source.pdf found — pass a source PDF path")
    return cands[0]


def build_giant(src: Path) -> int:
    w = PdfWriter()
    while True:
        w.append(PdfReader(str(src)))
        # estimate: double until comfortably over 50MB
        if len(w.pages) >= 2 * len(PdfReader(str(src)).pages) and src.stat().st_size * 2 > 50 * 1048576:
            break
        if len(w.pages) > 4000:
            break
    with open(OUT, "wb") as f:
        w.write(f)
    return OUT.stat().st_size


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else _largest_book()
    assert src.exists(), f"source book missing: {src}"
    print(f"source: {src}  ({src.stat().st_size / 1048576:.1f} MB)")

    size = build_giant(src)
    size_mb = size / 1048576
    print(f"synthetic giant: {size_mb:.1f} MB  ({OUT})")
    if size <= 50 * 1048576:
        sys.exit(f"source too small to build a >50MB giant by doubling ({size_mb:.1f}MB)")

    # (1) ingest gate against the SHIPPED DEFAULT (operator .env may pin lower)
    new_cap = Settings.model_fields["max_file_mb"].default
    assert size <= new_cap * 1048576, f"giant {size_mb:.1f}MB exceeds the {new_cap}MB default"
    print(f"[1] ingest: {size_mb:.1f}MB  >  old 50MB (rejected)  and  <= new {new_cap}MB default (ACCEPTED)")
    if settings.max_file_mb < size_mb:
        print(f"    NOTE: runtime cap is .env-pinned MAX_FILE_MB={settings.max_file_mb} — "
              f"operator must raise it to ingest this book on the live head.")

    # (2) bounded reads on the >50MB file — none may OOM or read the whole thing
    n_pages = agent.pdf_page_count(OUT)
    print(f"[2a] pdf_page_count -> {n_pages} pages")

    t = time.time()
    text = agent.read_whole_book_text(OUT)
    assert len(text) <= settings.extract_max_text_chars + 70_000, \
        f"whole-book text {len(text)} not bounded near {settings.extract_max_text_chars}"
    print(f"[2b] read_whole_book_text -> {len(text)} chars (bounded ~{settings.extract_max_text_chars}, "
          f"{time.time() - t:.1f}s)")

    win = agent._subset_pdf(OUT, 2, 4, margin=settings.extract_window_pages,
                            max_pages=settings.extract_window_max_pages)
    assert win is not None and Path(win).stat().st_size < 25 * 1048576, "lesson window not bounded"
    print(f"[2c] _subset_pdf(2..4) -> {Path(win).stat().st_size / 1048576:.1f}MB window (bounded)")
    Path(win).unlink(missing_ok=True)

    toc = agent._toc_source_pdf(OUT, settings.extract_toc_front_pages, settings.extract_toc_back_pages)
    assert toc is not None and Path(toc).stat().st_size < 20 * 1048576, "TOC window exceeds 20MB Vertex limit"
    print(f"[2d] _toc_source_pdf -> {Path(toc).stat().st_size / 1048576:.1f}MB window (<20MB Vertex inline)")
    Path(toc).unlink(missing_ok=True)

    print(f"\npeak RSS: {_rss_mb()} MB  — no OOM on a {size_mb:.1f}MB book")
    print("PASS: >50MB book ingests under the new cap and every read stays bounded.")
    OUT.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
