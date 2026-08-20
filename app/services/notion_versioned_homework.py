"""Immutable versioned homework publication to Notion (`Homework V{n}`).

A regenerated homework is published as a NEW sibling child page beside the
original — `Homework V2`, `Homework V3`, … under the same Lesson Topic page.
V1's `Homework` page is never renamed, cleared, rewritten or deleted, and this
module never routes through it.

**Why a marker instead of the title.** The legacy archive identifies a page by
its normalized title (`page_creator.find_or_create`). That is fine when every
page under `Generated Homeworks` is our own output, but it is not safe here: a
retry that adopts a page purely because it is called `Homework V2` would happily
overwrite a human's page, a different campaign's publication, or a Notion
duplicate. So every page this module writes carries a deterministic
machine-readable marker, and adoption requires an exact five-field marker match.
A same-title page whose marker is missing or different is a visible
`VersionPageCollision`, never a silent overwrite.

Writing the marker as the page's first block is a *convention* (it is created
with the page, before any content). Adoption deliberately does not depend on
that position: it scans ALL of the candidate's blocks for the first well-formed
marker. A strict index-0 check would turn our own page into a permanent
collision the moment a human types a line above the marker.

**Why a completion digest.** Crash recovery has to cover the whole page tree,
not just the root: a process that dies after `create_page` but before the leaves
are written leaves a correctly-marked but empty `Homework V2`. The root page
alone therefore cannot mean "published". A second, separately sentineled block
is appended only after every expected child/leaf has been populated in the same
call, and it carries a digest over both the marker and the exact rendered
payload. A retry that finds a matching digest and nothing conflicting makes no
call at all — no uploads, no page creation, no layout write, no deletion; a
retry that does not match deletes any conflicting stamp and then repairs the
tree beneath that one marked root. A matching digest found *alongside* a
conflicting one is a `VersionPageCollision` with zero writes: only the render
path may delete a stamp, because only it replaces the content that stamp
described.

This module is synchronous and does **not** retry: leases, backoff and version
allocation belong to the publisher loop, not here. Every client exception
propagates unchanged. At runtime it touches no DB, reads no `settings` and
constructs no client — the caller injects the client. (Importing it does pull in
`notion_archive`, which transitively imports `app.config` and `app.db`; the
claim above is about what this module *does*, not about import side effects.)
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence
from uuid import UUID

from app.services.notion import blocks as notion_blocks
from app.services.notion.client import NotionClientWrapper
from app.services.notion.page_creator import _normalize
from app.services.notion_archive import layout_groups, write_homework_layout

log = logging.getLogger("notion.versioned")

# Sentinels that let the decoders find our blocks without guessing at prose.
# Deliberately NOT prefixes of one another: a completion block must never be
# mistaken for a revision marker (that would make an empty page look adopted).
MARKER_SENTINEL = "hcga-homework-revision-marker/v1"
COMPLETION_SENTINEL = "hcga-homework-revision-complete/v1"

_UUID_FIELDS = ("toc_entry_id", "revision_job_id", "campaign_id")


@dataclass(frozen=True)
class HomeworkRevisionMarker:
    """Identity of one published revision. Frozen + value-compared: adoption is
    `decoded == expected`, which is exactly "all five fields match"."""

    toc_entry_id: UUID
    output_language: str
    revision_job_id: UUID
    campaign_id: UUID
    publication_version: int


class VersionPageCollision(RuntimeError):
    """A page that looks like ours but is not provably ours.

    Raised instead of touching it. The operator resolves it; the publisher must
    never clear, overwrite or adopt the page that triggered this."""


def version_page_title(publication_version: int) -> str:
    """The sibling page title for a version. V1 is the legacy `Homework` page
    and is not produced here."""
    return f"Homework V{publication_version}"


# --- marker encode / decode --------------------------------------------------


def encode_revision_marker(marker: HomeworkRevisionMarker) -> str:
    """Canonical, byte-stable text for a marker.

    `sort_keys` + fixed separators mean the same marker always encodes to the
    same bytes, on any Python version and regardless of field order — the
    completion digest and the adoption comparison both depend on that."""
    payload = {
        "toc_entry_id": str(marker.toc_entry_id),
        "output_language": marker.output_language,
        "revision_job_id": str(marker.revision_job_id),
        "campaign_id": str(marker.campaign_id),
        "publication_version": int(marker.publication_version),
    }
    return f"{MARKER_SENTINEL} " + json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def _block_text(block: Mapping[str, Any]) -> str:
    """The full text of a block, tolerating both shapes Notion hands back.

    A written block carries `text.content`; a read-back one carries
    `plain_text`. `blocks.make_paragraph` also chunks at 2000 chars, so ALL
    segments must be concatenated — reading only the first would silently
    truncate a long marker into garbage that then decodes to `None`."""
    btype = block.get("type")
    payload = block.get(btype) if isinstance(btype, str) else None
    if not isinstance(payload, Mapping):
        return ""
    parts: list[str] = []
    for seg in payload.get("rich_text") or ():
        if not isinstance(seg, Mapping):
            continue
        text = seg.get("plain_text")
        if not isinstance(text, str):
            inner = seg.get("text")
            text = inner.get("content") if isinstance(inner, Mapping) else None
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _sentineled(blocks: Iterable[Mapping[str, Any]], sentinel: str) -> Iterable[tuple[Optional[str], str]]:
    """`(block_id, payload)` for every block whose text starts with `sentinel`."""
    for block in blocks or ():
        if not isinstance(block, Mapping):
            continue
        text = _block_text(block).strip()
        if text.startswith(sentinel + " "):
            yield block.get("id"), text[len(sentinel) + 1:].strip()


def decode_revision_marker(
    blocks: Sequence[Mapping[str, Any]],
) -> Optional[HomeworkRevisionMarker]:
    """The first well-formed marker among `blocks`, else `None`.

    Never raises: this decides whether a page is safe to touch, and a hostile /
    truncated / hand-edited block must degrade to "not ours" (which the caller
    turns into a collision), not into an exception that reads like an outage."""
    for _, payload in _sentineled(blocks, MARKER_SENTINEL):
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            continue
        if not isinstance(data, Mapping):
            continue
        version = data.get("publication_version")
        language = data.get("output_language")
        # `isinstance(True, int)` is True — a bool version would be a corrupt
        # marker that silently compares equal to 1.
        if isinstance(version, bool) or not isinstance(version, int):
            continue
        if not isinstance(language, str):
            continue
        try:
            ids = {field: UUID(str(data[field])) for field in _UUID_FIELDS}
        except (KeyError, ValueError, AttributeError, TypeError):
            continue
        return HomeworkRevisionMarker(
            toc_entry_id=ids["toc_entry_id"],
            output_language=language,
            revision_job_id=ids["revision_job_id"],
            campaign_id=ids["campaign_id"],
            publication_version=version,
        )
    return None


# --- completion digest -------------------------------------------------------


def completion_digest(marker: HomeworkRevisionMarker, phase_md: Mapping[str, str]) -> str:
    """sha256 binding the marker AND the exact payload that will be rendered.

    The `(phase, markdown)` pairs come from `layout_groups`, i.e. the order the
    renderer actually walks — not `phase_md`'s insertion order — so re-running
    with an equivalent mapping built differently is correctly a no-op, while a
    single changed character anywhere invalidates the stamp.

    Known limitation: the digest binds the marker and the `(phase, markdown)`
    pairs only — NOT the rendering itself (`_HOMEWORK_LAYOUT` titles/structure,
    `PHASE_TITLES`, or the markdown→block converters). Changing any of those
    does not invalidate existing stamps, so already-published pages keep the old
    rendering until they are force-republished under a different payload."""
    rendered = [[phase, md] for _, present in layout_groups(phase_md) for phase, md in present]
    canonical = json.dumps(
        [encode_revision_marker(marker), rendered],
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def encode_completion_marker(digest: str) -> str:
    return f"{COMPLETION_SENTINEL} {digest}"


def decode_completion_digest(blocks: Sequence[Mapping[str, Any]]) -> Optional[str]:
    """The digest carried by the first completion block, else `None`."""
    for _, payload in _sentineled(blocks, COMPLETION_SENTINEL):
        return payload
    return None


def _completion_stamps(
    blocks: Sequence[Mapping[str, Any]], digest: str
) -> tuple[bool, list[tuple[Optional[str], str]]]:
    """`(matches_digest, conflicting)` over every completion block present.

    `conflicting` is `(block_id, digest)` for every completion block carrying a
    DIFFERENT digest, id-less ones included: an unaddressable conflict is the
    one we can least afford to drop silently, since it is also the one we could
    not delete even if we wanted to."""
    matches = False
    conflicting: list[tuple[Optional[str], str]] = []
    for block_id, payload in _sentineled(blocks, COMPLETION_SENTINEL):
        if payload == digest:
            matches = True
        else:
            conflicting.append((block_id, payload))
    return matches, conflicting


# --- the writer --------------------------------------------------------------


def _resolve_root(
    client: NotionClientWrapper,
    lesson_page_id: str,
    marker: HomeworkRevisionMarker,
    stored_page_id: Optional[str],
) -> tuple[str, list[dict]]:
    """Find, adopt or create the `Homework V{n}` root. Returns
    `(page_id, its blocks)` — empty blocks for a page we just created.

    Never returns a page whose marker is not exactly `marker`."""
    title = version_page_title(marker.publication_version)

    if stored_page_id:
        # The stored id is authoritative ONLY after revalidation: the DB can
        # point at a page a human has since deleted-and-recreated. A mismatch is
        # terminal — falling through to enumeration or creation here would let a
        # stale pointer silently mint a second page for the same version.
        existing = client.get_block_children(stored_page_id)
        if decode_revision_marker(existing) != marker:
            raise VersionPageCollision(
                f"stored page {stored_page_id} does not carry the expected "
                f"{title} marker (toc={marker.toc_entry_id} "
                f"lang={marker.output_language} v={marker.publication_version})"
            )
        return stored_page_id, existing

    # Title enumeration is a SEARCH, not an identity: `_normalize` so Notion's
    # own " (2)" dedup suffix cannot present a duplicate as a different page.
    wanted = _normalize(title)
    candidates = [p for p in client.get_child_pages(lesson_page_id)
                  if _normalize(p.get("title", "")) == wanted]

    if len(candidates) > 1:
        raise VersionPageCollision(
            f"{len(candidates)} pages titled {title!r} under lesson {lesson_page_id} "
            "— ambiguous, refusing to guess"
        )

    if not candidates:
        page = client.create_page(
            lesson_page_id, title,
            children=[notion_blocks.make_paragraph(encode_revision_marker(marker))],
        )
        log.info("notion: created %s %s under lesson %s", title, page["id"], lesson_page_id)
        return page["id"], []

    candidate_id = candidates[0]["id"]
    existing = client.get_block_children(candidate_id)
    found = decode_revision_marker(existing)
    if found != marker:
        raise VersionPageCollision(
            f"page {candidate_id} titled {title!r} under lesson {lesson_page_id} "
            f"carries {'a different' if found else 'no'} revision marker "
            "— not adopting, not clearing"
        )
    log.info("notion: adopting existing %s %s (marker match)", title, candidate_id)
    return candidate_id, existing


def write_or_adopt_versioned_homework(
    *,
    client: NotionClientWrapper,
    lesson_page_id: str,
    phase_md: Mapping[str, str],
    marker: HomeworkRevisionMarker,
    stored_page_id: Optional[str],
) -> str:
    """Publish `phase_md` as the immutable `Homework V{n}` sibling page and
    return its page id.

    Rejects a caller bug before touching Notion: `publication_version < 2`, or a
    `phase_md` that renders nothing, raises `ValueError` — either would burn a
    reserved version (spec section 9) on a page nobody wants.

    Resolution order: stored page id (revalidated) → exact-title candidate with
    a matching marker → create. Then, beneath that one proven root:

    * matching completion digest and no conflicting stamp → return having made
      **zero** writes — no uploads, no `create_page`, no layout write, no
      completion append, and no `delete_block`;
    * matching completion digest ALONGSIDE a conflicting one →
      `VersionPageCollision`, still zero writes. Both stamps are left exactly as
      they are. A publisher stamps only after it has rendered and deletes a
      conflicting stamp before it renders, so two coexisting stamps mean the
      other publisher read this page before our stamp landed and then rendered
      its payload over ours: our digest no longer proves the page holds our
      bytes. Deleting the other stamp and returning would report success for a
      payload that may not be on the page, and which publisher got blessed
      would depend only on which one retried first;
    * no matching digest → delete every conflicting stamp FIRST, then render the
      V1 grouped layout with replace semantics, and only then stamp completion.
      Deleting is safe here precisely because this path replaces the content it
      invalidates; a crash between the delete and the stamp leaves a page that
      claims nothing, which is the recoverable state. A conflicting stamp with
      no block id cannot be deleted, so it is a `VersionPageCollision` too
      rather than a page left carrying a foreign completeness claim over our
      content.

    The root page itself is never cleared — that would delete the marker and
    turn our own page into a collision on the next retry. Idempotent across
    timeouts and crashes; raises `VersionPageCollision` rather than touching a
    page it cannot prove is ours."""
    # --- caller-contract guards: both fire BEFORE any remote call, so a bad
    # payload creates no page and burns no version. `ValueError`, not
    # `VersionPageCollision` — these are programming errors in the publisher,
    # not a page out there that we must refuse to touch.
    if marker.publication_version < 2:
        # Spec section 9: the first allocated database version is 2, because
        # logical V1 has no version row, and V1's `Homework` page is not
        # renamed. V1 is structurally unreachable from here anyway — no
        # `Homework V{n}` normalizes to `homework` — so this guard is not about
        # protecting V1; it is about not minting a stray `Homework V1`/`V0`
        # sibling and permanently burning a reserved version on it.
        raise ValueError(
            f"publication_version must be >= 2, got {marker.publication_version} "
            f"(toc={marker.toc_entry_id} lang={marker.output_language})"
        )
    if not layout_groups(phase_md):
        # Nothing in `phase_md` maps onto `_HOMEWORK_LAYOUT`, so the page would
        # hold the two machine markers and no content — yet be stamped complete,
        # making every retry a permanent no-op on a version that "is never
        # reused" (spec section 9).
        raise ValueError(
            "refusing to publish a homework revision that renders no content: "
            f"phase_md has no phases the layout renders (toc={marker.toc_entry_id} "
            f"lang={marker.output_language} v={marker.publication_version})"
        )

    digest = completion_digest(marker, phase_md)
    root_id, existing = _resolve_root(client, lesson_page_id, marker, stored_page_id)

    complete, conflicting = _completion_stamps(existing, digest)

    if complete:
        if conflicting:
            # Zero writes, deliberately: see the docstring. Our digest proves an
            # earlier render, not the bytes currently on the page.
            raise VersionPageCollision(
                f"{version_page_title(marker.publication_version)} {root_id} carries our "
                f"completion digest {digest[:12]} alongside "
                + ", ".join(sorted(payload[:12] for _, payload in conflicting))
                + " — the page content is not provably ours, refusing to touch it"
            )
        log.info("notion: %s %s already complete (digest %s) — nothing rendered",
                 version_page_title(marker.publication_version), root_id, digest[:12])
        return root_id

    if any(block_id is None for block_id, _ in conflicting):
        raise VersionPageCollision(
            f"{version_page_title(marker.publication_version)} {root_id} carries a "
            "conflicting completion stamp with no block id — it cannot be deleted, "
            "so rebuilding would leave a foreign completeness claim over our content"
        )
    for block_id, _ in conflicting:
        # Delete BEFORE rendering: a crash in between must leave a page that
        # claims nothing rather than one claiming a completeness it has not
        # earned. Safe here only because this path replaces the content that
        # stamp described.
        client.delete_block(block_id)

    # `replace=True`: a leaf left half-written by a crashed attempt is cleared
    # and rewritten. Only leaves — `write_homework_layout` never clears its
    # `parent_id`, which is why the root's marker survives repair.
    write_homework_layout(client=client, parent_id=root_id, phase_md=phase_md, replace=True)

    client.append_block_children(root_id, [notion_blocks.make_paragraph(encode_completion_marker(digest))])
    log.info("notion: published %s %s (digest %s)",
             version_page_title(marker.publication_version), root_id, digest[:12])
    return root_id
