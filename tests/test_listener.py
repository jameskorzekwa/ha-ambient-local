"""Integration tests for the push listener (real aiohttp server, no HA)."""

from __future__ import annotations

import socket

import aiohttp

from custom_components.ambient_local.listener import PushListener


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def test_listener_receives_push_and_source_ip(socket_enabled):
    calls: list[tuple] = []
    port = _free_port()
    listener = PushListener(port, lambda data, ip: calls.append((data, ip)))
    await listener.start()
    try:
        async with (
            aiohttp.ClientSession() as s,
            s.get(f"http://127.0.0.1:{port}/?tempf=70&humidity=50") as r,
        ):
            assert r.status == 200
            assert await r.text() == "OK"
    finally:
        await listener.stop()

    assert len(calls) == 1
    data, source_ip = calls[0]
    assert data == {"tempf": "70", "humidity": "50"}
    assert source_ip  # the console's IP is learned from the request


async def test_listener_ignores_empty_request(socket_enabled):
    calls: list = []
    port = _free_port()
    listener = PushListener(port, lambda *a: calls.append(a))
    await listener.start()
    try:
        async with (
            aiohttp.ClientSession() as s,
            s.get(f"http://127.0.0.1:{port}/") as r,
        ):
            assert r.status == 200
    finally:
        await listener.stop()
    assert calls == []  # no query params -> no callback


async def test_stop_is_idempotent_and_safe_before_start(socket_enabled):
    listener = PushListener(_free_port(), lambda *a: None)
    await listener.stop()  # never started
    await listener.start()
    await listener.stop()
    await listener.stop()  # double stop
