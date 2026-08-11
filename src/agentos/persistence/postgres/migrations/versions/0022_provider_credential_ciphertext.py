"""separate encrypted provider credential storage from the legacy column"""
from alembic import op
import sqlalchemy as sa

revision = "0022_provider_ciphertext"
down_revision = "0021_agentic_collaboration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("provider_configurations", sa.Column("api_key_ciphertext", sa.String(8192), nullable=True))
    with op.batch_alter_table("provider_configurations") as batch:
        batch.alter_column("api_key", existing_type=sa.String(4096), nullable=True)
    op.execute(sa.text("UPDATE provider_configurations SET api_key_ciphertext = api_key, api_key = NULL WHERE api_key LIKE 'enc:v1:%'"))


def downgrade() -> None:
    op.drop_column("provider_configurations", "api_key_ciphertext")
