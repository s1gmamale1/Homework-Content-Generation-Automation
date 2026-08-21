"""The durable regeneration publisher: one claim, one immutable version page.

No database, no network, no credentials, no model. The DB layer is faked at the
repository boundary and the campaign rollup at the service boundary; the real
SQL those fakes stand in for is proven separately against a real Postgres in
`tests/integration/test_regeneration_publication_claims.py`, so nothing here is
asserting on a fake of the thing under test.

Notion is NOT faked at the function boundary: the real `find_or_create` and the
real `write_or_adopt_versioned_homework` run against `FakeNotion`, the
in-memory Notion already used by `test_notion_versioned_homework.py`. Imported
rather than copied on purpose — a second, drifting model of Notion's behaviour
would let a marker/adoption regression pass here while failing there.

What the file is actually holding down:

* **the publisher never touches V1.** `toc_entries.notion_homework_page_id`,
  `notion_archived_job_id`, `homework_jobs.notion_archived_at` and
  `notion_skip_reason` are legacy-archive authority; every one of them is a
  tripwire that fails the test if written.
* **no Notion call ever runs on the event loop.** Every fake client call records
  its thread; the assertion is that none of them is the main one.
* **no model is ever called.** `agent`'s spawn surface is a tripwire too — a
  failed delivery is re-DELIVERED, never re-generated.
* **the abandon-intent contract.** A successful remote write lands `published`
  even under a cancellation; a failed one under the same intent lands terminal
  `abandoned` with the reserved version preserved.
* **a version page's identity is its LANGUAGE's subject tree.** The shared
  `toc_entries.notion_lesson_page_id` names whichever Lesson Topic was archived
  first and carries no language of its own, so it may route a publication only
  once it is proven to sit under the target language's own container.
"""
from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Callable, Optional

import pytest

import app.services.regeneration_publisher as pub
from app.repositories.regeneration_targets import (
    ClaimedRegenerationTarget,
    StalePublicationClaim,
)
from app.services.flows import flow_for
from app.services.notion_versioned_homework import (
    VersionPageCollision,
    decode_revision_marker,
    version_page_title,
)
from tests.services.test_notion_versioned_homework import FakeNotion

_SUBJECT = "math-algebra"
_CANONICAL = ("extract", *flow_for(_SUBJECT))
_SUBJECT_PAGE = "subject-page-uz-math-5"
_SUBJECT_PAGE_RU = "subject-page-ru-math-5"
_CONTAINER = pub.notion_archive.CONTAINER_TITLE
#: Shaped like a real Notion integration token and never used against Notion —
#: `FakeNotion` is the client here. Only its SHAPE matters: the readiness gate
#: rejects anything the real client's constructor would reject.
_USABLE_KEY = "secret_pytest_not_a_real_notion_token"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _ThreadRecordingCalls(list):
    """Every `FakeNotion` method appends to `self.calls`, so recording the
    calling thread here covers the WHOLE client surface at once — including any
    method a future writer starts using."""

    def __init__(self) -> None:
        super().__init__()
        self.threads: list[threading.Thread] = []

    def append(self, item) -> None:
        self.threads.append(threading.current_thread())
        super().append(item)


class _Session:
    """The publisher uses a session only as an opaque handle it hands to
    repository functions, plus `commit()`. That is all this models."""

    def __init__(self, harness: "_Harness") -> None:
        self._harness = harness

    async def __aenter__(self) -> "_Session":
        self._harness.sessions_opened += 1
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False

    async def commit(self) -> None:
        self._harness.commits += 1

    async def rollback(self) -> None:
        self._harness.rollbacks += 1


@dataclass
class _Harness:
    """One lesson, one approved campaign, one claimable target, one fake Notion."""

    target_id: uuid.UUID = field(default_factory=uuid.uuid4)
    campaign_id: uuid.UUID = field(default_factory=uuid.uuid4)
    toc_entry_id: uuid.UUID = field(default_factory=uuid.uuid4)
    job_id: uuid.UUID = field(default_factory=uuid.uuid4)
    book_id: uuid.UUID = field(default_factory=uuid.uuid4)
    output_language: str = "uz"

    notion: FakeNotion = field(default_factory=FakeNotion)
    subject_page_id: Optional[str] = _SUBJECT_PAGE
    reserved_version: int = 2
    snapshot_complete: bool = True

    # test seams, all installed once by `_install`
    write_hook: Optional[Callable] = None       # wraps the real versioned writer
    parent_hook: Optional[Callable] = None      # wraps the real find_or_create
    reserve_hook: Optional[Callable] = None     # runs after a real reservation
    #: raised by the reservation before it writes anything — the shape of a
    #: refused version, as opposed to `reserve_hook`'s post-write inspection.
    reserve_error: Optional[BaseException] = None

    claims: list = field(default_factory=list)      # queue the sweep pops
    issued: list = field(default_factory=list)      # every claim ever handed out
    status_writes: list[dict] = field(default_factory=list)
    lesson_stamps: list[tuple] = field(default_factory=list)
    rollups: list[uuid.UUID] = field(default_factory=list)
    reconciles: int = 0
    sessions_opened: int = 0
    commits: int = 0
    rollbacks: int = 0
    client_threads: list[threading.Thread] = field(default_factory=list)
    subject_page_call: tuple = ()

    def __post_init__(self) -> None:
        self.notion.calls = _ThreadRecordingCalls()
        self.notion.titles[_SUBJECT_PAGE] = "Subject"
        self.notion.blocks[_SUBJECT_PAGE] = []
        self.target = SimpleNamespace(
            id=self.target_id,
            campaign_id=self.campaign_id,
            toc_entry_id=self.toc_entry_id,
            output_language=self.output_language,
            status="publishing",
            publication_claim_token=None,
            publication_claimed_at=_now(),
            publication_attempts=1,
            publication_version=None,
            publication_released_at=_now(),
            publication_next_attempt_at=None,
            publication_last_error=None,
            notion_page_id=None,
            terminal_at=None,
            terminal_reason=None,
            abandon_requested_at=None,
            abandon_requested_reason=None,
        )
        self.campaign = SimpleNamespace(
            id=self.campaign_id, status="approved", approved_at=_now(),
            cancel_requested_at=None, rejected_at=None,
            # The number the operator approved. Coherent with
            # `reserved_version` on purpose: the declared version IS what the
            # reservation hands back, and it is what a version refusal names.
            publication_version=self.reserved_version,
        )
        self.job = SimpleNamespace(
            id=self.job_id, book_id=self.book_id, subject=_SUBJECT,
            status="done", output_language=self.output_language,
            revision_of_job_id=uuid.uuid4(),
            regeneration_target_id=self.target_id,
            # V1/legacy columns: tripwired in `_install`, never written here
            notion_archived_at=None, notion_skip_reason=None,
        )
        self.book = SimpleNamespace(
            id=self.book_id, grade="5", subject=_SUBJECT,
            original_filename="algebra5.pdf",
        )
        self.section = SimpleNamespace(
            id=self.toc_entry_id, section_number="1", section_title="Lesson one",
            chapter_title="", page_start=7, notion_lesson_page_id=None,
            notion_homework_page_id=None, notion_archived_job_id=None,
        )
        self.phases = [
            SimpleNamespace(phase_name=name, phase_order=order, status="done",
                            output_md=f"# {name}\n\nbody", content_json=None)
            for order, name in enumerate(_CANONICAL)
        ]

    # ── the claim the sweep hands the publisher ──────────────────────────
    def claim(self, **overrides) -> ClaimedRegenerationTarget:
        token = overrides.pop("claim_token", uuid.uuid4())
        base = dict(
            target_id=self.target_id, campaign_id=self.campaign_id,
            toc_entry_id=self.toc_entry_id,
            output_language=self.output_language, claim_token=token,
            publication_attempts=1, publication_version=None,
            notion_page_id=None, abandon_requested_at=None,
        )
        base.update(overrides)
        claim = ClaimedRegenerationTarget(**base)
        # mirror what the real claim already did to the row
        self.target.status = "publishing"
        self.target.publication_claim_token = claim.claim_token
        self.target.publication_attempts = claim.publication_attempts
        self.claims.append(claim)
        self.issued.append(claim)
        return claim

    def publisher(self, **kwargs) -> "pub.RegenerationPublisher":
        params = dict(
            session_factory=lambda: _Session(self),
            campaign_service=SimpleNamespace(roll_up=self._roll_up),
            client_factory=self._client,
            lease_seconds=300, max_attempts=3,
            backoff_base_seconds=60, backoff_max_seconds=3600,
            interval_seconds=0.01,
        )
        params.update(kwargs)
        return pub.RegenerationPublisher(**params)

    def _client(self):
        self.client_threads.append(threading.current_thread())
        return self.notion

    async def _roll_up(self, campaign_id):
        self.rollups.append(campaign_id)

    # ── assertions helpers ───────────────────────────────────────────────
    def write(self, name: str) -> dict:
        for entry in self.status_writes:
            if entry["new_status"] == name:
                return entry
        raise AssertionError(
            f"no {name!r} write; got {[w['new_status'] for w in self.status_writes]}"
        )

    def lesson_page_id(self) -> str:
        container = self.child_ids(_SUBJECT_PAGE)[0]
        return self.child_ids(container)[0]

    def child_ids(self, page_id: str) -> list[str]:
        return [b["id"] for b in self.notion.blocks.get(page_id, [])
                if b.get("type") == "child_page"]

    def off_loop(self) -> bool:
        main = threading.main_thread()
        return (
            bool(self.notion.calls.threads)
            and all(t is not main for t in self.notion.calls.threads)
            and bool(self.client_threads)
            and all(t is not main for t in self.client_threads)
        )


@pytest.fixture
def h(monkeypatch) -> _Harness:
    harness = _Harness()
    _install(monkeypatch, harness)
    return harness


def _install(monkeypatch, h: _Harness) -> None:
    async def _reconcile(session):
        h.reconciles += 1
        return 0

    async def _claim_next(session, *, now, lease_seconds):
        return h.claims.pop(0) if h.claims else None

    async def _lock_campaign(session, *, campaign_id, skip_locked=False):
        return h.campaign if campaign_id == h.campaign_id else None

    async def _get_target(session, target_id):
        return h.target if target_id == h.target_id else None

    async def _reserve(session, *, target_id, claim_token):
        if h.target.publication_claim_token != claim_token:
            raise StalePublicationClaim("claim token is no longer current")
        if h.reserve_error is not None:
            raise h.reserve_error
        if h.target.publication_version is None:
            h.target.publication_version = h.reserved_version
        version = h.target.publication_version
        if h.reserve_hook is not None:
            h.reserve_hook(version)
        return version

    async def _set_status(session, *, target_id, new_status, expected_statuses,
                          expected_claim_token=None, **values):
        if h.target.status not in expected_statuses:
            return False
        if (expected_claim_token is not None
                and h.target.publication_claim_token != expected_claim_token):
            return False
        h.status_writes.append(
            {"new_status": new_status, "expected_statuses": list(expected_statuses),
             "expected_claim_token": expected_claim_token, **values})
        h.target.status = new_status
        for column in ("terminal_at", "terminal_reason", "notion_page_id",
                       "publication_version", "publication_next_attempt_at",
                       "publication_last_error", "publication_released_at",
                       "abandon_requested_at", "abandon_requested_reason"):
            if values.get(column) is not None:
                setattr(h.target, column, values[column])
        if values.get("clear_publication_claim"):
            h.target.publication_claim_token = None
        if values.get("clear_publication_backoff"):
            h.target.publication_next_attempt_at = None
            h.target.publication_last_error = None
        if values.get("clear_publication_next_attempt"):
            h.target.publication_next_attempt_at = None
        return True

    async def _revision_job(session, *, target_id):
        return h.job

    async def _phases(session, job_id):
        return h.phases if h.snapshot_complete else h.phases[:-1]

    async def _book(session, book_id):
        return h.book

    async def _toc_get(session, toc_entry_id):
        return h.section

    async def _titles(session, *, subject, grade):
        return [(h.section.section_number, h.section.section_title, "",
                 h.section.page_start, h.section.id)]

    async def _stamp(session, toc_entry_id, page_id):
        h.lesson_stamps.append((toc_entry_id, page_id))
        h.section.notion_lesson_page_id = page_id

    def _subject_page(mapping, subject, grade, hint="", language="uz"):
        h.subject_page_call = (subject, grade, hint, language)
        return h.subject_page_id

    real_writer = pub.write_or_adopt_versioned_homework
    real_find_or_create = pub.notion_archive.find_or_create

    def _writer(**kwargs):
        if h.write_hook is not None:
            return h.write_hook(real_writer, **kwargs)
        return real_writer(**kwargs)

    def _find_or_create(client, parent_id, title):
        if h.parent_hook is not None:
            return h.parent_hook(real_find_or_create, client, parent_id, title)
        return real_find_or_create(client, parent_id, title)

    monkeypatch.setattr(pub.regeneration_job_state,
                        "reconcile_terminal_revision_jobs", _reconcile)
    monkeypatch.setattr(pub.targets_repo, "claim_next_publication", _claim_next)
    monkeypatch.setattr(pub.targets_repo, "lock_owning_campaign", _lock_campaign)
    monkeypatch.setattr(pub.targets_repo, "get_target_for_update", _get_target)
    monkeypatch.setattr(pub.targets_repo, "reserve_publication_version", _reserve)
    monkeypatch.setattr(pub.targets_repo, "set_target_status", _set_status)
    monkeypatch.setattr(pub.targets_repo, "revision_job_for_target", _revision_job)
    monkeypatch.setattr(pub.phase_repo, "list_for_job", _phases)
    monkeypatch.setattr(pub.books_repo, "get", _book)
    monkeypatch.setattr(pub.toc_repo, "get", _toc_get)
    monkeypatch.setattr(pub.toc_repo, "titles_for_subject_grade", _titles)
    monkeypatch.setattr(pub.toc_repo, "set_notion_lesson_page_id", _stamp)
    monkeypatch.setattr(pub.notion_archive, "_resolve_subject_page_id", _subject_page)
    monkeypatch.setattr(pub.notion_archive, "find_or_create", _find_or_create)
    monkeypatch.setattr(pub, "write_or_adopt_versioned_homework", _writer)

    # ── tripwires: V1/legacy authority and the model, both out of bounds ──
    def _forbidden(name):
        async def _boom(*a, **kw):
            raise AssertionError(f"the versioned publisher must never call {name}")
        return _boom

    from app.repositories import jobs as jobs_repo
    from app.services import agent as agent_mod

    monkeypatch.setattr(pub.toc_repo, "set_notion_homework_page_id",
                        _forbidden("toc_repo.set_notion_homework_page_id"))
    monkeypatch.setattr(pub.toc_repo, "set_notion_archived_job",
                        _forbidden("toc_repo.set_notion_archived_job"))
    monkeypatch.setattr(jobs_repo, "set_notion_archived",
                        _forbidden("jobs_repo.set_notion_archived"))
    monkeypatch.setattr(jobs_repo, "set_notion_skip_reason",
                        _forbidden("jobs_repo.set_notion_skip_reason"))
    monkeypatch.setattr(agent_mod, "run_phase_prompt",
                        _forbidden("agent.run_phase_prompt"))
    monkeypatch.setattr(agent_mod, "run_phase", _forbidden("agent.run_phase"))
    monkeypatch.setattr(agent_mod, "_spawn", _forbidden("agent._spawn"))

    # ── the head this harness models is CONFIGURED for Notion ─────────────
    # `run_once` refuses to claim anything without a usable destination, so
    # every delivery test below needs the prerequisite the operator would have
    # set. Stated here rather than per-test so the refusal tests are the only
    # ones that talk about it — they take it away again.
    monkeypatch.setattr(pub.settings, "notion_enabled", True)
    monkeypatch.setattr(pub.settings, "notion_api_key", _USABLE_KEY)


def _speak_russian(h: _Harness) -> None:
    """Turn the harness into a `ru` lineage of the SAME lesson: the target, its
    revision job and the destination all move to Russian, and the ru subject
    page exists but is empty."""
    h.output_language = "ru"
    h.target.output_language = "ru"
    h.job.output_language = "ru"
    h.subject_page_id = _SUBJECT_PAGE_RU
    h.notion.titles[_SUBJECT_PAGE_RU] = "Subject RU"
    h.notion.blocks[_SUBJECT_PAGE_RU] = []


def _raise(exc: BaseException):
    def _hook(_real, **_kwargs):
        raise exc
    return _hook


# ═════════════════════════════ no work ═══════════════════════════════════


async def test_run_once_with_no_claimable_target_does_nothing(h):
    assert await h.publisher().run_once() is False
    assert h.status_writes == []


# ═════════════ the Notion prerequisite: fail closed, spend nothing ═══════════
#
# A version number is spent FOREVER — `reserve_publication_version` allocates
# once per target and every retry reuses it. `_prepare` reserves it, and only
# `_deliver`, afterwards, builds the client that needs a credential. So a head
# running with the two regeneration flags on but no usable Notion destination
# used to claim a target, burn its `Homework V{n}` identity, and only then
# discover it could never deliver. This is the guard that makes the whole pass
# refuse BEFORE the claim, which is the only point at which nothing is lost.


async def test_a_pass_refuses_before_claiming_when_notion_is_disabled(h, monkeypatch):
    monkeypatch.setattr(pub.settings, "notion_enabled", False)
    h.claim()

    assert await h.publisher().run_once() is False
    assert len(h.claims) == 1, "the queued target must not have been claimed"
    assert h.reconciles == 0, "the refusal must precede the whole pass"
    assert h.target.publication_version is None
    assert h.status_writes == []
    assert list(h.notion.calls) == []


@pytest.mark.parametrize(
    "key",
    ["", "   ", "not-a-notion-key", "ntn", '""'],
    ids=["empty", "blank", "wrong-shape", "truncated-prefix", "empty-quotes"],
)
async def test_a_pass_refuses_before_claiming_on_an_unusable_credential(
    h, monkeypatch, key
):
    """Enabled is not the same as configured. Every value here is one the real
    `NotionClientWrapper` constructor rejects, so letting the pass run would
    reserve a version for a delivery that cannot even build a client."""
    monkeypatch.setattr(pub.settings, "notion_enabled", True)
    monkeypatch.setattr(pub.settings, "notion_api_key", key)
    h.claim()

    assert await h.publisher().run_once() is False
    assert len(h.claims) == 1
    assert h.target.publication_version is None
    assert h.status_writes == []


async def test_an_unconfigured_head_never_reserves_a_version_it_cannot_deliver(
    h, monkeypatch
):
    """The regression, driven through the REAL client factory.

    `_default_client` is what production uses, and it raises on a missing
    credential — inside `_deliver`, which runs after the version is reserved and
    committed. Before the gate this pass returned True having consumed V2 and
    parked the target in `publication_failed`; now it never starts.
    """
    monkeypatch.setattr(pub.settings, "notion_enabled", True)
    monkeypatch.setattr(pub.settings, "notion_api_key", "")
    h.claim()
    publisher = h.publisher(
        client_factory=pub.RegenerationPublisher._default_client
    )

    assert await publisher.run_once() is False
    assert h.target.publication_version is None, "a version was consumed"
    assert h.target.status == "publishing", "no outcome may be written"
    assert h.status_writes == []
    assert h.rollups == []


async def test_the_refusal_names_the_setting_that_is_wrong(monkeypatch):
    """An operator reading the log has to know WHICH prerequisite is missing;
    "publication unavailable" alone sends them to the wrong file."""
    monkeypatch.setattr(pub.settings, "notion_enabled", False)
    monkeypatch.setattr(pub.settings, "notion_api_key", _USABLE_KEY)
    assert "NOTION_ENABLED" in (pub.publication_unavailable_reason() or "")

    monkeypatch.setattr(pub.settings, "notion_enabled", True)
    monkeypatch.setattr(pub.settings, "notion_api_key", "nonsense")
    assert "NOTION_API_KEY" in (pub.publication_unavailable_reason() or "")


async def test_a_configured_head_reports_no_reason_to_refuse(monkeypatch):
    monkeypatch.setattr(pub.settings, "notion_enabled", True)
    monkeypatch.setattr(pub.settings, "notion_api_key", _USABLE_KEY)
    assert pub.publication_unavailable_reason() is None


async def test_the_readiness_check_never_builds_a_notion_client(monkeypatch):
    """It runs on the event loop and inside request handlers, and
    `NotionClientWrapper.__init__` opens an HTTP client — which is exactly the
    work this module keeps off the loop."""
    from app.services.notion import client as client_mod

    def _boom(*_a, **_kw):
        raise AssertionError("the readiness check must not construct a client")

    monkeypatch.setattr(client_mod.NotionClientWrapper, "__init__", _boom)
    monkeypatch.setattr(pub.settings, "notion_enabled", True)
    monkeypatch.setattr(pub.settings, "notion_api_key", _USABLE_KEY)

    assert pub.publication_unavailable_reason() is None


async def test_run_forever_with_notion_unavailable_publishes_nothing(h, monkeypatch):
    """Defence in depth is only worth anything if the LOOP honours it too."""
    monkeypatch.setattr(pub.settings, "notion_enabled", False)
    h.claim()
    stop = asyncio.Event()
    publisher = h.publisher()

    task = asyncio.create_task(publisher.run_forever(stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=5)

    assert len(h.claims) == 1
    assert h.status_writes == []
    assert h.target.publication_version is None
    assert h.notion.calls == []
    assert h.rollups == []


async def test_every_pass_reconciles_terminal_revisions_before_selecting_work(h,
                                                                              monkeypatch):
    """A crash between a revision job's terminal commit and its target update
    would otherwise leave the target `generating` forever — with no API read to
    heal it, the publisher is the only thing that runs."""
    order: list[str] = []
    real_reconcile = pub.regeneration_job_state.reconcile_terminal_revision_jobs
    real_claim = pub.targets_repo.claim_next_publication

    async def _reconcile(session):
        order.append("reconcile")
        return await real_reconcile(session)

    async def _claim(session, **kw):
        order.append("claim")
        return await real_claim(session, **kw)

    monkeypatch.setattr(pub.regeneration_job_state,
                        "reconcile_terminal_revision_jobs", _reconcile)
    monkeypatch.setattr(pub.targets_repo, "claim_next_publication", _claim)
    await h.publisher().run_once()
    assert order == ["reconcile", "claim"]
    assert h.reconciles == 1


# ═════════════════════════ the happy path ════════════════════════════════


async def test_publishes_v2_under_a_newly_created_lesson_topic(h):
    h.claim()
    assert await h.publisher().run_once() is True

    assert h.notion.child_titles(_SUBJECT_PAGE) == ["Generated Homeworks"]
    container_id = h.child_ids(_SUBJECT_PAGE)[0]
    assert h.notion.child_titles(container_id) == ["1 Lesson one"]
    lesson_id = h.child_ids(container_id)[0]
    assert version_page_title(2) in h.notion.child_titles(lesson_id)

    page_id = [pid for pid in h.child_ids(lesson_id)
               if h.notion.titles[pid] == version_page_title(2)][0]
    marker = decode_revision_marker(h.notion.blocks[page_id])
    assert marker.publication_version == 2
    assert marker.toc_entry_id == h.toc_entry_id
    assert marker.revision_job_id == h.job_id
    assert marker.campaign_id == h.campaign_id
    assert marker.output_language == "uz"

    published = h.write("published")
    assert published["notion_page_id"] == page_id
    assert published["terminal_at"] is not None
    assert published["expected_statuses"] == ["publishing"]
    assert published["expected_claim_token"] == h.issued[0].claim_token
    assert h.rollups == [h.campaign_id]
    assert h.lesson_stamps == [(h.toc_entry_id, lesson_id)]
    assert h.off_loop(), "no Notion call may run on the event loop"


async def test_reuses_a_stamped_lesson_topic_inside_its_own_language_tree(h):
    """The stamped `notion_lesson_page_id` is shared with the legacy archive and
    the teacher deck; reusing it is what stops a title-suffix change from
    re-keying a lesson onto a fresh page.

    It is honoured only once the page is shown to sit under THIS language's
    `Generated Homeworks` container — the column carries no language, so
    membership of the target language's tree is the proof. The stamped page is
    titled with a suffix `resolve_lesson_title` no longer produces, so adoption
    here can only have come from the pointer.
    """
    container = h.notion.add_page(_SUBJECT_PAGE, _CONTAINER)
    lesson_id = h.notion.add_page(container, "1 Lesson one · p.7")
    h.section.notion_lesson_page_id = lesson_id
    h.claim()

    assert await h.publisher().run_once() is True
    assert h.lesson_stamps == [], "already stamped — nothing to persist"
    assert h.child_ids(_SUBJECT_PAGE) == [container], "no second container"
    assert h.notion.child_titles(container) == ["1 Lesson one · p.7"], (
        "the stamped Lesson Topic is adopted, never re-keyed onto a page named "
        "by the current title rule")
    assert version_page_title(2) in h.notion.child_titles(lesson_id)
    assert h.write("published")["notion_page_id"]


async def test_a_lesson_pointer_from_another_language_is_never_the_parent(h):
    """`toc_entries.notion_lesson_page_id` is ONE column for every language, so
    whichever lineage archived first owns it.

    A `ru` publication that trusted it would file `Homework V2` under the `uz`
    Lesson Topic — beside the `uz` V2, which is a `VersionPageCollision` that
    parks un-retryably, and beside the `uz` V1 even when there is no collision
    to trip over. The pointer is a hint; the language's own subject tree is the
    identity.
    """
    uz_container = h.notion.add_page(_SUBJECT_PAGE, _CONTAINER)
    uz_lesson = h.notion.add_page(uz_container, "1 Lesson one")
    h.section.notion_lesson_page_id = uz_lesson     # stamped by the uz lineage
    _speak_russian(h)
    h.claim(output_language="ru")

    assert await h.publisher().run_once() is True
    page_id = h.write("published")["notion_page_id"]

    assert h.notion.child_titles(uz_lesson) == [], (
        "the uz Lesson Topic must gain nothing from a ru publication")
    ru_container = h.child_ids(h.subject_page_id)[0]
    ru_lesson = h.child_ids(ru_container)[0]
    assert page_id in h.child_ids(ru_lesson), (
        "the ru V2 belongs under the ru subject page's own Lesson Topic")
    assert decode_revision_marker(
        h.notion.blocks[page_id]).output_language == "ru"
    assert h.lesson_stamps == [], (
        "the shared pointer is fill-once and belongs to the uz lineage")
    assert h.section.notion_lesson_page_id == uz_lesson, (
        "a foreign pointer is ignored for routing — never repointed, never "
        "cleared")


async def test_with_no_pointer_an_existing_lesson_topic_is_adopted_and_stamped(h):
    """The ordinary un-backfilled row: the legacy archive built the tree but
    never stamped the column. Resolution must ADOPT that Lesson Topic — V2 is a
    sibling of V1, not a parallel tree — and only then fill the pointer."""
    container = h.notion.add_page(_SUBJECT_PAGE, _CONTAINER)
    lesson_id = h.notion.add_page(container, "1 Lesson one")
    h.notion.add_page(lesson_id, "Homework")
    h.claim()

    assert await h.publisher().run_once() is True
    assert h.child_ids(_SUBJECT_PAGE) == [container], "no second container"
    assert h.child_ids(container) == [lesson_id], "no second Lesson Topic"
    assert h.notion.child_titles(lesson_id) == ["Homework", version_page_title(2)]
    assert h.lesson_stamps == [(h.toc_entry_id, lesson_id)]


async def test_v3_is_published_when_the_lineage_already_consumed_v2(h):
    h.reserved_version = 3
    h.claim()
    assert await h.publisher().run_once() is True
    page_id = h.write("published")["notion_page_id"]
    assert h.notion.titles[page_id] == version_page_title(3)
    assert decode_revision_marker(h.notion.blocks[page_id]).publication_version == 3


async def test_a_russian_publication_files_under_its_own_subject_page(h):
    """Versions are per (lesson, language): a `ru` V2 is independent of a `uz`
    V2, and must never be filed into the uz page."""
    _speak_russian(h)
    h.claim(output_language="ru")

    assert await h.publisher().run_once() is True
    assert h.subject_page_call[3] == "ru", "language-aware destination lookup"
    assert h.notion.child_titles(h.subject_page_id) == ["Generated Homeworks"]
    assert h.notion.child_titles(_SUBJECT_PAGE) == []
    page_id = h.write("published")["notion_page_id"]
    assert decode_revision_marker(h.notion.blocks[page_id]).output_language == "ru"
    assert decode_revision_marker(h.notion.blocks[page_id]).publication_version == 2


# ══════════════════════ crash / adoption recovery ════════════════════════


async def test_a_crash_after_page_creation_adopts_exactly_one_page(h):
    """The worst window: the version page exists in Notion but the process died
    before any DB write. The lease expires, the target is claimed again with the
    SAME reserved version, and the MARKER (not the title) proves the page is
    ours — so nothing is duplicated and nothing is cleared."""
    def _write_then_die(real, **kwargs):
        real(**kwargs)
        raise RuntimeError("process died mid-delivery")

    h.write_hook = _write_then_die
    first = h.claim()
    assert await h.publisher().run_once() is True
    assert h.write("publication_failed")["publication_next_attempt_at"] is not None
    assert h.target.notion_page_id is None, "the DB never learned the page id"

    lesson_id = h.lesson_page_id()
    assert h.notion.child_titles(lesson_id).count(version_page_title(2)) == 1

    # lease expired → a fresh claim on the same target, same reserved version
    h.write_hook = None
    h.target.status = "publication_pending"
    h.target.publication_last_error = None
    second = h.claim(publication_attempts=2)
    assert second.claim_token != first.claim_token
    assert await h.publisher().run_once() is True

    assert h.notion.child_titles(lesson_id).count(version_page_title(2)) == 1, (
        "exactly one V2 page — adopted by marker, not duplicated"
    )
    roots = [c for c in h.notion.calls
             if c[0] == "create_page" and c[1] == lesson_id]
    assert len(roots) == 1 and roots[0][2] == version_page_title(2), (
        "the version ROOT is created once across both attempts; the leaf pages "
        "beneath it are rebuilt by the writer's own repair path"
    )
    published = h.write("published")
    assert published["notion_page_id"] in h.child_ids(lesson_id)


async def test_a_completed_publication_re_run_writes_nothing_new(h):
    """Idempotence across a lost completion write: the digest already on the
    page means the second attempt renders nothing at all."""
    h.claim()
    await h.publisher().run_once()
    page_id = h.write("published")["notion_page_id"]
    before = len(h.notion.calls)

    h.status_writes.clear()
    h.target.status = "publication_pending"
    h.target.publication_last_error = None
    h.claim(publication_attempts=2)
    assert await h.publisher().run_once() is True

    after = [c for c in h.notion.calls[before:]]
    assert h.write("published")["notion_page_id"] == page_id
    assert not any(c[0] in ("create_page", "append_block_children",
                            "clear_content_blocks", "delete_block")
                   for c in after), f"a completed page must be re-read only: {after}"


# ═════════════════════════ failure handling ══════════════════════════════


async def test_a_transient_failure_backs_off_exponentially(h):
    h.write_hook = _raise(RuntimeError("notion 503"))
    h.claim(publication_attempts=2)
    before = _now()
    assert await h.publisher().run_once() is True

    failed = h.write("publication_failed")
    assert "notion 503" in failed["publication_last_error"]
    delay = (failed["publication_next_attempt_at"] - before).total_seconds()
    assert 110 <= delay <= 130, f"attempt 2 → 2×60s, got {delay}"
    assert failed["clear_publication_claim"] is True
    assert h.rollups == [h.campaign_id]


async def test_backoff_is_capped(h):
    h.write_hook = _raise(RuntimeError("notion 503"))
    h.claim(publication_attempts=9)
    before = _now()
    assert await h.publisher(max_attempts=50, backoff_max_seconds=300
                             ).run_once() is True
    delay = (h.write("publication_failed")["publication_next_attempt_at"]
             - before).total_seconds()
    assert 290 <= delay <= 310


async def test_an_exhausted_budget_parks_for_the_operator(h):
    """`publication_next_attempt_at` must be CLEARED, not merely left unset: the
    row still carries the PAST timestamp that made it claimable, so leaving it
    would loop a permanently failing delivery forever."""
    h.write_hook = _raise(RuntimeError("notion 503"))
    h.target.publication_next_attempt_at = _now() - timedelta(hours=1)
    h.claim(publication_attempts=3)
    assert await h.publisher(max_attempts=3).run_once() is True

    failed = h.write("publication_failed")
    assert failed["publication_next_attempt_at"] is None
    assert failed.get("clear_publication_next_attempt") is True
    assert h.target.publication_next_attempt_at is None
    assert "notion 503" in failed["publication_last_error"]


async def test_a_version_page_collision_is_operator_only(h):
    """A same-title page we cannot prove is ours is never retried automatically
    — retrying cannot change the answer, and a human has to look."""
    h.write_hook = _raise(VersionPageCollision("page pg9 carries no revision marker"))
    h.claim()
    assert await h.publisher().run_once() is True

    failed = h.write("publication_failed")
    assert failed["publication_next_attempt_at"] is None
    assert failed.get("clear_publication_next_attempt") is True
    assert "no revision marker" in failed["publication_last_error"]


async def test_a_missing_notion_destination_is_a_visible_failure(h):
    """Configuration can change between the campaign preflight and delivery, so
    the publisher revalidates and refuses rather than mis-filing."""
    h.subject_page_id = None
    h.claim()
    assert await h.publisher().run_once() is True

    failed = h.write("publication_failed")
    assert "Notion" in failed["publication_last_error"]
    assert failed["publication_next_attempt_at"] is None
    assert h.notion.calls == [], "refused before any remote call"


async def test_an_incomplete_revision_snapshot_is_never_published(h):
    """Publishing a packet with a missing phase is the one outcome regeneration
    exists to prevent."""
    h.snapshot_complete = False
    h.claim()
    assert await h.publisher().run_once() is True

    failed = h.write("publication_failed")
    assert "snapshot" in failed["publication_last_error"]
    assert h.notion.calls == []


async def test_a_lesson_topic_that_cannot_be_resolved_fails_retryably(h):
    def _boom(_real, _client, _parent_id, _title):
        raise RuntimeError("notion 500 on find_or_create")

    h.parent_hook = _boom
    h.claim()
    assert await h.publisher().run_once() is True

    failed = h.write("publication_failed")
    assert "find_or_create" in failed["publication_last_error"]
    assert failed["publication_next_attempt_at"] is not None
    assert h.lesson_stamps == [], "nothing to stamp when the parent never resolved"


# ═══════════════════════ fencing and abandonment ═════════════════════════


async def test_a_stale_claim_writes_nothing(h):
    """The lease expired and a peer took over mid-delivery. This publisher must
    discard its own outcome rather than overwrite the new owner's."""
    claim = h.claim()
    h.target.publication_claim_token = uuid.uuid4()  # peer takeover
    assert await h.publisher().run_once() is True

    assert h.status_writes == []
    assert h.notion.calls == []
    assert h.rollups == []
    assert h.target.publication_claim_token != claim.claim_token


async def test_a_successful_write_publishes_even_under_a_cancellation(h):
    """The page is out there. Landing anything but `published` would leave a
    live Notion page the campaign reports as abandoned."""
    def _write_then_cancel(real, **kwargs):
        page_id = real(**kwargs)
        h.target.abandon_requested_at = _now()
        h.target.abandon_requested_reason = "campaign cancelled: stop"
        return page_id

    h.write_hook = _write_then_cancel
    h.claim()
    assert await h.publisher().run_once() is True

    published = h.write("published")
    assert published["terminal_at"] is not None
    assert h.target.status == "published"
    assert h.rollups == [h.campaign_id]


async def test_a_failed_write_under_a_cancellation_lands_abandoned(h):
    """No page was written and the operator asked to stop: converge terminally
    so the campaign can roll up — and keep the reserved version, which is
    consumed forever either way."""
    def _cancel(_version):
        h.target.abandon_requested_at = _now()
        h.target.abandon_requested_reason = "campaign cancelled: stop"

    h.reserve_hook = _cancel
    h.write_hook = _raise(RuntimeError("notion 500"))
    h.claim()
    assert await h.publisher().run_once() is True

    abandoned = h.write("abandoned")
    assert abandoned["terminal_at"] is not None
    assert "stop" in abandoned["terminal_reason"]
    assert abandoned.get("publication_version") is None, (
        "the reserved version is preserved, not rewritten or cleared"
    )
    assert h.target.publication_version == 2
    assert h.rollups == [h.campaign_id]


async def test_an_abandon_intent_before_any_remote_call_converges_terminally(h):
    """A cancel landing between the claim and the first Notion call: there is no
    unknown-outcome request to protect, so the target converges immediately
    instead of being re-claimed forever and wedging the campaign."""
    h.claim()
    h.target.abandon_requested_at = _now()
    h.target.abandon_requested_reason = "campaign cancelled: stop"
    assert await h.publisher().run_once() is True

    assert h.write("abandoned")["terminal_at"] is not None
    assert h.notion.calls == [], "no page is created for an abandoned target"
    assert h.rollups == [h.campaign_id]


async def test_a_campaign_that_lost_approval_is_not_published(h):
    h.campaign.status = "cancelled"
    h.campaign.approved_at = None
    h.claim()
    assert await h.publisher().run_once() is True

    failed = h.write("publication_failed")
    assert "approved" in failed["publication_last_error"]
    assert h.notion.calls == []


# ═══════════════════════ loop and rollup wiring ══════════════════════════


async def test_run_forever_drains_work_then_stops_promptly(h):
    h.claim()
    stop = asyncio.Event()
    task = asyncio.create_task(h.publisher(interval_seconds=0.01).run_forever(stop))
    for _ in range(200):
        await asyncio.sleep(0.01)
        if h.status_writes:
            break
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert h.write("published")


async def test_run_forever_survives_a_failing_pass(h, monkeypatch):
    calls: list[int] = []

    async def _boom(session):
        calls.append(1)
        raise RuntimeError("database went away")

    monkeypatch.setattr(pub.regeneration_job_state,
                        "reconcile_terminal_revision_jobs", _boom)
    stop = asyncio.Event()
    task = asyncio.create_task(h.publisher(interval_seconds=0.01).run_forever(stop))
    for _ in range(200):
        await asyncio.sleep(0.01)
        if len(calls) >= 2:
            break
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert len(calls) >= 2, "one bad pass must not kill the loop"


async def test_a_rollup_failure_does_not_undo_a_published_target(h):
    async def _boom(campaign_id):
        raise RuntimeError("campaign table unavailable")

    h.claim()
    publisher = h.publisher(campaign_service=SimpleNamespace(roll_up=_boom))
    assert await publisher.run_once() is True
    assert h.write("published")["notion_page_id"]
    assert h.target.status == "published"


async def test_a_takeover_detected_during_version_reservation_writes_nothing(h,
                                                                            monkeypatch):
    """`reserve_publication_version` fences on the claim token too, and it
    signals a takeover by RAISING. That is a benign "someone else owns this
    now", not a delivery failure: writing `publication_failed` over the new
    owner's row — or logging it as an unexpected error — would both be wrong."""
    async def _stale(session, *, target_id, claim_token):
        raise StalePublicationClaim(f"target {target_id}: claim is not current")

    monkeypatch.setattr(pub.targets_repo, "reserve_publication_version", _stale)
    h.claim()
    assert await h.publisher().run_once() is True

    assert h.status_writes == [], "the new owner's row must not be touched"
    assert h.notion.calls == []
    assert h.rollups == []


# ═════════════ the campaign's declared publication version ═══════════════


#: A statement and a driver message that name NO constraint. Every builder
#: below uses them so that a test which passes can only have passed through the
#: lookup it is named for — a message that quoted the index would let the
#: substring fallback answer for all three shapes at once, which is exactly how
#: the earlier `SimpleNamespace(constraint_name=...)` fixture (whose `repr`
#: leaked the name into `str(exc)`) pinned nothing.
_NEUTRAL_STATEMENT = "UPDATE regeneration_targets SET publication_version=$1"
_NEUTRAL_MESSAGE = (
    "<class 'asyncpg.exceptions.UniqueViolationError'>: "
    "duplicate key value violates a unique constraint"
)


class _NamedDriverError(Exception):
    """A driver exception that reports `constraint_name`, psycopg-style — and
    what asyncpg's OWN exception carries before SQLAlchemy translates it."""

    def __init__(self, message: str, *, constraint_name, sqlstate: str = "23505"):
        super().__init__(message)
        self.constraint_name = constraint_name
        self.sqlstate = sqlstate


class _TranslatedDriverError(Exception):
    """What SQLAlchemy's asyncpg dialect actually puts on `.orig`.

    `sqlalchemy/dialects/postgresql/asyncpg.py::_handle_exception` builds a
    FRESH `AsyncAdapt_asyncpg_dbapi.IntegrityError` from a message string,
    copies `pgcode`/`sqlstate` and nothing else, then `raise translated from
    error` — so `constraint_name` survives only on `__cause__`.
    """

    def __init__(self, message: str, *, sqlstate: str = "23505"):
        super().__init__(message)
        self.pgcode = self.sqlstate = sqlstate


def _integrity_error(orig, *, statement: str = _NEUTRAL_STATEMENT):
    from sqlalchemy.exc import IntegrityError

    return IntegrityError(statement, {}, orig)


def _asyncpg_integrity_error(constraint, *, message: str = _NEUTRAL_MESSAGE):
    """The PRODUCTION shape: `.orig` has no `constraint_name` at all and the
    name is reachable only through `.orig.__cause__`."""
    translated = _TranslatedDriverError(message)
    translated.__cause__ = _NamedDriverError(message, constraint_name=constraint)
    return _integrity_error(translated, statement=_NEUTRAL_STATEMENT)


def _psycopg_integrity_error(constraint):
    """A driver that reports the name on `.orig` itself."""
    return _integrity_error(
        _NamedDriverError(_NEUTRAL_MESSAGE, constraint_name=constraint)
    )


def _nameless_integrity_error(index: str):
    """A driver that reports no name anywhere; the index is only in the text."""
    return _integrity_error(
        _TranslatedDriverError(
            f'duplicate key value violates unique constraint "{index}"'
        )
    )


async def test_a_refused_publication_version_parks_the_target_for_an_operator(h):
    """`PublicationVersionUnavailable` is NOT a lease handover.

    `_TAKEOVER` returns without recording anything, which is right for a stolen
    claim and catastrophic here: retrying cannot free a consumed number, so the
    row would be re-claimed every lease forever. It must land a NON-retryable
    outcome instead — and the failed preparation session must be rolled back
    before that outcome is written in its own fresh session.
    """
    from app.repositories.regeneration_targets import PublicationVersionUnavailable

    h.reserve_error = PublicationVersionUnavailable(
        "Homework V4 is already consumed for this lesson and language"
    )
    h.claim()
    assert await h.publisher().run_once() is True

    write = h.write("publication_failed")
    assert "already consumed" in write["publication_last_error"]
    assert write["publication_next_attempt_at"] is None, "parked, not retried"
    assert h.rollbacks == 1, "the failed preparation session was not rolled back"
    assert h.notion.calls == []
    assert h.target.publication_version is None


async def test_a_publication_version_unique_violation_parks_the_target_too(h):
    """The partial unique index is the final fence and it fires at the UPDATE
    inside the reservation, not only at the trailing commit — so the whole
    preparation is covered, and the operator sees the version message rather
    than a raw database error that would be retried three times first.

    Shape 1 of 3, and the one production actually raises: under the asyncpg
    dialect the name lives ONLY on `.orig.__cause__`. The fixture proves that
    by keeping the index out of the statement, the params and `.orig`'s own
    text, so nothing but the `__cause__` lookup can answer.
    """
    exc = _asyncpg_integrity_error("uq_regeneration_targets_publication_version")
    assert not hasattr(exc.orig, "constraint_name"), (
        "the translated asyncpg error carries no constraint name"
    )
    assert pub._VERSION_INDEX not in str(exc), (
        "the substring fallback must not be able to answer this one"
    )
    h.reserve_error = exc
    h.claim()
    assert await h.publisher().run_once() is True

    write = h.write("publication_failed")
    assert pub._VERSION_INDEX in write["publication_last_error"]
    assert write["publication_next_attempt_at"] is None, "parked, not retried"
    assert h.rollbacks == 1
    assert h.notion.calls == []


async def test_a_driver_that_names_the_constraint_on_orig_is_read_too(h):
    """Shape 2 of 3: psycopg reports `constraint_name` on `.orig` itself.

    Kept working deliberately — the asyncpg lookup is an ADDITION, not a
    replacement, and a driver swap must not silently turn a spent version back
    into a retried database error.
    """
    exc = _psycopg_integrity_error("uq_regeneration_targets_publication_version")
    assert pub._VERSION_INDEX not in str(exc)
    h.reserve_error = exc
    h.claim()
    assert await h.publisher().run_once() is True

    write = h.write("publication_failed")
    assert pub._VERSION_INDEX in write["publication_last_error"]
    assert write["publication_next_attempt_at"] is None, "parked, not retried"
    assert h.rollbacks == 1
    assert h.notion.calls == []


async def test_a_version_violation_with_no_name_anywhere_falls_back_to_the_text(h):
    """Shape 3 of 3: no name on `.orig`, none on `.orig.__cause__`.

    The text match is the LAST resort precisely because it is the weak one; it
    still has to hold, or a driver that reports nothing would retry a number
    that can never be freed.
    """
    exc = _nameless_integrity_error(pub._VERSION_INDEX)
    assert getattr(exc.orig, "constraint_name", None) is None
    assert getattr(exc.orig.__cause__, "constraint_name", None) is None
    h.reserve_error = exc
    h.claim()
    assert await h.publisher().run_once() is True

    write = h.write("publication_failed")
    assert write["publication_next_attempt_at"] is None, "parked, not retried"
    assert h.rollbacks == 1
    assert h.notion.calls == []


async def test_the_index_fence_refusal_names_the_version_not_the_index(h):
    """What an OPERATOR reads when the final fence fires.

    The sibling refusal, raised by `reserve_publication_version`, already says
    `Homework V4 is already consumed …`. This branch is the same condition
    caught one layer lower, so it must not degrade into a bare index name: the
    NUMBER is the actionable part, and
    `uq_regeneration_targets_publication_version` is an identifier, not an
    instruction — it belongs in the diagnostic tail.
    """
    h.campaign.publication_version = 4
    h.reserve_error = _asyncpg_integrity_error(pub._VERSION_INDEX)
    h.claim()
    assert await h.publisher().run_once() is True

    message = h.write("publication_failed")["publication_last_error"]
    assert message.endswith(pub._VERSION_INDEX_NOTE), (
        f"the index belongs in the diagnostic tail, not the sentence: {message!r}"
    )
    sentence = message[: -len(pub._VERSION_INDEX_NOTE)]
    assert "Homework V4" in sentence, (
        f"the operator sentence does not name the version: {sentence!r}"
    )
    assert pub._VERSION_INDEX not in sentence, (
        f"a raw index identifier leaked into the instruction: {sentence!r}"
    )
    assert "new campaign" in sentence and "abandon" in sentence, (
        "a declared version is immutable, so `retry_publication` re-reserves "
        "the same consumed number and parks again — the refusal has to name "
        f"the remedy that actually works: {sentence!r}"
    )


async def test_an_unrelated_integrity_error_is_not_a_publication_version_conflict(h):
    """A different constraint is a database defect, not a spent number. Hiding
    it behind the version message would tell an operator to pick another
    version for a fault no version can fix.

    On the production shape, and with the version index quoted in the driver's
    own text: a NAME that says something else outranks a message that merely
    mentions this one. That is the whole reason the text match is last.
    """
    h.reserve_error = _asyncpg_integrity_error(
        "fk_regeneration_targets_campaign_id",
        message=(
            "insert or update violates foreign key constraint; DETAIL: the row "
            f"quotes {pub._VERSION_INDEX} and must not be read as it"
        ),
    )
    h.claim()
    assert await h.publisher().run_once() is True

    write = h.write("publication_failed")
    assert "consumed" not in write["publication_last_error"]
    assert "IntegrityError" in write["publication_last_error"]
    assert write["publication_next_attempt_at"] is not None, (
        "an unexplained database error is retryable; only a spent version is not"
    )
    assert h.rollbacks == 1
