"""新增助手确认凭证与执行收据；保留既有业务数据。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    ascii43 = mysql.VARCHAR(43, charset="ascii", collation="ascii_bin")
    ascii64 = mysql.VARCHAR(64, charset="ascii", collation="ascii_bin")
    op.create_table(
        "assistant_confirmations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("token", ascii43, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("session_token_hash", ascii64, nullable=False),
        sa.Column("session_version", sa.BigInteger(), nullable=False),
        sa.Column("conversation_id", mysql.VARCHAR(128, charset="ascii", collation="ascii_bin"), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(16, collation="utf8mb4_0900_as_cs"), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_hash", ascii64, nullable=False),
        sa.Column("state", sa.String(16, collation="utf8mb4_0900_as_cs"), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("consumed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("result", sa.JSON(none_as_null=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT", name=op.f("fk_assistant_confirmations_user_id_users")),
        sa.UniqueConstraint("token", name=op.f("uq_assistant_confirmations_token")),
        sa.UniqueConstraint("user_id", "session_token_hash", "conversation_id", "revision", "payload_hash",
                            name="uq_assistant_confirmations_plan"),
        sa.CheckConstraint("revision > 0", name=op.f("ck_assistant_confirmations_revision")),
        sa.CheckConstraint("session_version > 0", name=op.f("ck_assistant_confirmations_session_version")),
        sa.CheckConstraint("action = 'submit'", name=op.f("ck_assistant_confirmations_action")),
        sa.CheckConstraint("state IN ('pending','consumed','superseded','expired')", name=op.f("ck_assistant_confirmations_state")),
        sa.CheckConstraint("expires_at > created_at", name=op.f("ck_assistant_confirmations_expires_at")),
        sa.CheckConstraint("JSON_TYPE(payload) = 'OBJECT'", name=op.f("ck_assistant_confirmations_payload_object")),
        sa.CheckConstraint("(state = 'consumed' AND result IS NOT NULL) OR (state <> 'consumed' AND result IS NULL)",
                           name=op.f("ck_assistant_confirmations_result_state")),
        mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("ix_assistant_confirmations_conversation", "assistant_confirmations",
                    ["user_id", "session_token_hash", "conversation_id"])


def downgrade() -> None:
    op.drop_table("assistant_confirmations")
