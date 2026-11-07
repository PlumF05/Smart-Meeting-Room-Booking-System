"""本机显式预置会议室，不在应用启动或数据库迁移时自动插入。"""

import sys

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import DatabaseUnavailableError
from app.db.session import create_database_engine
from app.services.room_seed import RoomSeedConflictError, seed_rooms


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments:
        print("本命令不接受参数，请在本地.env中配置数据库。", file=sys.stderr)
        return 2
    engine = None
    try:
        engine = create_database_engine()
        with Session(engine) as session:
            result = seed_rooms(session)
        print(f"演示会议室预置完成：新增{result.created}间，已有编号跳过{result.skipped}间。")
        return 0
    except RoomSeedConflictError:
        print("预置已停止：预置名称被其他编号的会议室占用，本次未新增或覆盖资料。", file=sys.stderr)
    except (DatabaseUnavailableError, SQLAlchemyError, ValueError, OSError):
        print("预置失败：请在本地检查数据库服务、连接配置、迁移和账号权限。", file=sys.stderr)
    finally:
        if engine is not None:
            engine.dispose()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
