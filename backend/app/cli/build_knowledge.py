"""离线构建当前发布规则索引；仅--download-model显式下载公开Embedding权重。"""
import argparse
from contextlib import redirect_stdout
import io
import os
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-model", action="store_true")
    args = parser.parse_args(argv)
    os.environ.update(HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1", ANONYMIZED_TELEMETRY="False",
                      TOKENIZERS_PARALLELISM="false")
    try:
        from app.assistant.knowledge import RuleIndex, download_model, load_catalog
        if args.download_model:
            download_model()
        os.environ["HF_HUB_OFFLINE"] = "1"
        with redirect_stdout(io.StringIO()):
            result = RuleIndex(load_catalog()).build()
        print(f"知识索引构建成功：版本{result['version']}，{result['chunks']}个片段。未调用DeepSeek或业务数据库。")
        return 0
    except Exception:
        print("知识准备失败：请核对AI依赖、发布快照、本地模型及索引目录权限；首次准备可加--download-model。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
