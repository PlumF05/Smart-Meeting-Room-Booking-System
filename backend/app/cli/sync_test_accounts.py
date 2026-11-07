"""只读数据库，初始化或校准本机测试账号清单，不修改密码或会话。"""

import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.admin_config import load_admin_settings
from app.core.local_accounts import AccountLedger, KnownAccount, load_local_account_settings
from app.db.session import create_database_engine
from app.models import User


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments:
        print("本命令不接受参数，请在本地配置文件中设置开关。", file=sys.stderr)
        return 2
    engine = None
    try:
        settings = load_local_account_settings()
        if not settings.enabled:
            print("尚未开启本机测试账号记录，请在.env中设置SMRBS_RECORD_TEST_PASSWORDS=true。")
            return 1
        admin = load_admin_settings()
        engine = create_database_engine()

        def load_accounts() -> tuple[list[KnownAccount], dict[int, str]]:
            # 普通快照对象在事务结束后使用，不把ORM对象或数据库连接交给文件写入层。
            with Session(engine) as session, session.begin():
                rows = session.execute(select(User.id, User.username, User.password_hash)).all()
                accounts = [KnownAccount(row.id, row.username, row.password_hash) for row in rows]
                admin_id = session.scalar(
                    select(User.id).where(User.username == admin.username, User.role == "admin")
                )
                candidates = {}
                if admin_id is not None and admin.password is not None:
                    # 候选密码仍需在sync中通过当前哈希验证，不能直接相信旧配置。
                    candidates[admin_id] = admin.password.get_secret_value()
            return accounts, candidates

        result = AccountLedger(settings.path).sync_from(load_accounts)
        print(f"本机测试账号文件已同步：{settings.path}")
        print(f"用户总数：{result['total']}；已知密码：{result['known']}；未知密码：{result['unknown']}。")
        return 0
    except Exception:
        # 禁止输出原始异常，避免配置、SQL参数或文件内容出现在终端。
        print("同步失败：请在本地检查数据库、配置开关及账号文件的格式和权限；已有文件未主动清空。", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
