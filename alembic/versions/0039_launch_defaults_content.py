"""launch_defaults: add content_provider/model/transport"""
from alembic import op
import sqlalchemy as sa

revision = "0039_launch_defaults_content"
down_revision = "0038_output_language"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("launch_defaults", sa.Column("content_provider", sa.String(32), nullable=True))
    op.add_column("launch_defaults", sa.Column("content_model", sa.String(128), nullable=True))
    op.add_column("launch_defaults", sa.Column("content_transport", sa.String(16), nullable=True))
    op.execute(
        "UPDATE launch_defaults SET content_provider='gemini', "
        "content_model='gemini-2.5-pro', content_transport='api' WHERE id=1"
    )

def downgrade() -> None:
    op.drop_column("launch_defaults", "content_transport")
    op.drop_column("launch_defaults", "content_model")
    op.drop_column("launch_defaults", "content_provider")
