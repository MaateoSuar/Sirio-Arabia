"""add performance indexes

Revision ID: 9f3c2a1b7c9d
Revises: c8b1f0d2a1e3
Create Date: 2026-01-07

"""

from alembic import op


def _is_postgres():
    try:
        ctx = op.get_context()
        return ctx and ctx.dialect and ctx.dialect.name == "postgresql"
    except Exception:
        return False


def _create_idx(name: str, table: str, cols):
    if _is_postgres():
        cols_sql = ", ".join([f'"{c}"' for c in cols])
        with op.get_context().autocommit_block():
            op.execute(f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{name}" ON "{table}" ({cols_sql})')
    else:
        op.create_index(name, table, cols, unique=False)


def _drop_idx(name: str, table: str):
    if _is_postgres():
        with op.get_context().autocommit_block():
            op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{name}"')
    else:
        op.drop_index(name, table_name=table)


# revision identifiers, used by Alembic.
revision = "9f3c2a1b7c9d"
down_revision = "c8b1f0d2a1e3"
branch_labels = None
depends_on = None


def upgrade():
    _create_idx("ix_client_apellido", "client", ["apellido"])
    _create_idx("ix_client_nombre", "client", ["nombre"])
    _create_idx("ix_client_provincia", "client", ["provincia"])

    _create_idx("ix_company_marca", "company", ["marca"])

    _create_idx("ix_order_created_at", "order", ["created_at"])
    _create_idx("ix_order_client_id", "order", ["client_id"])
    _create_idx("ix_order_company_id", "order", ["company_id"])
    _create_idx("ix_order_client_company_created_at", "order", ["client_id", "company_id", "created_at"])

    _create_idx("ix_logistics_status_entrega_estimada", "logistics_status", ["fecha_entrega_estimada"])
    _create_idx("ix_logistics_status_entrega_efectiva", "logistics_status", ["fecha_entrega_efectiva"])

    _create_idx("ix_collection_pago_estimada", "collection", ["fecha_pago_estimada"])
    _create_idx("ix_collection_cobro_efectiva", "collection", ["fecha_cobro_efectiva"])

    _create_idx("ix_client_company_link_status", "client_company_link", ["status"])

    _create_idx("ix_client_alert_state_client_id", "client_alert_state", ["client_id"])
    _create_idx("ix_client_alert_state_snoozed_until", "client_alert_state", ["snoozed_until"])


def downgrade():
    _drop_idx("ix_client_alert_state_snoozed_until", "client_alert_state")
    _drop_idx("ix_client_alert_state_client_id", "client_alert_state")

    _drop_idx("ix_client_company_link_status", "client_company_link")

    _drop_idx("ix_collection_cobro_efectiva", "collection")
    _drop_idx("ix_collection_pago_estimada", "collection")

    _drop_idx("ix_logistics_status_entrega_efectiva", "logistics_status")
    _drop_idx("ix_logistics_status_entrega_estimada", "logistics_status")

    _drop_idx("ix_order_client_company_created_at", "order")
    _drop_idx("ix_order_company_id", "order")
    _drop_idx("ix_order_client_id", "order")
    _drop_idx("ix_order_created_at", "order")

    _drop_idx("ix_company_marca", "company")

    _drop_idx("ix_client_provincia", "client")
    _drop_idx("ix_client_nombre", "client")
    _drop_idx("ix_client_apellido", "client")
