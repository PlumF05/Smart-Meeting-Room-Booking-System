"""实际Python子进程验证Windows reload循环兼容性，不调用模型或数据库。"""

import asyncio
import ctypes
import os
import sys
import threading

import pytest

from app.assistant.model import AIConnectionError
from app.assistant import worker_transport as transport


PROBE = '''
import json,os,pathlib,sys,time
payload=json.loads(sys.stdin.readline())
mode=payload.get('mode','inspect')
if mode=='sleep':
    pathlib.Path(payload['marker']).write_text(str(os.getpid()))
    time.sleep(30)
elif mode=='bad':
    print(payload['secret'])
elif mode=='error':
    print(json.dumps({'ok':False,'code':'credentials'}))
else:
    secret=payload['secret']
    print(json.dumps({'ok':True,'result':{
        'received':secret=='synthetic-worker-only',
        'argv_contains_secret':any(secret in item for item in sys.argv),
        'private_environment':[key for key in os.environ if key.startswith(
            ('SMRBS_','DEEPSEEK_','OPENAI_','LANGCHAIN_','LANGSMITH_','HF_TOKEN','HUGGING_FACE_HUB_TOKEN'))]
    }}))
'''


@pytest.fixture
def module(tmp_path, monkeypatch):
    (tmp_path / '_worker_transport_probe.py').write_text(PROBE, encoding='utf-8')
    monkeypatch.setenv('PYTHONPATH', str(tmp_path))
    for key in ('SMRBS_DB_PASSWORD', 'DEEPSEEK_API_KEY', 'OPENAI_API_KEY',
                'LANGCHAIN_API_KEY', 'LANGSMITH_API_KEY', 'HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN'):
        monkeypatch.setenv(key, 'synthetic-environment-only')
    return '_worker_transport_probe'


@pytest.fixture(params=['proactor', 'selector'] if sys.platform == 'win32' else ['default'])
def loop_factory(request):
    if request.param == 'proactor':
        return asyncio.ProactorEventLoop
    if request.param == 'selector':
        return asyncio.SelectorEventLoop
    return asyncio.new_event_loop


def run(factory, operation):
    with asyncio.Runner(loop_factory=factory) as runner:
        return runner.run(operation)


def alive(pid):
    if sys.platform == 'win32':
        # Windows的os.kill(pid,0)并非POSIX探测语义，使用只读进程句柄。
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_real_worker_receives_stdin_without_argv_or_environment_secrets(module, loop_factory, capsys):
    response = run(loop_factory, transport.run_worker(module, {'secret': 'synthetic-worker-only'}, 5))
    assert response == {'ok': True, 'result': {
        'received': True, 'argv_contains_secret': False, 'private_environment': [],
    }}
    output = capsys.readouterr()
    assert 'synthetic-worker-only' not in output.out + output.err
    assert 'synthetic-environment-only' not in output.out + output.err


def test_real_timeout_kills_child_and_finishes_bridge_thread(module, tmp_path, loop_factory):
    marker = tmp_path / 'timeout-pid.txt'
    with pytest.raises(AIConnectionError, match='^timeout$'):
        run(loop_factory, transport.run_worker(module, {'mode': 'sleep', 'marker': str(marker)}, .7))
    assert marker.exists(), '需要确实启动子进程后测试超时清理'
    assert not alive(int(marker.read_text()))
    assert not [t for t in threading.enumerate() if t.name == 'smrbs-worker-proactor']


def test_real_cancellation_kills_child_and_finishes_bridge_thread(module, tmp_path, loop_factory):
    marker = tmp_path / 'cancel-pid.txt'

    async def scenario():
        task = asyncio.create_task(transport.run_worker(module, {'mode': 'sleep', 'marker': str(marker)}, 10))
        async with asyncio.timeout(5):
            while not marker.exists():
                await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(loop_factory, scenario())
    assert not alive(int(marker.read_text()))
    assert not [t for t in threading.enumerate() if t.name == 'smrbs-worker-proactor']


def test_worker_failure_code_preserved(module, loop_factory):
    with pytest.raises(AIConnectionError, match='^credentials$'):
        run(loop_factory, transport.run_worker(module, {'mode': 'error'}, 5))


def test_invalid_json_does_not_expose_worker_output(module, loop_factory, capsys):
    with pytest.raises(AIConnectionError, match='^worker_unavailable$') as error:
        run(loop_factory, transport.run_worker(module, {'mode': 'bad', 'secret': 'synthetic-worker-only'}, 5))
    assert 'synthetic-worker-only' not in str(error.value)
    output = capsys.readouterr()
    assert 'synthetic-worker-only' not in output.out + output.err


def test_spawn_delay_is_also_inside_deadline(monkeypatch, loop_factory):
    async def delayed_spawn(*args, **kwargs):
        await asyncio.sleep(10)
        raise AssertionError('deadline失效')
    monkeypatch.setattr(transport.asyncio, 'create_subprocess_exec', delayed_spawn)
    with pytest.raises(AIConnectionError, match='^timeout$'):
        run(loop_factory, transport.run_worker('app.assistant.rules_worker', {}, .03))


@pytest.mark.skipif(sys.platform != 'win32', reason='原故障仅Windows Selector循环存在')
def test_original_direct_spawn_fails_under_selector():
    async def direct_spawn():
        with pytest.raises(NotImplementedError):
            await asyncio.create_subprocess_exec(sys.executable, '-c', 'pass')
    run(asyncio.SelectorEventLoop, direct_spawn())
