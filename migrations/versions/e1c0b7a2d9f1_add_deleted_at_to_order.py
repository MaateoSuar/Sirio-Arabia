"""add deleted_at to order

Revision ID: e1c0b7a2d9f1
Revises: d4c2e8a1f0ab
Create Date: 2026-01-22

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e1c0b7a2d9f1"
down_revision = "d4c2e8a1f0ab"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("order", schema=None) as batch_op:
        batch_op.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("order", schema=None) as batch_op:
        batch_op.drop_column("deleted_at")
