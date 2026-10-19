"""Opt-in end-to-end checks against the compiled Windows Qt server.

Set WHAT_TEST_EXE and WHAT_TEST_PROFILE. Never starts the app on the LAN:
--test-mode enforces loopback and uses an isolated test-only credential store.
"""
import asyncio
import ctypes
import hashlib
import os
from pathlib import Path
import ssl
import subprocess
import struct
import time

import aiohttp
import pytest
import yaml

from test_protocol import PACKAGE
from what_under_test.api import AuthenticationError, WhatClient
from what_under_test.protocol import ProtocolError

EXE = os.getenv('WHAT_TEST_EXE')
PROFILE = os.getenv('WHAT_TEST_PROFILE')
pytestmark = pytest.mark.skipif(not EXE or not PROFILE, reason='Set opt-in live app paths')


class SharedMemoryFixture:
    """Own test names only; never writes the real HWiNFO mapping."""
    def __init__(self):
        k = self.k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.CreateFileMappingW.restype = ctypes.c_void_p
        k.CreateFileMappingW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_wchar_p]
        k.CreateMutexW.restype = ctypes.c_void_p
        k.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        k.MapViewOfFile.restype = ctypes.c_void_p
        k.MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t]
        k.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        k.ReleaseMutex.argtypes = [ctypes.c_void_p]
        k.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        self.mapping = k.CreateFileMappingW(ctypes.c_void_p(-1), None, 4, 0, 628, 'Local\\WHAT_TEST_SM2')
        self.mutex = k.CreateMutexW(None, False, 'Local\\WHAT_TEST_MUTEX')
        self.view = k.MapViewOfFile(self.mapping, 0xF001F, 0, 0, 628)
        assert self.mapping and self.mutex and self.view
        self.update()

    def update(self, active=True, value=62.5, poll_time=None):
        data = bytearray(628)
        struct.pack_into(
            '<IIIqIIIIIII',
            data,
            0,
            0x53695748 if active else 0x44414544,
            1,
            1,
            int(poll_time or time.time()),
            48,
            264,
            1,
            312,
            316,
            1,
            1000,
        )
        struct.pack_into('<II', data, 48, 42, 2)
        data[56:60] = b'CPU\0'
        struct.pack_into('<III', data, 312, 1, 0, 99)
        data[324:336] = b'CPU Package\0'
        data[580:582] = b'C\0'
        struct.pack_into('<d', data, 596, value)
        assert self.k.WaitForSingleObject(self.mutex, 1000) == 0
        ctypes.memmove(self.view, bytes(data), len(data))
        self.k.ReleaseMutex(self.mutex)

    def close(self):
        self.k.UnmapViewOfFile(self.view)
        self.k.CloseHandle(self.mapping)
        self.k.CloseHandle(self.mutex)


@pytest.fixture(scope='module')
def live_app():
    profile = Path(PROFILE).resolve()
    profile.mkdir(parents=True, exist_ok=True)
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    source = SharedMemoryFixture()
    with (profile / 'stderr.txt').open('w') as log:
        proc = subprocess.Popen(
            [EXE, '--test-mode', '--data-dir', str(profile), '--quit-after', '90'],
            stderr=log,
            creationflags=flags,
        )
        try:
            for _ in range(100):
                if proc.poll() is not None:
                    pytest.fail('Qt app exited: ' + (profile / 'stderr.txt').read_text())
                if (profile / 'test-certificate.pem').exists() and (profile / 'test-settings.yaml').exists():
                    break
                time.sleep(.1)
            time.sleep(.5)
            settings = yaml.safe_load((profile / 'test-settings.yaml').read_text(encoding='utf-8'))['settings']
            token = settings['test_token']
            der = ssl.PEM_cert_to_DER_cert((profile / 'test-certificate.pem').read_text())
            yield token, hashlib.sha256(der).hexdigest(), settings['device_id'], source
        finally:
            proc.terminate()
            proc.wait(timeout=10)
            source.close()


def test_live_ui_feedback(live_app):
    token, pin, identity, _ = live_app
    async def run():
        answered = asyncio.Event()
        async with aiohttp.ClientSession() as session:
            client = WhatClient(session, '127.0.0.1', 18765, token, pin, identity)
            def lookup(keys):
                assert keys == []
                answered.set()
                return []
            client.entity_resolver = lookup
            try:
                hello, _ = await client.connect()
                assert hello['ui_feedback_v1'] is True
                async with asyncio.timeout(10):
                    while not answered.is_set():
                        await client.receive_snapshot()
                # Windows must accept the reply and keep the telemetry stream open.
                await client.receive_snapshot()
            finally:
                await client.close()
    asyncio.run(run())


def test_live_valid_and_reconnect(live_app):
    token, pin, identity, _ = live_app
    async def run():
        async with aiohttp.ClientSession() as session:
            client = WhatClient(session, '127.0.0.1', 18765, token, pin)
            hello, data = await client.connect()
            assert hello['device_id'] == identity
            if os.getenv('WHAT_EXPECTED_VERSION'):
                assert hello['app_version'] == os.environ['WHAT_EXPECTED_VERSION']
            for key in ('enabled', 'running', 'installed', 'control_enabled', 'cloud', 'share_path', 'busy'):
                if key in data.get('wallpaper', {}):
                    assert type(data['wallpaper'][key]) is bool
            assert data['app']['update']['installed'] == hello['app_version']
            assert data['app']['update']['latest'] is None  # Test mode never contacts GitHub.
            assert data['app']['uptime_seconds'] >= 0
            assert data['device_id'] == identity
            assert data['license'] in ('unknown', 'free', 'pro')
            # The first valid snapshot can precede asynchronous collection.
            async with asyncio.timeout(10):
                while not data['healthy']:
                    data = await client.receive_snapshot()
            assert data['sensors'][0]['value'] == 62.5
            await client.receive_snapshot()
            await client.close()
            hello2, _ = await client.connect()
            assert hello2['device_id'] == identity
            await client.close()
    asyncio.run(run())


def test_live_commands_require_windows_permission(live_app):
    token, pin, identity, _ = live_app
    async def run():
        async with aiohttp.ClientSession() as session:
            client = WhatClient(session, '127.0.0.1', 18765, token, pin)
            hello, data = await client.connect()
            assert hello['commands_v1'] is True
            assert not data['controls']['permissions']['media']
            async def pump():
                while True:
                    await client.receive_snapshot()
            reader = asyncio.create_task(pump())
            try:
                assert data['app']['control_enabled'] is False
                for action, args in (("app_logging", {"value": True}),
                                     ("app_publish_interval", {"value": 2000}), ("app_restart", {})):
                    with pytest.raises(ConnectionError, match="Manage WHAT"):
                        await client.command(action, args)
                with pytest.raises(ConnectionError, match='permission'):
                    await client.command('volume', {'value': .4})
                assert data['displays']['enabled'] is False
                with pytest.raises(ConnectionError, match='Displays'):
                    await client.command('display', {'operation': 'extend', 'monitor': 'a' * 24})
                assert data['controls']['input_enabled'] is False
                with pytest.raises(ConnectionError, match='permission'):
                    await client.command('keyboard', {'keys': ['CTRL', 'S']})
                with pytest.raises(ConnectionError, match='permission'):
                    await client.command('mouse', {'x': 0, 'y': 0, 'button': 'left'})
                assert data['controls']['notifications_enabled'] is False
                with pytest.raises(ConnectionError, match='Enable'):
                    await client.command(
                        'notify',
                        {'title': 'Test', 'message': 'Permission must block this'},
                    )
            finally:
                reader.cancel()
                try:
                    await reader
                except asyncio.CancelledError:
                    pass
                await client.close()
    asyncio.run(run())


def test_live_bad_token(live_app):
    _, pin, _, _ = live_app
    async def run():
        async with aiohttp.ClientSession() as session:
            client = WhatClient(session, '127.0.0.1', 18765, '0' * 64, pin)
            with pytest.raises(AuthenticationError):
                await client.connect()
    asyncio.run(run())


def test_live_bad_fingerprint(live_app):
    token, _, _, _ = live_app
    async def run():
        async with aiohttp.ClientSession() as session:
            client = WhatClient(session, '127.0.0.1', 18765, token, '00' * 32)
            with pytest.raises(aiohttp.ServerFingerprintMismatch):
                await client.connect()
    asyncio.run(run())


def test_live_wrong_device(live_app):
    token, pin, _, _ = live_app
    async def run():
        async with aiohttp.ClientSession() as session:
            client = WhatClient(session, '127.0.0.1', 18765, token, pin, device_id='different-pc')
            with pytest.raises(ProtocolError):
                await client.connect()
    asyncio.run(run())


def test_live_concurrent_clients(live_app):
    token, pin, identity, _ = live_app
    async def run():
        async with aiohttp.ClientSession() as session:
            a = WhatClient(session, '127.0.0.1', 18765, token, pin)
            b = WhatClient(session, '127.0.0.1', 18765, token, pin)
            try:
                result = await asyncio.gather(a.connect(), b.connect())
                assert all(hello['device_id'] == identity for hello, _ in result)
            finally:
                await a.close()
                await b.close()
    asyncio.run(run())


def test_live_source_loss_stale_and_recovery(live_app):
    token, pin, _, source = live_app
    async def run():
        async with aiohttp.ClientSession() as session:
            client = WhatClient(session, '127.0.0.1', 18765, token, pin)
            await client.connect()
            async def until(predicate):
                async with asyncio.timeout(8):
                    while True:
                        result = await client.receive_snapshot()
                        if predicate(result):
                            return result
            source.update(active=False)
            data = await until(lambda d: not d['healthy'])
            assert data['windows_healthy']
            assert any(row['source'] == 'windows' and row['available'] for row in data['sensors'])
            assert all(not row['available'] for row in data['sensors'] if row['source'] == 'hwinfo')
            source.update(value=71.25)
            data = await until(
                lambda d: d['healthy'] and any(r['source'] == 'hwinfo' and r['value'] == 71.25 for r in d['sensors']),
            )
            source.update(poll_time=time.time() - 120)
            data = await until(lambda d: not d['healthy'] and 'stale' in d['status'].lower())
            source.update(value=48.0)
            await until(
                lambda d: d['healthy'] and any(r['source'] == 'hwinfo' and r['value'] == 48.0 for r in d['sensors']),
            )
            await client.close()
    asyncio.run(run())


def test_live_windows_only_no_hwinfo_mapping(tmp_path):
    profile = tmp_path / "windows-only"
    profile.mkdir()
    (profile / "test-settings.yaml").write_text(
        yaml.safe_dump({"format": "WHAT settings", "version": 1, "settings": {
            "port": 18766, "license": "pro", "sample_seconds": 1,
            "publish_seconds": 1, "sources/hwinfo": False,
        }}),
        encoding="utf-8",
    )
    with (profile / "stderr.txt").open("w") as log:
        proc = subprocess.Popen(
            [EXE, "--test-mode", "--data-dir", str(profile), "--quit-after", "20"],
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            for _ in range(100):
                if (profile / "test-certificate.pem").exists():
                    break
                assert proc.poll() is None
                time.sleep(.1)
            time.sleep(1)
            settings = yaml.safe_load((profile / "test-settings.yaml").read_text(encoding="utf-8"))["settings"]
            der = ssl.PEM_cert_to_DER_cert((profile / "test-certificate.pem").read_text())
            async def run():
                async with aiohttp.ClientSession() as session:
                    client = WhatClient(
                        session,
                        "127.0.0.1",
                        18766,
                        settings["test_token"],
                        hashlib.sha256(der).hexdigest(),
                    )
                    _, data = await client.connect()
                    for _ in range(8):
                        if data["windows_healthy"]:
                            break
                        data = await client.receive_snapshot()
                    assert not data["hwinfo_enabled"] and not data["healthy"]
                    assert data["windows_healthy"]
                    rows = {r["id"]: r for r in data["sensors"]}
                    assert rows["win_uptime"]["value"] > 0
                    assert rows["win_last_boot"]["kind"] == "timestamp"
                    assert rows["win_collector_status"]["value"] == "Ready"
                    assert all(r["source"] == "windows" for r in rows.values())
                    assert not any(r["id"].endswith("_title") for r in rows.values())
                    await client.close()
            asyncio.run(run())
        finally:
            proc.terminate()
            proc.wait(timeout=10)


def test_live_app_settings_survive_process_handoff(tmp_path):
    """Two test-mode processes exercise the actual startup wait and YAML persistence."""
    import socket
    import contextlib

    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    profile = tmp_path / 'handoff'
    profile.mkdir()
    settings_path = profile / 'test-settings.yaml'
    settings_path.write_text(yaml.safe_dump({
        'format': 'WHAT settings', 'version': 1,
        'settings': {'port': port, 'controls/what': True, 'sources/hwinfo': False,
                     'modules/windows/enabled': False, 'publish_seconds': 1},
    }), encoding='utf-8')
    processes = []
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    with (profile / 'first.log').open('w') as first_log, (profile / 'second.log').open('w') as second_log:
        first = subprocess.Popen([EXE, '--test-mode', '--data-dir', str(profile), '--quit-after', '10'],
                                 stderr=first_log, creationflags=flags)
        processes.append(first)
        try:
            async def run():
                async with aiohttp.ClientSession() as session:
                    async def connect(process):
                        async with asyncio.timeout(15):
                            while True:
                                assert process.poll() is None, 'Test WHAT exited before connecting'
                                if (profile / 'test-certificate.pem').exists():
                                    settings = yaml.safe_load(settings_path.read_text(encoding='utf-8'))['settings']
                                    if 'test_token' in settings:
                                        der = ssl.PEM_cert_to_DER_cert((profile / 'test-certificate.pem').read_text())
                                        client = WhatClient(session, '127.0.0.1', port, settings['test_token'],
                                                            hashlib.sha256(der).hexdigest())
                                        try:
                                            _, data = await client.connect()
                                            return client, data
                                        except (OSError, ConnectionError, aiohttp.ClientError):
                                            await client.close()
                                await asyncio.sleep(.1)

                    client, data = await connect(first)
                    identity = data['device_id']
                    assert data['app']['control_enabled']
                    latest = data
                    async def pump():
                        nonlocal latest
                        while True:
                            latest = await client.receive_snapshot()
                    reader = asyncio.create_task(pump())
                    try:
                        await client.command('app_logging', {'value': True})
                        await client.command('app_publish_interval', {'value': 2500})
                        async with asyncio.timeout(5):
                            while not latest['app']['logging'] or latest['app']['publishing_interval_ms'] != 2500:
                                await asyncio.sleep(.05)
                        assert latest['app']['high_interval_ms'] == 100
                        assert latest['publish_seconds'] == 1
                        await client.command('app_restart', {})  # Accepted, dry-run in test mode.
                    finally:
                        reader.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await reader
                        await client.close()
                    second = subprocess.Popen(
                        [EXE, '--test-mode', '--data-dir', str(profile), '--restart-after', str(first.pid),
                         '--quit-after', '20'], stderr=second_log, creationflags=flags,
                    )
                    processes.append(second)
                    await asyncio.sleep(.3)
                    assert second.poll() is None
                    assert int((profile / 'app.lock').read_text().splitlines()[0]) == first.pid
                    assert await asyncio.to_thread(first.wait, 15) == 0
                    client, data = await connect(second)
                    try:
                        assert data['device_id'] == identity
                        assert data['app']['logging'] is True
                        assert data['app']['publishing_interval_ms'] == 2500
                        assert int((profile / 'app.lock').read_text().splitlines()[0]) == second.pid
                    finally:
                        await client.close()
            asyncio.run(run())
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=10)
