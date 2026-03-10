"""add users and ownership

Revision ID: d4c2e8a1f0ab
Revises: b2a1c0d9e8f7
Create Date: 2026-01-13

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d4c2e8a1f0ab"
down_revision = "b2a1c0d9e8f7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "app_user",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )

    with op.batch_alter_table("client", schema=None) as batch_op:
        batch_op.add_column(sa.Column("owner_user_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_client_owner_user", "app_user", ["owner_user_id"], ["id"])

    with op.batch_alter_table("order", schema=None) as batch_op:
        batch_op.add_column(sa.Column("owner_user_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_order_owner_user", "app_user", ["owner_user_id"], ["id"])

    with op.batch_alter_table("order_draft", schema=None) as batch_op:
        batch_op.add_column(sa.Column("owner_user_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_order_draft_owner_user", "app_user", ["owner_user_id"], ["id"])

    with op.batch_alter_table("collection", schema=None) as batch_op:
        batch_op.add_column(sa.Column("owner_user_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_collection_owner_user", "app_user", ["owner_user_id"], ["id"])

    with op.batch_alter_table("collection_payment", schema=None) as batch_op:
        batch_op.add_column(sa.Column("owner_user_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_collection_payment_owner_user", "app_user", ["owner_user_id"], ["id"])

    with op.batch_alter_table("client_document", schema=None) as batch_op:
        batch_op.add_column(sa.Column("owner_user_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_client_document_owner_user", "app_user", ["owner_user_id"], ["id"])

    with op.batch_alter_table("client_alert_state", schema=None) as batch_op:
        batch_op.add_column(sa.Column("owner_user_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_client_alert_state_owner_user", "app_user", ["owner_user_id"], ["id"])

    # Seed users and assign existing data to SIRIO
    from werkzeug.security import generate_password_hash

    conn = op.get_bind()

    users = [
        ("SIRIO", "Sirio76", True),
        ("RICARDO", "Santiago76", False),
        ("EDGARDO", "Edgar76", False),
        ("PEPE", "Centro76", False),
    ]

    for username, pwd, is_admin in users:
        try:
            existing = conn.execute(sa.text("SELECT id FROM app_user WHERE username = :u"), {"u": username}).scalar()
        except Exception:
            existing = None
        if existing is None:
            conn.execute(
                sa.text(
                    "INSERT INTO app_user (username, password_hash, is_admin, created_at) "
                    "VALUES (:u, :p, :a, CURRENT_TIMESTAMP)"
                ),
                {"u": username, "p": generate_password_hash(pwd), "a": True if is_admin else False},
            )

    sirio_id = conn.execute(sa.text("SELECT id FROM app_user WHERE username='SIRIO'"))
    sirio_id = sirio_id.scalar()

    if sirio_id is not None:
        conn.execute(sa.text("UPDATE client SET owner_user_id = :uid WHERE owner_user_id IS NULL"), {"uid": sirio_id})
        conn.execute(sa.text('UPDATE "order" SET owner_user_id = :uid WHERE owner_user_id IS NULL'), {"uid": sirio_id})
        conn.execute(sa.text("UPDATE order_draft SET owner_user_id = :uid WHERE owner_user_id IS NULL"), {"uid": sirio_id})

        # Derivar ownership donde sea posible; fallback a SIRIO
        try:
            conn.execute(
                sa.text(
                    "UPDATE collection SET owner_user_id = "
                    "(SELECT owner_user_id FROM \"order\" o WHERE o.id = collection.order_id) "
                    "WHERE owner_user_id IS NULL"
                )
            )
        except Exception:
            pass
        conn.execute(sa.text("UPDATE collection SET owner_user_id = :uid WHERE owner_user_id IS NULL"), {"uid": sirio_id})

        try:
            conn.execute(
                sa.text(
                    "UPDATE collection_payment SET owner_user_id = "
                    "(SELECT owner_user_id FROM \"order\" o WHERE o.id = collection_payment.order_id) "
                    "WHERE owner_user_id IS NULL"
                )
            )
        except Exception:
            pass
        conn.execute(sa.text("UPDATE collection_payment SET owner_user_id = :uid WHERE owner_user_id IS NULL"), {"uid": sirio_id})

        try:
            conn.execute(
                sa.text(
                    "UPDATE client_document SET owner_user_id = "
                    "(SELECT owner_user_id FROM client c WHERE c.id = client_document.client_id) "
                    "WHERE owner_user_id IS NULL"
                )
            )
        except Exception:
            pass
        conn.execute(sa.text("UPDATE client_document SET owner_user_id = :uid WHERE owner_user_id IS NULL"), {"uid": sirio_id})

        try:
            conn.execute(
                sa.text(
                    "UPDATE client_alert_state SET owner_user_id = "
                    "(SELECT owner_user_id FROM \"order\" o WHERE o.id = client_alert_state.order_id) "
                    "WHERE owner_user_id IS NULL"
                )
            )
        except Exception:
            pass
        conn.execute(sa.text("UPDATE client_alert_state SET owner_user_id = :uid WHERE owner_user_id IS NULL"), {"uid": sirio_id})


def downgrade():
    conn = op.get_bind()
    try:
        conn.execute(sa.text("DELETE FROM app_user WHERE username IN ('SIRIO','RICARDO','EDGARDO','PEPE')"))
    except Exception:
        pass

    with op.batch_alter_table("order_draft", schema=None) as batch_op:
        batch_op.drop_constraint("fk_order_draft_owner_user", type_="foreignkey")
        batch_op.drop_column("owner_user_id")

    with op.batch_alter_table("client_alert_state", schema=None) as batch_op:
        batch_op.drop_constraint("fk_client_alert_state_owner_user", type_="foreignkey")
        batch_op.drop_column("owner_user_id")

    with op.batch_alter_table("client_document", schema=None) as batch_op:
        batch_op.drop_constraint("fk_client_document_owner_user", type_="foreignkey")
        batch_op.drop_column("owner_user_id")

    with op.batch_alter_table("collection_payment", schema=None) as batch_op:
        batch_op.drop_constraint("fk_collection_payment_owner_user", type_="foreignkey")
        batch_op.drop_column("owner_user_id")

    with op.batch_alter_table("collection", schema=None) as batch_op:
        batch_op.drop_constraint("fk_collection_owner_user", type_="foreignkey")
        batch_op.drop_column("owner_user_id")

    with op.batch_alter_table("order", schema=None) as batch_op:
        batch_op.drop_constraint("fk_order_owner_user", type_="foreignkey")
        batch_op.drop_column("owner_user_id")

    with op.batch_alter_table("client", schema=None) as batch_op:
        batch_op.drop_constraint("fk_client_owner_user", type_="foreignkey")
        batch_op.drop_column("owner_user_id")

    op.drop_table("app_user")
