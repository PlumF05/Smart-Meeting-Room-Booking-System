"""建立账号与会议室基础表，不插入演示账号或会议室。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(100), nullable=False),
        sa.Column(
            "role", sa.String(16, collation="utf8mb4_0900_as_cs"),
            server_default=sa.text("'user'"), nullable=False,
        ),
        sa.Column(
            "identity_type", sa.String(16, collation="utf8mb4_0900_as_cs"),
            nullable=True,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "session_version", sa.Integer(), server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "created_at", mysql.DATETIME(fsp=6),
            server_default=sa.text("(UTC_TIMESTAMP(6))"), nullable=False,
        ),
        sa.CheckConstraint("role IN ('user', 'admin')", name=op.f("ck_users_role")),
        sa.CheckConstraint(
            "(identity_type IS NULL OR identity_type IN ('teacher', 'student')) "
            "AND (role <> 'user' OR identity_type IS NOT NULL)",
            name=op.f("ck_users_identity_type"),
        ),
        sa.CheckConstraint("session_version > 0", name=op.f("ck_users_session_version")),
        sa.CheckConstraint("is_active IN (0, 1)", name=op.f("ck_users_is_active")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("username", name=op.f("uq_users_username")),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_table(
        "meeting_rooms",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("location", sa.String(255), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column(
            "equipment", sa.JSON(), server_default=sa.text("(JSON_ARRAY())"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at", mysql.DATETIME(fsp=6),
            server_default=sa.text("(UTC_TIMESTAMP(6))"), nullable=False,
        ),
        sa.CheckConstraint("capacity > 0", name=op.f("ck_meeting_rooms_capacity")),
        sa.CheckConstraint(
            "JSON_TYPE(equipment) = 'ARRAY'",
            name=op.f("ck_meeting_rooms_equipment_array"),
        ),
        sa.CheckConstraint("is_active IN (0, 1)", name=op.f("ck_meeting_rooms_is_active")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_meeting_rooms")),
        sa.UniqueConstraint("name", name=op.f("uq_meeting_rooms_name")),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )


def downgrade() -> None:
    # 会删除这两张表及其数据；本轮仅在隔离的临时MySQL实例中验证撤回。
    op.drop_table("meeting_rooms")
    op.drop_table("users")
