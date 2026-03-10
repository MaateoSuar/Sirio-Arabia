"""add forma_pago_detalle to order/logistics/collection/draft

Revision ID: b2a1c0d9e8f7
Revises: 9f3c2a1b7c9d
Create Date: 2026-01-13

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b2a1c0d9e8f7"
down_revision = "9f3c2a1b7c9d"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("order", schema=None) as batch_op:
        batch_op.add_column(sa.Column("forma_pago_detalle", sa.String(length=32), nullable=True))

    with op.batch_alter_table("logistics_status", schema=None) as batch_op:
        batch_op.add_column(sa.Column("forma_pago_detalle", sa.String(length=32), nullable=True))

    with op.batch_alter_table("collection", schema=None) as batch_op:
        batch_op.add_column(sa.Column("forma_pago_detalle", sa.String(length=32), nullable=True))

    with op.batch_alter_table("order_draft", schema=None) as batch_op:
        batch_op.add_column(sa.Column("forma_pago_detalle", sa.String(length=32), nullable=True))


def downgrade():
    with op.batch_alter_table("order_draft", schema=None) as batch_op:
        batch_op.drop_column("forma_pago_detalle")

    with op.batch_alter_table("collection", schema=None) as batch_op:
        batch_op.drop_column("forma_pago_detalle")

    with op.batch_alter_table("logistics_status", schema=None) as batch_op:
        batch_op.drop_column("forma_pago_detalle")

    with op.batch_alter_table("order", schema=None) as batch_op:
        batch_op.drop_column("forma_pago_detalle")
