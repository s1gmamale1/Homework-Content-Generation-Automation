"""launch_defaults: flip singleton row to the 3.x-flash target tuple
(content=gemini-3.6-flash, extract=gemini-3.5-flash-lite, judge=gemini-3.5-flash,
solver=gemini-3.1-pro-preview, all providers gemini, all 5 transports 'api').

Deliberately UNCONDITIONAL — alembic runs each migration exactly once, so there
is no risk of clobbering an operator's later manual edit on replay. This single
UPDATE has to correctly land on BOTH starting states seen in the fleet:
  - a fresh DB seeded through 0037/0039/0043 (content=gemini-2.5-pro,
    extract/judge_transport='inherit', toc_transport='cli')
  - the current PROD row (content=gemini-3-flash-preview, extract/judge=
    gemini-2.5-flash, all transports already 'api')
Both converge on the same target tuple after this migration.

Revision ID: 0051_launch_defaults_3x
Revises: 0050_phase_output_structured
"""
from alembic import op

revision = "0051_launch_defaults_3x"
down_revision = "0050_phase_output_structured"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE launch_defaults
           SET content_provider = 'gemini',
               content_model    = 'gemini-3.6-flash',
               content_transport = 'api',
               extract_provider = 'gemini',
               extract_model    = 'gemini-3.5-flash-lite',
               extract_transport = 'api',
               judge_provider   = 'gemini',
               judge_model      = 'gemini-3.5-flash',
               judge_transport  = 'api',
               solver_provider  = 'gemini',
               solver_model     = 'gemini-3.1-pro-preview',
               solver_transport = 'api',
               toc_transport    = 'api',
               updated_at       = now()
         WHERE id = 1
        """
    )


def downgrade() -> None:
    # Restore the exact pre-migration PROD tuple.
    op.execute(
        """
        UPDATE launch_defaults
           SET content_provider = 'gemini',
               content_model    = 'gemini-3-flash-preview',
               content_transport = 'api',
               extract_provider = 'gemini',
               extract_model    = 'gemini-2.5-flash',
               extract_transport = 'api',
               judge_provider   = 'gemini',
               judge_model      = 'gemini-2.5-flash',
               judge_transport  = 'api',
               solver_provider  = 'gemini',
               solver_model     = 'gemini-3.1-pro-preview',
               solver_transport = 'api',
               toc_transport    = 'api',
               updated_at       = now()
         WHERE id = 1
        """
    )
