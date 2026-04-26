"""add archived to client

Revision ID: f2b8c3d91a11
Revises: e1c0b7a2d9f1
Create Date: 2026-04-26

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f2b8c3d91a11"
down_revision = "e1c0b7a2d9f1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("client", schema=None) as batch_op:
        batch_op.add_column(sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()))



def downgrade():
    with op.batch_alter_table("client", schema=None) as batch_op:
        batch_op.drop_column("archived")
