"""建立业务操作记录和站内消息，保留已有用户、会议室、会话和预约。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operation_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_key", mysql.VARCHAR(64, charset="ascii", collation="ascii_bin"), nullable=False),
        sa.Column("actor_kind", sa.String(16, collation="utf8mb4_0900_as_cs"), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("object_type", sa.String(16, collation="utf8mb4_0900_as_cs"), nullable=False),
        sa.Column("object_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(32, collation="utf8mb4_0900_as_cs"), nullable=False),
        sa.Column("source", sa.String(16, collation="utf8mb4_0900_as_cs"), nullable=False),
        sa.Column("before_status", sa.String(32), nullable=True),
        sa.Column("after_status", sa.String(32), nullable=True),
        sa.Column(
            "result", sa.String(16, collation="utf8mb4_0900_as_cs"),
            server_default=sa.text("'success'"), nullable=False,
        ),
        sa.Column("reason", sa.String(1000), nullable=True),
        sa.Column(
            "created_at", mysql.DATETIME(fsp=6),
            server_default=sa.text("(UTC_TIMESTAMP(6))"), nullable=False,
        ),
        sa.CheckConstraint("CHAR_LENGTH(TRIM(event_key)) > 0", name=op.f("ck_operation_logs_event_key")),
        sa.CheckConstraint("object_id > 0", name=op.f("ck_operation_logs_object_id")),
        sa.CheckConstraint("actor_kind IN ('user', 'system')", name=op.f("ck_operation_logs_actor_kind")),
        sa.CheckConstraint(
            "object_type IN ('user', 'meeting_room', 'reservation')",
            name=op.f("ck_operation_logs_object_type"),
        ),
        sa.CheckConstraint(
            "action IN ('register', 'submit', 'approve', 'reject', 'cancel', 'revoke', "
            "'expire', 'finish', 'remind', 'edit', 'disable', 'enable')",
            name=op.f("ck_operation_logs_action"),
        ),
        sa.CheckConstraint("source IN ('page', 'assistant', 'system')", name=op.f("ck_operation_logs_source")),
        sa.CheckConstraint("result IN ('success', 'failure')", name=op.f("ck_operation_logs_result")),
        sa.CheckConstraint(
            "(actor_kind = 'user' AND actor_user_id IS NOT NULL AND source IN ('page', 'assistant')) "
            "OR (actor_kind = 'system' AND actor_user_id IS NULL AND source = 'system')",
            name=op.f("ck_operation_logs_actor_source"),
        ),
        sa.CheckConstraint(
            "before_status IS NULL OR CHAR_LENGTH(TRIM(before_status)) > 0",
            name=op.f("ck_operation_logs_before_status"),
        ),
        sa.CheckConstraint(
            "after_status IS NULL OR CHAR_LENGTH(TRIM(after_status)) > 0",
            name=op.f("ck_operation_logs_after_status"),
        ),
        sa.CheckConstraint(
            "reason IS NULL OR CHAR_LENGTH(TRIM(reason)) > 0",
            name=op.f("ck_operation_logs_reason"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], ondelete="RESTRICT",
            name=op.f("fk_operation_logs_actor_user_id_users"),
        ),
        sa.UniqueConstraint("event_key", name=op.f("uq_operation_logs_event_key")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operation_logs")),
        mysql_engine="InnoDB", mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index(
        op.f("ix_operation_logs_object_time"), "operation_logs",
        ["object_type", "object_id", "created_at"],
    )
    op.create_index(
        op.f("ix_operation_logs_actor_time"), "operation_logs", ["actor_user_id", "created_at"],
    )
    op.create_index(op.f("ix_operation_logs_created_at"), "operation_logs", ["created_at"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column("recipient_user_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("content", sa.String(1000), nullable=False),
        sa.Column(
            "created_at", mysql.DATETIME(fsp=6),
            server_default=sa.text("(UTC_TIMESTAMP(6))"), nullable=False,
        ),
        sa.Column("read_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.CheckConstraint("CHAR_LENGTH(TRIM(title)) > 0", name=op.f("ck_notifications_title")),
        sa.CheckConstraint("CHAR_LENGTH(TRIM(content)) > 0", name=op.f("ck_notifications_content")),
        sa.CheckConstraint("read_at IS NULL OR read_at >= created_at", name=op.f("ck_notifications_read_at")),
        sa.ForeignKeyConstraint(
            ["event_id"], ["operation_logs.id"], ondelete="RESTRICT",
            name=op.f("fk_notifications_event_id_operation_logs"),
        ),
        sa.ForeignKeyConstraint(
            ["recipient_user_id"], ["users.id"], ondelete="RESTRICT",
            name=op.f("fk_notifications_recipient_user_id_users"),
        ),
        sa.UniqueConstraint(
            "event_id", "recipient_user_id", name=op.f("uq_notifications_event_id_recipient_user_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
        mysql_engine="InnoDB", mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index(
        op.f("ix_notifications_recipient_read_time"), "notifications",
        ["recipient_user_id", "read_at", "created_at"],
    )


def downgrade() -> None:
    # 仅在隔离测试实例验证；回退会删除这两张表的数据，不删除其引用的旧业务表。
    op.drop_table("notifications")
    op.drop_table("operation_logs")
