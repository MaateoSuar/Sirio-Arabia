"""add client_document table and new client fields

Revision ID: 3a2f1c7b4d90
Revises: db2836e3da2e
Create Date: 2025-11-17 17:09:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '3a2f1c7b4d90'
down_revision = 'db2836e3da2e'
branch_labels = None
depends_on = None


def upgrade():
    # Add new columns to client
    with op.batch_alter_table('client', schema=None) as batch_op:
        batch_op.add_column(sa.Column('direccion_principal', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('transporte_recomendado', sa.String(length=120), nullable=True))

    # Create client_document table
    op.create_table(
        'client_document',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('filepath', sa.String(length=500), nullable=False),
        sa.Column('uploaded_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade():
    # Drop client_document table
    op.drop_table('client_document')

    # Remove columns from client
    with op.batch_alter_table('client', schema=None) as batch_op:
        batch_op.drop_column('transporte_recomendado')
        batch_op.drop_column('direccion_principal')
