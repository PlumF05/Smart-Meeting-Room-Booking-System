"""独立运行的预约时间状态补齐进程，不依赖AI或Web服务器生命周期。"""
import argparse
from threading import Event
from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import DatabaseUnavailableError
from app.db.session import get_engine
from app.services.reservation_lifecycle import process_due


def main():
    parser = argparse.ArgumentParser(description="补齐已到时刻的预约状态、系统日志与过期消息")
    parser.add_argument("--once", action="store_true", help="只处理一个批次后退出")
    parser.add_argument("--interval", type=int, default=15, help="检查间隔秒数，默认15")
    args = parser.parse_args()
    if args.interval < 1:
        parser.error("间隔须至少1秒")
    try:
        engine = get_engine()
    except (OSError, ValueError, SQLAlchemyError):
        raise SystemExit("数据库配置不可用，请检查本地配置。") from None
    try:
        while True:
            try:
                print(f"本批更新 {process_due(engine)} 条预约。", flush=True)
            except DatabaseUnavailableError:
                print("数据库暂不可用，本批失败；请核对服务和本地配置。", flush=True)
                if args.once:
                    raise SystemExit(1) from None
            if args.once:
                break
            Event().wait(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
