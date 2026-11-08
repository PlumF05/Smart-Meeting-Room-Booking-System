"""默认仅离线检查配置与依赖；显式--live才调用一次真实DeepSeek API。"""

import argparse
import asyncio
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import sys

from app.core.ai_config import AI_ENV_FILE, load_ai_settings
from app.assistant.model import AIConnectionError, check_connection

MESSAGES = {
    "disabled": "AI开关未启用；请在本地.env.ai中设置SMRBS_AI_ENABLED=true。",
    "missing_key": "未配置DeepSeek密钥；请只在本地.env.ai中填写DEEPSEEK_API_KEY。",
    "dependencies": "AI依赖不可用；请在meeting-room环境安装requirements-ai.txt。",
    "timeout": "调用超时，未自动重试；请检查网络或稍后手动重试。",
    "credentials": "API认证或访问被拒绝；请在本地核对密钥及账号权限。",
    "balance": "API余额不足；请在DeepSeek平台核对账号余额。",
    "rate_limit": "API请求受限；请稍后手动重试。",
    "model_or_request": "模型或请求不被接受；请核对本地型号与官方API支持情况。",
    "provider": "DeepSeek服务暂不可用；未自动重试。",
    "connection": "调用未完成；请核对网络、证书或模型集成兼容性。",
    "empty_response": "服务未返回非空文本，尚不能确认最小模型调用成功。",
}


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.exit(2, "参数无效：仅接受--config路径及--live；不要在命令行传密钥。\n")


def main(argv: list[str] | None = None) -> int:
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=AI_ENV_FILE)
    parser.add_argument("--live", action="store_true", help="发送一条固定短消息，可能产生少量API费用")
    args = parser.parse_args(argv)
    try:
        settings = load_ai_settings(args.config)
        if not settings.enabled:
            raise AIConnectionError("disabled")
        if not settings.is_configured:
            raise AIConnectionError("missing_key")
        for package in ("langchain-deepseek", "langchain-core"):
            try:
                version(package)
            except PackageNotFoundError:
                raise AIConnectionError("dependencies") from None
        if not args.live:
            print("离线检查通过：AI已启用，密钥字段已填写，最小依赖已安装；未验证密钥有效性，未联网。")
            return 0
        elapsed = asyncio.run(check_connection(settings))
        print(f"真实DeepSeek调用成功：收到非空文本，耗时{elapsed:.2f}秒；未打印或保存回复内容。")
        return 0
    except AIConnectionError as error:
        print(MESSAGES[error.code], file=sys.stderr)
        return 1
    except (ValueError, OSError):
        print("AI配置不可用；请检查本地配置文件、true/false开关、型号及1至45秒超时设置。", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("检查已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
