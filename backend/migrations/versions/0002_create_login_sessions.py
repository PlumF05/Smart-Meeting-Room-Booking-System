"""建立服务器端登录会话表，保留现有账号和会议室。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_sessions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("token_hash", mysql.VARCHAR(64, charset="ascii", collation="ascii_bin"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("csrf_token", mysql.VARCHAR(43, charset="ascii", collation="ascii_bin"), nullable=False),
        sa.Column("session_version", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", mysql.DATETIME(fsp=6),
            server_default=sa.text("(UTC_TIMESTAMP(6))"), nullable=False,
        ),
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("revoked_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.CheckConstraint("session_version > 0", name=op.f("ck_login_sessions_session_version")),
        sa.CheckConstraint("expires_at > created_at", name=op.f("ck_login_sessions_expires_at")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="RESTRICT",
            name=op.f("fk_login_sessions_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_login_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_login_sessions_token_hash")),
        mysql_engine="InnoDB", mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index(op.f("ix_login_sessions_user_id"), "login_sessions", ["user_id"])


def downgrade() -> None:
    op.drop_table("login_sessions")
