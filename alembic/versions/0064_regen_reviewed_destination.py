"""guided regeneration: campaign publication version + reviewed Notion destination

Additive only — six nullable columns and two named CHECKs, no data migration.

`regeneration_campaigns.publication_version` is the version the WHOLE campaign
publishes ("Homework V2"). It is NOT the same column as
`regeneration_targets.publication_version`, which 0063 added as the per-lesson
allocation guarded by `uq_regeneration_targets_publication_version`; the two
tables simply share a name. The downgrade below drops only the campaign one.

The five `regeneration_targets` columns are the destination the operator
approved in the guided wizard: `reuse` names an existing page by id, `create`
names a page that does not exist yet and so carries a title instead. They are
frozen at target creation and read at publication time, which is what stops a
publisher re-deriving a destination from a live Notion search and writing
somewhere nobody approved.

Every new column is NULLABLE on purpose. Campaigns and targets created before
the wizard have no version and no reviewed decision, and back-filling one would
invent a publication that never happened. Making them mandatory on NEW
service-created campaigns is a later task's job, in the service layer.

In `ck_regeneration_targets_notion_parent_decision` the load-bearing part is
that every DECIDING comparison is `IS NOT DISTINCT FROM` rather than `=`. SQL
is three-valued and a CHECK constraint is SATISFIED by UNKNOWN, so the same
rule spelled with bare `notion_container_policy = 'reuse'` comparisons
evaluates to NULL — and is therefore ACCEPTED — for precisely the half-filled
shapes it exists to refuse: a `reuse` lesson policy with no container policy
beside it, or every policy NULL with a reviewed title set. Total comparisons
turn those NULLs into FALSE. Same trap, same fix as
`ck_homework_jobs_revision_session_limit_strategy` in 0063.

The leading `notion_parent_policy IS NOT NULL AND notion_parent_policy IN
('reuse','create')` is NOT what closes that hole. It is redundant
defence-in-depth, kept because it names the legal policy set where a reader
looks for it: the `notion_parent_policy IS NOT DISTINCT FROM` branches further
down already force that column into {'reuse','create'} and already yield FALSE
— not UNKNOWN — when it is NULL, so the prefix can never turn a would-be-TRUE
row FALSE. Swept exhaustively over 1,764 combinations (policies in
{NULL,'reuse','create','adopt','','Reuse',' reuse'} x page ids in
{NULL,'p1',''} x titles in {NULL,'T','','   '}), in Python and again in
PostgreSQL against this exact rule text: 22 rows accepted with the clause, the
same 22 without it, zero UNKNOWN results either way. Its own `IS NOT NULL`
exists only so that its `IN` cannot evaluate to UNKNOWN; it neither widens nor
narrows the rule.

`ck_regeneration_campaigns_publication_version` needs no such care: `NULL IS
NULL` is TRUE, so its first disjunct already decides the NULL case.

Revision ID: 0064_regen_reviewed_destination
Revises: 0063_regeneration_campaigns
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0064_regen_reviewed_destination"
down_revision: Union[str, Sequence[str], None] = "0063_regeneration_campaigns"
branch_labels = None
depends_on = None

# Spelled out here rather than imported from `app.models.regeneration_target`:
# a migration is a frozen record of the DDL that was applied, so it must not
# change when the model does.
_CAMPAIGN_VERSION_RULE = "publication_version IS NULL OR publication_version >= 2"

_DESTINATION_RULE = """(notion_parent_policy IS NULL
 AND notion_container_policy IS NULL
 AND reviewed_notion_container_page_id IS NULL
 AND reviewed_notion_lesson_page_id IS NULL
 AND reviewed_notion_lesson_title IS NULL)
OR
(notion_parent_policy IS NOT NULL
 AND notion_parent_policy IN ('reuse','create')
 AND reviewed_notion_lesson_title IS NOT NULL
 AND (
   (notion_container_policy IS NOT DISTINCT FROM 'reuse'
    AND reviewed_notion_container_page_id IS NOT NULL)
   OR
   (notion_container_policy IS NOT DISTINCT FROM 'create'
    AND reviewed_notion_container_page_id IS NULL)
 )
 AND (
   (notion_parent_policy IS NOT DISTINCT FROM 'reuse'
    AND notion_container_policy IS NOT DISTINCT FROM 'reuse'
    AND reviewed_notion_lesson_page_id IS NOT NULL)
   OR
   (notion_parent_policy IS NOT DISTINCT FROM 'create'
    AND reviewed_notion_lesson_page_id IS NULL)
 ))"""


def upgrade() -> None:
    # ─── the version the campaign publishes ───────────────────────────────
    op.add_column(
        "regeneration_campaigns",
        sa.Column("publication_version", sa.Integer(), nullable=True),
    )
    # Logical V1 is the pre-existing `Homework` page, which no campaign
    # produced, so the lowest number a campaign may claim is 2.
    op.create_check_constraint(
        "ck_regeneration_campaigns_publication_version",
        "regeneration_campaigns",
        _CAMPAIGN_VERSION_RULE,
    )

    # ─── the destination the operator approved ────────────────────────────
    for column, type_ in (
        ("notion_container_policy", sa.String(length=16)),
        ("reviewed_notion_container_page_id", sa.String(length=128)),
        ("notion_parent_policy", sa.String(length=16)),
        ("reviewed_notion_lesson_page_id", sa.String(length=128)),
        ("reviewed_notion_lesson_title", sa.Text()),
    ):
        op.add_column(
            "regeneration_targets", sa.Column(column, type_, nullable=True)
        )
    # Either NO reviewed destination (a pre-wizard target) or a WHOLE coherent
    # one. See the module docstring for why every comparison is total.
    op.create_check_constraint(
        "ck_regeneration_targets_notion_parent_decision",
        "regeneration_targets",
        _DESTINATION_RULE,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_regeneration_targets_notion_parent_decision",
        "regeneration_targets",
        type_="check",
    )
    for column in (
        "reviewed_notion_lesson_title",
        "reviewed_notion_lesson_page_id",
        "notion_parent_policy",
        "reviewed_notion_container_page_id",
        "notion_container_policy",
    ):
        op.drop_column("regeneration_targets", column)

    op.drop_constraint(
        "ck_regeneration_campaigns_publication_version",
        "regeneration_campaigns",
        type_="check",
    )
    # The CAMPAIGN column only — `regeneration_targets.publication_version` is
    # 0063's and must survive this downgrade untouched.
    op.drop_column("regeneration_campaigns", "publication_version")
