"""add plazo_pago_dias to client_company_link

Revision ID: 7d1c9f4e0a12
Revises: 20946727b627
Create Date: 2025-12-31 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7d1c9f4e0a12'
down_revision = '20946727b627'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('client_company_link', schema=None) as batch_op:
        batch_op.add_column(sa.Column('plazo_pago_dias', sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table('client_company_link', schema=None) as batch_op:
        batch_op.drop_column('plazo_pago_dias')
