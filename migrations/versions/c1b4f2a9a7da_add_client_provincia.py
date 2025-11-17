"""add provincia to client

Revision ID: c1b4f2a9a7da
Revises: ab7ebab36a39
Create Date: 2025-11-17 17:57:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c1b4f2a9a7da'
down_revision = 'ab7ebab36a39'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('client', schema=None) as batch_op:
        batch_op.add_column(sa.Column('provincia', sa.String(length=80), nullable=True))


def downgrade():
    with op.batch_alter_table('client', schema=None) as batch_op:
        batch_op.drop_column('provincia')
