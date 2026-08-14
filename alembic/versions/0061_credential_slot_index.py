"""credential_slots.slot_index: replace the fleet-wide advisory lock with a
per-slot unique constraint.

Revision ID: 0061_credential_slot_index
Revises: 0060_job_reclaims

Why: `credential_limiter.acquire` serialized EVERY slot acquisition fleet-wide
through one `pg_advisory_xact_lock(hashtext(credential))` — every host shares
one Gemini key, so every host hashed to the same lock. Measured in production:
75 connections blocked on that single lock, longest wait 822s, while only 54 of
a 900-slot ceiling were in use.

The fix shards the mutual exclusion down to ONE LOCK PER SLOT, and implements
each of those locks as a unique-index entry instead of an advisory lock:
`UNIQUE(credential, slot_index)` makes it physically impossible for more than
`limit` rows to exist for a credential in the index range `[0, limit)`, so the
ceiling is enforced by the database's own uniqueness guarantee rather than by a
count-then-insert critical section that has to be held across client round
trips. See `app/services/credential_limiter.py` for the acquire statement.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0061_credential_slot_index"
down_revision: Union[str, Sequence[str], None] = "0060_job_reclaims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable first, so live in-flight slots survive the deploy: a row here
    # means "an api call is running right now", and deleting them would
    # under-count real concurrency until they aged out.
    op.add_column(
        "credential_slots", sa.Column("slot_index", sa.Integer(), nullable=True)
    )
    # Backfill densely from 0 per credential. row_number() is unique inside
    # each partition, so the unique index below can never fail on existing
    # rows. Any row that lands at an index >= the credential's current limit
    # (possible only if the ceiling was lowered) simply drains as its call
    # finishes — acquire()'s own fresh-count guard refuses new admissions
    # while those rows are still live.
    op.execute(
        """
        UPDATE credential_slots AS c
           SET slot_index = r.rn
          FROM (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY credential ORDER BY acquired_at, id
                       ) - 1 AS rn
                  FROM credential_slots
               ) AS r
         WHERE c.id = r.id
        """
    )
    # DELIBERATELY LEFT NULLABLE — this is the "expand" half of an expand/contract
    # deploy, and making it NOT NULL here is a fleet outage.
    #
    # The fleet auto-pulls (`git pull --ff-only` every supervisor loop), so there is
    # always a window where the DB is migrated and some workers still run the old
    # sha. The OLD limiter inserts `(credential, pc_id)` with NO slot_index
    # (`credential_limiter.py` at d27465f), so a NOT NULL column fails it outright:
    #     null value in column "slot_index" ... violates not-null constraint
    # Verified by replaying that exact INSERT against a 0062-migrated schema.
    # It would break every host whose limiter is live (CG != 0) for the whole
    # rollout — and a host with no CG line at all defaults to 8, i.e. live.
    #
    # Nullable makes the two versions interoperate correctly, in both directions:
    #   - old code inserts NULL. Postgres treats NULLs as DISTINCT in a unique
    #     index, so any number of them coexist and none of them occupies a
    #     numbered slot (`s.slot_index = g.i` never matches NULL).
    #   - new code inserts real indices and is still bounded by the unique index.
    #   - the ceiling stays honest for BOTH: _ACQUIRE_SQL's `count(*)` filters only
    #     on credential + freshness, so a NULL row from an old worker still counts
    #     against the limit. It is a real in-flight call and should.
    #
    # The "contract" half — ALTER ... SET NOT NULL — belongs in a LATER migration,
    # applied only once no worker is on a pre-0061 sha.
    op.create_index(
        "uq_credential_slots_credential_slot_index",
        "credential_slots",
        ["credential", "slot_index"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_credential_slots_credential_slot_index", table_name="credential_slots"
    )
    op.drop_column("credential_slots", "slot_index")
