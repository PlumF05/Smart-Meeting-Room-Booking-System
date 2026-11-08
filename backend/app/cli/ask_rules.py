"""本地只读规则问答；stdin输入问题，不接受命令行密钥。"""
import asyncio
import sys
from app.core.ai_config import load_ai_settings
from app.assistant.rules import ask_rules
from app.assistant.model import AIConnectionError


def main():
    try:
        question = input("请输入会议室规则问题：").strip()
        if not 1 <= len(question) <= 500:
            print("问题须为1至500个字符。", file=sys.stderr)
            return 2
        result = asyncio.run(ask_rules(question, load_ai_settings()))
        print(result.answer)
        for source in result.sources:
            print(f"依据：{source.section} {source.title}（{source.version}）\n{source.excerpt}")
        return 0
    except AIConnectionError:
        print("规则问答未完成，请核对本地配置和知识准备；未执行预约操作。", file=sys.stderr)
        return 1
    except (OSError, ValueError, EOFError):
        print("输入或本地配置不可用。", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
