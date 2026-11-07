"""通过本地配置或不回显的终端输入预置管理员。"""

import argparse
import getpass
from pathlib import Path
import sys
import warnings

from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.core.admin_config import ADMIN_ENV_FILE, AdminSeedSettings, load_admin_settings
from app.core.errors import DatabaseUnavailableError
from app.db.session import create_database_engine
from app.services.bootstrap import AdminPasswordRequiredError, AdminSeedConflictError, seed_admin


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, "参数无效：仅支持--config配置路径，不接受命令行密码。\n")


def read_password(settings: AdminSeedSettings) -> AdminSeedSettings:
    if not sys.stdin.isatty():
        raise ValueError("请在本地.env.admin中配置密码，或使用交互终端执行命令。")
    # getpass无法隐藏输入时必须停止，不能退回普通stdin回显。
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        password = getpass.getpass("管理员密码（8至1024个字符，不回显）：")
        confirmation = getpass.getpass("再次输入管理员密码：")
    if password != confirmation:
        raise ValueError("两次输入的密码不一致。")
    return AdminSeedSettings(
        username=settings.username, full_name=settings.full_name, password=password
    )


def main(argv: list[str] | None = None) -> int:
    parser = SafeArgumentParser(description="预置管理员；同名管理员存在时不修改。")
    parser.add_argument("--config", type=Path, default=ADMIN_ENV_FILE, help="本机管理员配置文件")
    args = parser.parse_args(argv)
    engine = None
    try:
        settings = load_admin_settings(args.config)
        engine = create_database_engine()
        with Session(engine) as session:
            try:
                result = seed_admin(session, settings)
            except AdminPasswordRequiredError:
                settings = read_password(settings)
                result = seed_admin(session, settings)
        if result.created:
            print(f"管理员已创建：{result.username}。密码仅由本机配置或输入提供。")
        else:
            print(f"管理员已存在：{result.username}。未修改其密码、资料或启停状态。")
        return 0
    except AdminSeedConflictError:
        print("初始化已停止：登录名被普通用户占用，未修改任何账号。", file=sys.stderr)
    except (DatabaseUnavailableError, SQLAlchemyError):
        print("初始化失败：请在本地检查数据库服务、连接配置、迁移及账号权限。", file=sys.stderr)
    except (OSError, ValueError, getpass.GetPassWarning):
        print("初始化配置或输入无效：请检查本地字段、密码长度和两次输入。", file=sys.stderr)
    except (EOFError, KeyboardInterrupt):
        print("初始化已取消。", file=sys.stderr)
    finally:
        if engine is not None:
            engine.dispose()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
