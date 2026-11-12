"""Run the representative backend and frontend regression cases together."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mysqld", default=os.environ.get("SMRBS_TEST_MYSQLD"),
                        help="Path to the installed mysqld executable for isolated tests")
    args = parser.parse_args()
    if not args.mysqld or not Path(args.mysqld).is_file():
        parser.error("supply a valid --mysqld path or set SMRBS_TEST_MYSQLD")
    node = shutil.which("node")
    if node is None:
        parser.error("Node.js must be on PATH")

    cases = json.loads(Path(__file__).with_name("core_test_cases.json").read_text(encoding="utf-8"))
    backend = [f"{file}::{name}" for file, names in cases["backend"].items() for name in names]
    frontend_names = [name for names in cases["frontend"].values() for name in names]
    if len(set(backend)) != len(backend) or len(set(frontend_names)) != len(frontend_names):
        parser.error("the core test list contains duplicates")
    env = {
        **os.environ,
        "SMRBS_RUN_MYSQL_SCHEMA_TEST": "1",
        "SMRBS_TEST_MYSQLD": str(Path(args.mysqld).resolve()),
        "PYTHONIOENCODING": "utf-8",
    }
    print(f"Core regression: {len(backend)} backend + {len(frontend_names)} frontend cases",
          flush=True)
    print("\nBackend", flush=True)
    with tempfile.TemporaryDirectory(prefix="smrbs-test-") as directory:
        report = Path(directory) / "backend.xml"
        backend_result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", f"--junitxml={report}", *backend],
            cwd=ROOT / "backend", env=env, check=False,
        )
        backend_ok = backend_result.returncode == 0
        if backend_ok:
            executed = ElementTree.parse(report).findall(".//testcase")
            backend_ok = len(executed) == len(backend) and all(
                case.find("skipped") is None for case in executed
            )
            if not backend_ok:
                print("Backend test selection did not run every core case exactly once.",
                      file=sys.stderr)

    print("\nFrontend", flush=True)
    pattern = "^(?:" + "|".join(re.escape(name) for name in frontend_names) + ")$"
    frontend_result = subprocess.run(
        [node, "--test", "--test-reporter=tap", f"--test-name-pattern={pattern}", *cases["frontend"]],
        cwd=ROOT / "frontend", capture_output=True, text=True, encoding="utf-8", check=False,
    )
    if frontend_result.returncode:
        print(frontend_result.stdout, end="")
        print(frontend_result.stderr, end="", file=sys.stderr)
        return 1
    counts = dict(re.findall(r"^# (tests|pass|fail|skipped) (\d+)$", frontend_result.stdout, re.M))
    print(f"Frontend results: {counts}")
    if counts.get("pass") != str(len(frontend_names)):
        print("Frontend test selection no longer matches the core test list.", file=sys.stderr)
        return 1
    return 0 if backend_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
