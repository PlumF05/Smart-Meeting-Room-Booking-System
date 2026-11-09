"""主题和用途选填，保留历史数据和字段长度限制。"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint(op.f('ck_reservations_title'), 'reservations', type_='check')
    op.drop_constraint(op.f('ck_reservations_purpose'), 'reservations', type_='check')


def downgrade():
    # 不能为了恢复旧约束而覆盖用户的空白数据。
    count=op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM reservations WHERE CHAR_LENGTH(TRIM(title))=0 OR CHAR_LENGTH(TRIM(purpose))=0"))
    if count:
        raise RuntimeError('存在选填留空记录，不能无损恢复必填约束。')
    op.create_check_constraint(op.f('ck_reservations_title'),'reservations','CHAR_LENGTH(TRIM(title)) > 0')
    op.create_check_constraint(op.f('ck_reservations_purpose'),'reservations','CHAR_LENGTH(TRIM(purpose)) > 0')
