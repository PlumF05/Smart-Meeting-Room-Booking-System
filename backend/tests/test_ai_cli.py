"""命令行保密、离线行为与基础应用隔离；不读取真实AI或数据库配置。"""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.cli import check_ai
from app.core.ai_config import AISettings
from app.assistant.model import AIConnectionError


def test_default_check_never_calls_model(monkeypatch, capsys):
    monkeypatch.setattr(check_ai, "load_ai_settings", lambda *args: AISettings(enabled=True, api_key="synthetic-secret"))
    monkeypatch.setattr(check_ai, "version", lambda name: "synthetic")
    def forbidden(*args):
        pytest.fail("离线检查不得调用模型")
    monkeypatch.setattr(check_ai, "check_connection", forbidden)
    assert check_ai.main([]) == 0
    output = capsys.readouterr()
    assert "未联网" in output.out and "synthetic-secret" not in output.out + output.err


def test_live_reports_safe_error_only(monkeypatch, capsys):
    monkeypatch.setattr(check_ai, "load_ai_settings", lambda *args: AISettings(enabled=True, api_key="synthetic-secret"))
    monkeypatch.setattr(check_ai, "version", lambda name: "synthetic")
    async def failed(*args):
        raise AIConnectionError("credentials")
    monkeypatch.setattr(check_ai, "check_connection", failed)
    assert check_ai.main(["--live"]) == 1
    output = capsys.readouterr()
    assert "认证" in output.err and "synthetic-secret" not in output.out + output.err


def test_secret_in_unknown_argument_is_not_echoed(capsys):
    with pytest.raises(SystemExit) as raised:
        check_ai.main(["--api-key=synthetic-secret"])
    assert raised.value.code == 2
    output = capsys.readouterr()
    assert "synthetic-secret" not in output.out + output.err


def test_basic_app_and_cli_import_without_ai_packages(tmp_path):
    script = r'''
import sys,importlib.abc
class NoAI(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname.split('.')[0] in ('langchain','langchain_core','langchain_deepseek','langchain_openai','langsmith','openai','langgraph','chromadb','sentence_transformers'):
   raise ImportError('synthetic optional dependency unavailable')
sys.meta_path.insert(0,NoAI())
from app.core import config
def forbidden(*a,**k): raise AssertionError('不得读取开发配置')
config.load_database_settings=forbidden
from app.main import app
from app.cli.check_ai import main
from app.assistant.model import AIConnectionError,check_connection
from app.core.ai_config import AISettings
import asyncio
from fastapi.testclient import TestClient
with TestClient(app) as client:
 assert client.get('/api/health').status_code==200
 assert client.get('/docs').status_code==200
 assert '/api/reservations' in client.get('/openapi.json').json()['paths']
assert main(['--config',sys.argv[1]])==1
try:
 asyncio.run(check_connection(AISettings(enabled=True,api_key='synthetic-unavailable-dependencies')))
except AIConnectionError as error:
 assert error.code=='dependencies'
else:
 raise AssertionError('缺少AI依赖时不应调用模型')
print('BASE_APP_WITHOUT_AI_OK')
'''
    environment = {k:v for k,v in os.environ.items() if not k.startswith(("SMRBS_","DEEPSEEK_","LANGCHAIN_","LANGSMITH_"))}
    result = subprocess.run([sys.executable,"-c",script,str(tmp_path/"missing.env")],
        cwd=Path(__file__).resolve().parents[1],env=environment,capture_output=True,text=True,encoding="utf-8",timeout=30)
    assert result.returncode == 0, "隔离子进程启动失败；原始输出不在此回显"
    assert "BASE_APP_WITHOUT_AI_OK" in result.stdout
