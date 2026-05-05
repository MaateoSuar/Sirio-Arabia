"""add archived to company

Revision ID: a7d3b1e8c4f2
Revises: f2b8c3d91a11
Create Date: 2026-05-04

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a7d3b1e8c4f2"
down_revision = "f2b8c3d91a11"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("company", schema=None) as batch_op:
        batch_op.add_column(sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()))



def downgrade():
    with op.batch_alter_table("company", schema=None) as batch_op:
        batch_op.drop_column("archived")
