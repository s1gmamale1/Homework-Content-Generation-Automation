"""Unit tests for ``app/services/regeneration_discovery.py``.

Scope split, deliberately:

* **here** — the SERVICE's decisions: which job of a lineage becomes the
  source, when a lineage is refused and with which stable reason, and what the
  read-only Notion preflight concludes. The ``regeneration_sources`` repository
  is replaced by an in-memory stand-in so a test states the row situation
  directly instead of through SQL;
* **``tests/integration/test_regeneration_source_and_version_queries.py``** —
  the repository CONTRACT itself against a real Postgres: that a failed job, a
  ``teacher_material`` job and a revision job are not V1 candidates, that an
  unpublished/abandoned revision is never returned, and that languages and
  version numbers are isolated. Those are SQL predicates; a fake session cannot
  prove them and this file does not pretend to.

Completeness is never re-implemented here: the expected reasons are the ones
``regeneration_planner.validate_complete_snapshot`` produces, imported from it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional

import pytest

from app.services import flows
from app.services.regeneration_planner import FLOW_DRIFT_REASON

SUBJECT = "math-algebra"
CANONICAL = ("extract", *flows.flow_for(SUBJECT))


# ─────────────────────────── row stand-ins ───────────────────────────


@dataclass
class _Job:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    book_id: uuid.UUID = field(default_factory=uuid.uuid4)
    toc_entry_id: uuid.UUID = field(default_factory=uuid.uuid4)
    subject: str = SUBJECT
    output_language: str = "uz"
    status: str = "done"
    kind: str = "homework"
    revision_of_job_id: Optional[uuid.UUID] = None


@dataclass
class _Target:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    publication_version: int = 2
    source_job_id: Optional[uuid.UUID] = field(default_factory=uuid.uuid4)
    status: str = "published"


@dataclass
class _PhaseRow:
    phase_name: str
    phase_order: int
    status: str = "done"
    output_md: Optional[str] = "content"
    content_json: Optional[dict] = None


def _complete_rows() -> list[_PhaseRow]:
    return [_PhaseRow(name, i) for i, name in enumerate(CANONICAL)]


def _candidate(job: _Job, **overrides):
    from app.repositories.regeneration_sources import LineageCandidate

    kwargs = dict(
        toc_entry_id=job.toc_entry_id,
        output_language=job.output_language,
        book_id=job.book_id,
        grade="7",
        book_filename="algebra-7.pdf",
        section_number="1",
        section_title="Kirish",
        chapter_title="I bob",
        page_start=4,
        notion_lesson_page_id=None,
        order_index=0,
    )
    kwargs.update(overrides)
    return LineageCandidate(**kwargs)


class _FakeRepo:
    """In-memory stand-in for ``app.repositories.regeneration_sources``.

    It answers exactly the contract each real function documents; the real SQL
    behind that contract is proven in the integration file.
    """

    def __init__(
        self,
        *,
        candidates=(),
        v1_jobs=None,
        published=None,
        revision_jobs=None,
        phase_rows=None,
        missing_source=None,
        versions=None,
    ):
        self.candidates = list(candidates)
        self.v1_jobs = dict(v1_jobs or {})
        self.published = dict(published or {})
        self.revision_jobs = dict(revision_jobs or {})
        self.phase_rows = dict(phase_rows or {})
        self.missing_source = dict(missing_source or {})
        self.versions = dict(versions or {})
        self.phase_row_calls: list[list[uuid.UUID]] = []
        self.candidate_limits: list[Optional[int]] = []

    def install(self, monkeypatch):
        from app.repositories import regeneration_sources as sources_repo
        from app.repositories import regeneration_targets as targets_repo

        async def candidate_lineages(session, **kwargs):
            self.candidate_limits.append(kwargs.get("limit"))
            return list(self.candidates)

        async def latest_v1_source_job(session, *, toc_entry_id, output_language):
            return self.v1_jobs.get((toc_entry_id, output_language))

        async def latest_published_target(
            session, *, toc_entry_id, output_language, for_update=False
        ):
            return self.published.get((toc_entry_id, output_language))

        async def next_expected_version(session, *, toc_entry_id, output_language):
            return self.versions.get((toc_entry_id, output_language), 2)

        async def lineage_targets_missing_source(
            session, *, toc_entry_id, output_language
        ):
            return list(self.missing_source.get((toc_entry_id, output_language), []))

        async def phase_rows_for_jobs(session, job_ids):
            self.phase_row_calls.append(list(job_ids))
            return {jid: list(self.phase_rows.get(jid, [])) for jid in job_ids}

        async def revision_job_for_target(session, *, target_id):
            return self.revision_jobs.get(target_id)

        for name, fn in (
            ("candidate_lineages", candidate_lineages),
            ("latest_v1_source_job", latest_v1_source_job),
            ("latest_published_target", latest_published_target),
            ("next_expected_version", next_expected_version),
            ("lineage_targets_missing_source", lineage_targets_missing_source),
            ("phase_rows_for_jobs", phase_rows_for_jobs),
        ):
            monkeypatch.setattr(sources_repo, name, fn)
        monkeypatch.setattr(
            targets_repo, "revision_job_for_target", revision_job_for_target
        )
        return self


@pytest.mark.asyncio
async def test_discovery_refuses_overwide_work_before_per_lineage_queries(
    monkeypatch,
):
    from app.services import regeneration_discovery as discovery

    maximum = 2
    monkeypatch.setattr(
        discovery.settings, "regeneration_max_discovery_lineages", maximum
    )
    repo = _FakeRepo(candidates=[_candidate(_Job()) for _ in range(maximum + 1)])
    repo.install(monkeypatch)

    async def _must_not_pick(*_args, **_kwargs):
        raise AssertionError("overwide discovery reached per-lineage queries")

    monkeypatch.setattr(discovery, "_pick_source_job", _must_not_pick)

    with pytest.raises(discovery.DiscoverySelectionTooLarge) as excinfo:
        await discovery.list_source_candidates(
            None, book_ids=[uuid.uuid4()], toc_entry_ids=None,
            output_languages=None,
        )

    assert repo.candidate_limits == [maximum + 1]
    assert excinfo.value.count_at_least == maximum + 1
    assert excinfo.value.maximum == maximum


def _one_lineage(monkeypatch, *, job=None, rows=None, **repo_kwargs) -> _Job:
    job = job or _Job()
    _FakeRepo(
        candidates=[_candidate(job)],
        v1_jobs={(job.toc_entry_id, job.output_language): job},
        phase_rows={job.id: rows if rows is not None else _complete_rows()},
        **repo_kwargs,
    ).install(monkeypatch)
    return job


# ───────────────────────────── source choice ──────────────────────────


@pytest.mark.asyncio
async def test_v1_fallback_uses_the_latest_completed_ordinary_job(monkeypatch):
    from app.services import regeneration_discovery as discovery

    job = _one_lineage(monkeypatch)

    sources = await discovery.list_eligible_sources(
        None, book_ids=None, toc_entry_ids=None, output_languages=None
    )
    assert [s.source_job_id for s in sources] == [job.id]
    assert sources[0].source_publication_version == 1
    assert sources[0].source_is_revision is False
    assert sources[0].next_expected_version == 2

    resolved = await discovery.resolve_default_source(
        None, toc_entry_id=job.toc_entry_id, output_language=job.output_language
    )
    assert resolved is job


@pytest.mark.asyncio
async def test_v3_source_is_the_highest_published_revision(monkeypatch):
    """With V2 published, the next revision is built on the V2 REVISION JOB —
    not on the original V1 job, which is two versions stale."""
    from app.services import regeneration_discovery as discovery

    v1 = _Job()
    revision = _Job(
        book_id=v1.book_id,
        toc_entry_id=v1.toc_entry_id,
        revision_of_job_id=v1.id,
    )
    target = _Target(publication_version=2, source_job_id=v1.id)
    _FakeRepo(
        candidates=[_candidate(v1)],
        v1_jobs={(v1.toc_entry_id, "uz"): v1},
        published={(v1.toc_entry_id, "uz"): target},
        revision_jobs={target.id: revision},
        phase_rows={revision.id: _complete_rows(), v1.id: _complete_rows()},
        versions={(v1.toc_entry_id, "uz"): 3},
    ).install(monkeypatch)

    resolved = await discovery.resolve_default_source(
        None, toc_entry_id=v1.toc_entry_id, output_language="uz"
    )
    assert resolved is revision

    (source,) = await discovery.list_eligible_sources(
        None, book_ids=None, toc_entry_ids=None, output_languages=None
    )
    assert source.source_job_id == revision.id
    assert source.source_publication_version == 2
    assert source.source_is_revision is True
    assert source.next_expected_version == 3


@pytest.mark.asyncio
async def test_a_published_target_whose_revision_job_is_gone_is_refused(monkeypatch):
    from app.services import regeneration_discovery as discovery

    v1 = _Job()
    target = _Target(publication_version=2, source_job_id=v1.id)
    _FakeRepo(
        candidates=[_candidate(v1)],
        v1_jobs={(v1.toc_entry_id, "uz"): v1},
        published={(v1.toc_entry_id, "uz"): target},
        revision_jobs={},  # the revision row is gone
        phase_rows={v1.id: _complete_rows()},
    ).install(monkeypatch)

    assert (
        await discovery.list_eligible_sources(
            None, book_ids=None, toc_entry_ids=None, output_languages=None
        )
        == []
    )
    with pytest.raises(discovery.NoEligibleSource) as excinfo:
        await discovery.resolve_default_source(
            None, toc_entry_id=v1.toc_entry_id, output_language="uz"
        )
    assert discovery.PUBLISHED_REVISION_JOB_MISSING_REASON in excinfo.value.reasons
    # NOT silently demoted to the V1 job: the newest published content has no
    # snapshot, so regenerating from V1 would branch off a stale page.
    assert str(v1.id) not in str(excinfo.value)


@pytest.mark.asyncio
async def test_discovery_excludes_a_target_whose_source_job_id_is_null(monkeypatch):
    """A child-first purge nulls `regeneration_targets.source_job_id` (SET NULL)
    and leaves the reporting row behind. Both entry points must refuse on THAT
    predicate — not on a generic "source no longer eligible" — or the operator
    is told to look for a problem that does not exist."""
    from app.services import regeneration_discovery as discovery

    job = _Job()
    purged_target_id = uuid.uuid4()
    _FakeRepo(
        candidates=[_candidate(job)],
        # The V1 job is present and perfectly complete: nothing but the null
        # link may be doing the refusing.
        v1_jobs={(job.toc_entry_id, "uz"): job},
        phase_rows={job.id: _complete_rows()},
        missing_source={(job.toc_entry_id, "uz"): [purged_target_id]},
    ).install(monkeypatch)

    assert (
        await discovery.list_eligible_sources(
            None, book_ids=None, toc_entry_ids=None, output_languages=None
        )
        == []
    )
    (candidate,) = await discovery.list_source_candidates(
        None, book_ids=None, toc_entry_ids=None, output_languages=None
    )
    assert candidate.eligible is False
    assert candidate.reasons == (discovery.SOURCE_JOB_ID_IS_NULL_REASON,)

    with pytest.raises(discovery.NoEligibleSource) as excinfo:
        await discovery.resolve_default_source(
            None, toc_entry_id=job.toc_entry_id, output_language="uz"
        )
    assert excinfo.value.reasons == (discovery.SOURCE_JOB_ID_IS_NULL_REASON,)

    # The refusal names the predicate itself, and names the purged row.
    assert "source_job_id IS NULL" in discovery.SOURCE_JOB_ID_IS_NULL_REASON
    assert discovery.SOURCE_JOB_ID_IS_NULL_REASON != discovery.NO_COMPLETED_SOURCE_REASON
    assert str(purged_target_id) in str(excinfo.value)


@pytest.mark.asyncio
async def test_lineage_without_a_completed_homework_job_is_ineligible(monkeypatch):
    """Failed jobs and teacher decks never reach the service — the repository's
    `status='done' AND kind='homework'` filter drops them, so the lineage
    arrives here with no source at all."""
    from app.services import regeneration_discovery as discovery

    toc_id = uuid.uuid4()
    _FakeRepo(
        candidates=[_candidate(_Job(toc_entry_id=toc_id))],
        v1_jobs={},
        phase_rows={},
    ).install(monkeypatch)

    (candidate,) = await discovery.list_source_candidates(
        None, book_ids=None, toc_entry_ids=None, output_languages=None
    )
    assert candidate.eligible is False
    assert candidate.reasons == (discovery.NO_COMPLETED_SOURCE_REASON,)
    with pytest.raises(discovery.NoEligibleSource):
        await discovery.resolve_default_source(
            None, toc_entry_id=toc_id, output_language="uz"
        )


@pytest.mark.asyncio
async def test_an_incomplete_snapshot_surfaces_the_planner_reasons_verbatim(monkeypatch):
    """Task 2's `validate_complete_snapshot` is the ONLY completeness authority;
    discovery reports its strings rather than inventing a second definition."""
    from app.services import regeneration_discovery as discovery
    from app.services.regeneration_planner import validate_complete_snapshot

    rows = [r for r in _complete_rows() if r.phase_name != "reflection"]
    rows[3].status = "failed"
    job = _one_lineage(monkeypatch, rows=rows)

    (candidate,) = await discovery.list_source_candidates(
        None, book_ids=None, toc_entry_ids=None, output_languages=None
    )
    expected = validate_complete_snapshot(subject=SUBJECT, rows=rows).reasons
    assert expected  # the fixture really is incomplete
    assert candidate.reasons == expected
    assert candidate.eligible is False

    with pytest.raises(discovery.NoEligibleSource) as excinfo:
        await discovery.resolve_default_source(
            None, toc_entry_id=job.toc_entry_id, output_language="uz"
        )
    assert excinfo.value.reasons == expected


@pytest.mark.asyncio
async def test_a_source_from_an_older_flow_is_explained_as_flow_drift(monkeypatch):
    from app.services import regeneration_discovery as discovery

    rows = [*_complete_rows(), _PhaseRow("retired-phase", 99)]
    _one_lineage(monkeypatch, rows=rows)

    (candidate,) = await discovery.list_source_candidates(
        None, book_ids=None, toc_entry_ids=None, output_languages=None
    )
    assert FLOW_DRIFT_REASON in candidate.reasons
    assert candidate.eligible is False


@pytest.mark.asyncio
async def test_an_unsupported_subject_is_reported_not_raised(monkeypatch):
    """A retired subject on an old job must not blow up a 200-lesson discovery."""
    from app.services import regeneration_discovery as discovery

    # The retired subject lives on the JOB — which is the one discovery grades
    # against (`_snapshot_reasons` reads `job.subject`); the lineage row has no
    # subject of its own, on purpose.
    job = _Job(subject="quidditch")
    _FakeRepo(
        candidates=[_candidate(job)],
        v1_jobs={(job.toc_entry_id, "uz"): job},
        phase_rows={job.id: _complete_rows()},
    ).install(monkeypatch)

    (candidate,) = await discovery.list_source_candidates(
        None, book_ids=None, toc_entry_ids=None, output_languages=None
    )
    assert candidate.eligible is False
    assert candidate.reasons == (discovery.UNKNOWN_SUBJECT_REASON.format(subject="quidditch"),)


@pytest.mark.asyncio
async def test_languages_are_independent_lineages(monkeypatch):
    """One lesson, two languages: each keeps its own source and its own version
    counter — an RU publication must never bump the UZ number."""
    from app.services import regeneration_discovery as discovery

    toc_id, book_id = uuid.uuid4(), uuid.uuid4()
    uz = _Job(toc_entry_id=toc_id, book_id=book_id, output_language="uz")
    ru = _Job(toc_entry_id=toc_id, book_id=book_id, output_language="ru")
    ru_revision = _Job(
        toc_entry_id=toc_id, book_id=book_id, output_language="ru",
        revision_of_job_id=ru.id,
    )
    ru_target = _Target(publication_version=3, source_job_id=ru.id)
    _FakeRepo(
        candidates=[_candidate(uz), _candidate(ru)],
        v1_jobs={(toc_id, "uz"): uz, (toc_id, "ru"): ru},
        published={(toc_id, "ru"): ru_target},
        revision_jobs={ru_target.id: ru_revision},
        phase_rows={
            uz.id: _complete_rows(),
            ru.id: _complete_rows(),
            ru_revision.id: _complete_rows(),
        },
        versions={(toc_id, "uz"): 2, (toc_id, "ru"): 4},
    ).install(monkeypatch)

    by_lang = {
        s.output_language: s
        for s in await discovery.list_eligible_sources(
            None, book_ids=None, toc_entry_ids=None, output_languages=None
        )
    }
    assert by_lang["uz"].source_job_id == uz.id
    assert by_lang["uz"].next_expected_version == 2
    assert by_lang["ru"].source_job_id == ru_revision.id
    assert by_lang["ru"].next_expected_version == 4


@pytest.mark.asyncio
async def test_legacy_archive_provenance_reaches_the_eligible_source(monkeypatch):
    from app.services import regeneration_discovery as discovery

    job = _Job()
    _FakeRepo(
        candidates=[_candidate(
            job,
            notion_homework_page_id="legacy-homework",
            notion_homework_lineage_verified=True,
            lineage_previously_published=True,
        )],
        v1_jobs={(job.toc_entry_id, "uz"): job},
        phase_rows={job.id: _complete_rows()},
    ).install(monkeypatch)

    (source,) = await discovery.list_eligible_sources(
        None, book_ids=None, toc_entry_ids=None, output_languages=None
    )
    assert source.notion_homework_page_id == "legacy-homework"
    assert source.notion_homework_lineage_verified is True
    assert source.lineage_previously_published is True


@pytest.mark.asyncio
async def test_phase_rows_are_fetched_in_one_batched_call(monkeypatch):
    """A 200-lesson discovery must not become 200 phase-row queries."""
    from app.services import regeneration_discovery as discovery

    jobs = [_Job(toc_entry_id=uuid.uuid4()) for _ in range(3)]
    repo = _FakeRepo(
        candidates=[_candidate(j) for j in jobs],
        v1_jobs={(j.toc_entry_id, "uz"): j for j in jobs},
        phase_rows={j.id: _complete_rows() for j in jobs},
    ).install(monkeypatch)

    got = await discovery.list_eligible_sources(
        None, book_ids=None, toc_entry_ids=None, output_languages=None
    )
    assert len(got) == 3
    assert len(repo.phase_row_calls) == 1
    assert sorted(map(str, repo.phase_row_calls[0])) == sorted(str(j.id) for j in jobs)


# ─────────────────────────── Notion preflight ────────────────────────


def _source(**overrides):
    from app.services.regeneration_discovery import EligibleRegenerationSource

    kwargs = dict(
        source_job_id=uuid.uuid4(),
        toc_entry_id=uuid.uuid4(),
        book_id=uuid.uuid4(),
        subject=SUBJECT,
        grade="7",
        output_language="uz",
        source_publication_version=1,
        next_expected_version=2,
        source_is_revision=False,
        book_filename="algebra-7.pdf",
        section_number="1",
        section_title="Kirish",
        chapter_title="I bob",
        page_start=4,
        notion_lesson_page_id=None,
        order_index=0,
    )
    kwargs.update(overrides)
    return EligibleRegenerationSource(**kwargs)


@pytest.fixture
def _no_notion_client(monkeypatch):
    """Any attempt to build a Notion client (let alone call one) fails loudly."""
    from app.services import notion_archive
    from app.services.notion import client as notion_client

    class _Explode:
        def __init__(self, *a, **kw):
            raise AssertionError("preflight must not construct a Notion client")

    monkeypatch.setattr(notion_client, "NotionClientWrapper", _Explode)
    monkeypatch.setattr(notion_archive, "NotionClientWrapper", _Explode)
    monkeypatch.setattr(
        notion_archive,
        "find_or_create",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("preflight must not create a Notion page")
        ),
    )


@pytest.fixture
def _siblings(monkeypatch):
    """`toc_entries.titles_for_subject_grade` stand-in — one row per lesson."""
    from app.repositories import toc_entries as toc_repo

    rows: list[tuple] = []

    async def _titles(session, *, subject, grade):
        return list(rows)

    monkeypatch.setattr(toc_repo, "titles_for_subject_grade", _titles)
    return rows


@pytest.mark.asyncio
async def test_a_known_lesson_page_does_not_excuse_a_missing_language_mapping(
    monkeypatch, _no_notion_client, _siblings
):
    """`toc_entries.notion_lesson_page_id` is ONE language-blind column, owned by
    whichever lineage archived that lesson first.

    The publisher stopped treating it as the parent across languages: it resolves
    the Lesson Topic beneath THIS target's own `{lang}:{subject}|{grade}` subject
    page and refuses non-retryably when that mapping is missing. So a pointer
    stamped by the `uz` lineage says nothing about whether the `ru` one has a
    home, and waving it through here would let the operator pay for a revision
    that can only park.
    """
    from app.config import settings
    from app.services import regeneration_discovery as discovery

    monkeypatch.setattr(
        settings, "notion_subject_pages", {f"{SUBJECT}|7": "page-uz"}, raising=False
    )
    source = _source(output_language="ru", notion_lesson_page_id="uz-lesson-page")

    (failure,) = await discovery.preflight_notion_destinations(None, [source])

    assert failure.reason == discovery.NO_SUBJECT_PAGE_REASON
    assert failure.output_language == "ru"
    assert f"ru:{SUBJECT}|7" in failure.detail


@pytest.mark.asyncio
async def test_preflight_passes_a_known_lesson_page_with_its_own_language_mapping(
    monkeypatch, _no_notion_client, _siblings
):
    """The pointer is not what makes it pass — the subject tree the publisher
    will actually resolve under, `ru:{subject}|{grade}`, is configured."""
    from app.config import settings
    from app.services import regeneration_discovery as discovery

    monkeypatch.setattr(
        settings,
        "notion_subject_pages",
        {f"{SUBJECT}|7": "page-uz", f"ru:{SUBJECT}|7": "page-ru"},
        raising=False,
    )
    source = _source(output_language="ru", notion_lesson_page_id="uz-lesson-page")

    assert await discovery.preflight_notion_destinations(None, [source]) == []


@pytest.mark.asyncio
async def test_a_known_lesson_page_does_not_excuse_a_missing_grade(
    monkeypatch, _no_notion_client, _siblings
):
    """The grade is half the destination key, so a gradeless book is still the
    distinct fix even when the lesson already has a page."""
    from app.config import settings
    from app.services import regeneration_discovery as discovery

    monkeypatch.setattr(
        settings, "notion_subject_pages", {f"{SUBJECT}|7": "page-uz"}, raising=False
    )
    (failure,) = await discovery.preflight_notion_destinations(
        None, [_source(grade=None, notion_lesson_page_id="uz-lesson-page")]
    )
    assert failure.reason == discovery.MISSING_GRADE_REASON


@pytest.mark.asyncio
async def test_preflight_passes_when_the_subject_page_mapping_resolves(
    monkeypatch, _no_notion_client, _siblings
):
    from app.config import settings
    from app.services import regeneration_discovery as discovery

    monkeypatch.setattr(
        settings, "notion_subject_pages", {f"{SUBJECT}|7": "page-7"}, raising=False
    )
    assert await discovery.preflight_notion_destinations(None, [_source()]) == []


@pytest.mark.asyncio
async def test_preflight_returns_every_missing_mapping_together(
    monkeypatch, _no_notion_client, _siblings
):
    """One actionable list, not a first-failure abort: the operator fixes the
    whole configuration once, before any model spend."""
    from app.config import settings
    from app.services import regeneration_discovery as discovery

    monkeypatch.setattr(
        settings, "notion_subject_pages", {f"{SUBJECT}|7": "page-7"}, raising=False
    )
    ok = _source()
    bad_grade = _source(grade="8")
    bad_language = _source(output_language="ru")

    failures = await discovery.preflight_notion_destinations(
        None, [ok, bad_grade, bad_language]
    )

    assert {f.source_job_id for f in failures} == {
        bad_grade.source_job_id,
        bad_language.source_job_id,
    }
    assert all(f.reason == discovery.NO_SUBJECT_PAGE_REASON for f in failures)
    detail = " ".join(f.detail for f in failures)
    assert f"{SUBJECT}|8" in detail
    assert f"ru:{SUBJECT}|7" in detail


@pytest.mark.asyncio
async def test_preflight_reports_a_missing_grade_distinctly(
    monkeypatch, _no_notion_client, _siblings
):
    """`{subject}|{grade}` is the destination key, so a gradeless book is a
    different fix from an unmapped subject page."""
    from app.config import settings
    from app.services import regeneration_discovery as discovery

    monkeypatch.setattr(
        settings, "notion_subject_pages", {f"{SUBJECT}|7": "page-7"}, raising=False
    )
    (failure,) = await discovery.preflight_notion_destinations(
        None, [_source(grade=None)]
    )
    assert failure.reason == discovery.MISSING_GRADE_REASON


@pytest.mark.asyncio
async def test_preflight_carries_the_disambiguated_lesson_title(
    monkeypatch, _no_notion_client, _siblings
):
    """The failure names the page the operator will actually see, using the
    SAME disambiguation the archiver applies (repeated titles get a suffix)."""
    from app.config import settings
    from app.services import notion_archive
    from app.services import regeneration_discovery as discovery

    monkeypatch.setattr(settings, "notion_subject_pages", {}, raising=False)
    source = _source()
    twin_id = uuid.uuid4()
    _siblings.extend(
        [
            ("1", "Kirish", "I bob", 4, source.toc_entry_id),
            ("1", "Kirish", "I bob", 9, twin_id),
        ]
    )

    (failure,) = await discovery.preflight_notion_destinations(None, [source])
    assert failure.lesson_title == notion_archive.resolve_lesson_title(
        source, list(_siblings)
    )
    assert failure.lesson_title == "1 Kirish · p.4"


@pytest.mark.asyncio
async def test_preflight_reads_only_and_touches_no_remote_service(
    monkeypatch, _no_notion_client, _siblings
):
    """No client, no page creation, and no write to our own rows either."""
    from app.config import settings
    from app.repositories import toc_entries as toc_repo
    from app.services import regeneration_discovery as discovery

    monkeypatch.setattr(settings, "notion_subject_pages", {}, raising=False)
    for writer in (
        "set_notion_lesson_page_id",
        "set_notion_homework_page_id",
        "set_notion_archived_job",
    ):
        monkeypatch.setattr(
            toc_repo,
            writer,
            lambda *a, **kw: (_ for _ in ()).throw(
                AssertionError("preflight must not write")
            ),
        )

    failures = await discovery.preflight_notion_destinations(
        None, [_source(), _source(output_language="ru")]
    )
    assert len(failures) == 2
