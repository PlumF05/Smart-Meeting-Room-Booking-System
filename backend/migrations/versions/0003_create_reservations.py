"""建立预约记录及提交时房间快照，保留现有账号、会议室与登录会话。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reservations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("room_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("purpose", sa.String(1000), nullable=False),
        sa.Column("attendees", sa.Integer(), nullable=False),
        # DATETIME不保存时区，业务服务统一转换为UTC后再写入。
        sa.Column("starts_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("ends_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column(
            "status", sa.String(16, collation="utf8mb4_0900_as_cs"),
            server_default=sa.text("'pending'"), nullable=False,
        ),
        sa.Column(
            "source", sa.String(16, collation="utf8mb4_0900_as_cs"),
            server_default=sa.text("'page'"), nullable=False,
        ),
        sa.Column(
            "required_equipment", sa.JSON(),
            server_default=sa.text("(JSON_ARRAY())"), nullable=False,
        ),
        sa.Column("room_name_snapshot", sa.String(100), nullable=False),
        sa.Column("room_location_snapshot", sa.String(255), nullable=False),
        sa.Column("room_capacity_snapshot", sa.Integer(), nullable=False),
        sa.Column("room_equipment_snapshot", sa.JSON(), nullable=False),
        sa.Column("status_reason", sa.String(1000), nullable=True),
        sa.Column(
            "submitted_at", mysql.DATETIME(fsp=6),
            server_default=sa.text("(UTC_TIMESTAMP(6))"), nullable=False,
        ),
        sa.Column(
            "updated_at", mysql.DATETIME(fsp=6),
            server_default=sa.text("(UTC_TIMESTAMP(6))"), nullable=False,
        ),
        sa.CheckConstraint("attendees > 0", name=op.f("ck_reservations_attendees")),
        sa.CheckConstraint("room_capacity_snapshot > 0", name=op.f("ck_reservations_snapshot_capacity")),
        sa.CheckConstraint("attendees <= room_capacity_snapshot", name=op.f("ck_reservations_attendees_capacity")),
        sa.CheckConstraint("ends_at > starts_at", name=op.f("ck_reservations_time_order")),
        sa.CheckConstraint("CHAR_LENGTH(TRIM(title)) > 0", name=op.f("ck_reservations_title")),
        sa.CheckConstraint("CHAR_LENGTH(TRIM(purpose)) > 0", name=op.f("ck_reservations_purpose")),
        sa.CheckConstraint(
            "JSON_TYPE(required_equipment) = 'ARRAY'",
            name=op.f("ck_reservations_required_equipment_array"),
        ),
        sa.CheckConstraint(
            "JSON_TYPE(room_equipment_snapshot) = 'ARRAY'",
            name=op.f("ck_reservations_room_equipment_array"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled', 'expired', 'revoked', 'finished')",
            name=op.f("ck_reservations_status"),
        ),
        sa.CheckConstraint("source IN ('page', 'assistant')", name=op.f("ck_reservations_source")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="RESTRICT",
            name=op.f("fk_reservations_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["room_id"], ["meeting_rooms.id"], ondelete="RESTRICT",
            name=op.f("fk_reservations_room_id_meeting_rooms"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reservations")),
        mysql_engine="InnoDB", mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index(
        op.f("ix_reservations_room_occupancy"), "reservations",
        ["room_id", "status", "starts_at", "ends_at"],
    )
    op.create_index(
        op.f("ix_reservations_user_occupancy"), "reservations",
        ["user_id", "status", "starts_at", "ends_at"],
    )
    op.create_index(op.f("ix_reservations_submitted_at"), "reservations", ["submitted_at"])


def downgrade() -> None:
    # 会删除预约表及其数据；仅在隔离测试实例中验证回退，不删除其他业务表。
    op.drop_table("reservations")
