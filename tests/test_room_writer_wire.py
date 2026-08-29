"""RoomWriter speaks the room's WIRE — measured, because it used not to.

Until 2026-08-29 `apply()` POSTed each operation to `/v1/rooms/{room}/op`, an
endpoint that has never existed. Room operations travel on the room's WebSocket
(`stratigraph-server/app/ws.py`, ADR-002). Nothing looked broken because the
fallback to the local container is good — which is exactly why this file exists:
a write path whose failure mode is a silent, working alternative needs a test
that asserts the write LANDED, not that the call returned.

Two levels, and both are here on purpose:

* **against a fake relay** (a real WebSocket server, in-process): the wire, the
  ack, the refusals, the ordering. Runs everywhere, no stack;
* **against the REAL server** when one is up: a card said out loud by the
  assistant appears in the room's own document. Skipped with a sentence when
  there is no stack, never silently.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("websockets", reason="the room's wire needs the client")

from app.assets import InMemoryAssetStore              # noqa: E402
from app.contract import GraphDelta, invoke            # noqa: E402
from app.tools import build_registry                   # noqa: E402
from app.writer import RoomRefused, RoomWriter, describe  # noqa: E402

ORCID = "0000-0002-1825-0097"


# ── a relay that behaves like the real one ───────────────────────────────────

class FakeRelay:
    """A WebSocket server speaking WIRE 2: the three join frames, then one
    `op_result` per `op`. Records what it was sent."""

    def __init__(self, *, can_write=True, apply_ops=True, noise=False):
        self.received = []
        self.can_write = can_write
        self.apply_ops = apply_ops
        self.noise = noise
        self.tokens = []
        self.port = None
        self._loop = None
        self._thread = None
        self._stop = None
        self._ready = threading.Event()

    def __enter__(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self._ready.wait(10), "the fake relay never came up"
        return self

    def __exit__(self, *exc):
        # Shut the loop down THROUGH the server, not by stopping the loop under
        # it: `loop.stop()` left websockets' close task unscheduled, and every
        # test emitted an unraisable "Event loop is closed". A teardown that
        # warns teaches people to ignore warnings.
        if self._loop and self._stop is not None:
            self._loop.call_soon_threadsafe(
                lambda: self._stop.done() or self._stop.set_result(None))
        if self._thread:
            self._thread.join(timeout=5)
        return False

    def _run(self):
        import websockets

        async def handler(socket):
            path = getattr(socket, "request", None)
            target = getattr(path, "path", "") or ""
            if "token=" in target:
                self.tokens.append(target.split("token=", 1)[1].split("&")[0])
            await socket.send(json.dumps({
                "v": 2, "type": "host_info", "source": "em-server",
                "payload": {"room": "r", "author": ORCID, "role": "owner",
                            "can_write": self.can_write}}))
            await socket.send(json.dumps({
                "v": 2, "type": "snapshot", "source": "em-server",
                "payload": {"doc": {"graphs": {}}}}))
            await socket.send(json.dumps({
                "v": 2, "type": "presence", "source": "em-server",
                "payload": {"members": []}}))
            try:
                async for raw in socket:
                    message = json.loads(raw)
                    self.received.append(message)
                    if self.noise:
                        # somebody else's news arrives on the same socket, and
                        # the answer is NOT the next frame
                        await socket.send(json.dumps({
                            "v": 2, "type": "presence", "source": "em-server",
                            "payload": {"members": ["someone"]}}))
                    await socket.send(json.dumps({
                        "v": 2, "type": "op_result", "source": "em-server",
                        "payload": {"applied": bool(self.apply_ops),
                                    "reason": "" if self.apply_ops else "stale",
                                    "op": message.get("payload")}}))
            except Exception:                      # the client closed
                pass

        async def main():
            async with websockets.serve(handler, "127.0.0.1", 0) as server:
                self.port = server.sockets[0].getsockname()[1]
                self._stop = asyncio.get_running_loop().create_future()
                self._ready.set()
                await self._stop

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(main())
        finally:
            self._loop.close()

    @property
    def ops(self):
        return [m["payload"] for m in self.received if m.get("type") == "op"]


def _writer(relay, **kwargs):
    return RoomWriter(f"http://127.0.0.1:{relay.port}", "saggio-b", "tok-123",
                      timeout=5.0, **kwargs)


def _delta():
    return GraphDelta(
        nodes=[{"id": "US12", "node_type": "US", "name": "US 12"}],
        edges=[{"id": "e1", "source": "US12", "target": "EP1",
                "edge_type": "has_epoch"}],
        process={"id": "p1", "node_type": "dtc_process", "name": "create_su"},
        author=ORCID)


# ── 1 · the wire ─────────────────────────────────────────────────────────────

def test_the_delta_goes_out_as_wire_2_op_frames():
    with FakeRelay() as relay:
        writer = _writer(relay)
        writer.apply(_delta())
    frames = [m for m in relay.received if m.get("type") == "op"]
    assert len(frames) == 3, relay.received
    for frame in frames:
        assert frame["v"] == 2
        assert set(frame) >= {"v", "type", "payload"}
        # the body is nested under `payload` — that is WIRE 2, and the bug it
        # cured was an envelope word colliding with an edge's own `source`
        assert "op" in frame["payload"]
    kinds = [f["payload"]["op"] for f in frames]
    assert kinds == ["add_node", "add_node", "add_edge"]
    ids = [f["payload"].get("id") for f in frames]
    assert ids == ["US12", "p1", "e1"]


def test_the_edge_frame_carries_its_own_source_and_target():
    with FakeRelay() as relay:
        _writer(relay).apply(_delta())
    edge = [o for o in relay.ops if o["op"] == "add_edge"][0]
    assert edge["source"] == "US12" and edge["target"] == "EP1"
    assert edge["edge_type"] == "has_epoch"


def test_no_author_is_sent_because_the_relay_takes_it_from_the_token():
    """P4.1b: the stamp is what the merge trusts, so it cannot be
    self-declared. A client that filled it in would be lying downstream."""
    with FakeRelay() as relay:
        _writer(relay).apply(_delta())
    for op in relay.ops:
        assert "author" not in op


def test_the_token_travels_on_the_url():
    with FakeRelay() as relay:
        _writer(relay).apply(_delta())
    assert relay.tokens == ["tok-123"]


def test_it_connects_once_per_act_not_once_per_operation():
    """A phone in a pocket must not sit in the room's roster: presence is
    ephemeral, and a held-open socket would be a claim that is not true."""
    with FakeRelay() as relay:
        _writer(relay).apply(_delta())
        assert len(relay.tokens) == 1
        _writer(relay).apply(_delta())
        assert len(relay.tokens) == 2


def test_an_empty_delta_opens_nothing():
    with FakeRelay() as relay:
        _writer(relay).apply(GraphDelta(author=ORCID))
    assert relay.tokens == []


# ── 2 · reading the ack is the difference between "sent" and "landed" ────────

def test_someone_elses_news_on_the_same_socket_is_skipped():
    with FakeRelay(noise=True) as relay:
        writer = _writer(relay)
        writer.apply(_delta())
        assert not writer.degraded
    assert len(relay.ops) == 3


def test_an_operation_the_room_did_not_apply_is_a_refusal_not_a_success():
    with FakeRelay(apply_ops=False) as relay:
        writer = _writer(relay)
        with pytest.raises(RoomRefused) as exc:
            writer.apply(_delta())
    assert "stale" in str(exc.value)
    assert writer.degraded


def test_a_read_only_role_is_refused_at_the_door_before_any_op():
    with FakeRelay(can_write=False) as relay:
        writer = _writer(relay)
        with pytest.raises(RoomRefused) as exc:
            writer.apply(_delta())
    assert "read-only" in str(exc.value)
    assert relay.ops == []          # nothing was even attempted


def test_a_refusal_does_NOT_fall_back_to_the_local_container():
    """The room applied a rule correctly. Writing the delta locally anyway would
    hide that rule and leave two copies of a study that disagree."""
    written = []

    class Local:
        def apply(self, delta):
            written.append(delta)

    with FakeRelay(apply_ops=False) as relay:
        with pytest.raises(RoomRefused):
            _writer(relay, fallback=Local()).apply(_delta())
    assert written == []


def test_an_unreachable_room_DOES_fall_back_and_says_why():
    written = []

    class Local:
        def apply(self, delta):
            written.append(delta)

    # nothing is listening on this port
    writer = RoomWriter("http://127.0.0.1:9", "saggio-b", "tok",
                        fallback=Local(), timeout=2.0)
    writer.apply(_delta())
    assert len(written) == 1
    assert writer.degraded
    assert writer.last_refusal
    assert "degraded" in describe(writer) and writer.last_refusal in describe(writer)


# ── 3 · the tool the field card actually uses ────────────────────────────────

def test_a_card_said_out_loud_reaches_the_room():
    """End to end through the registry: the sentence, the tool, the wire."""
    with FakeRelay() as relay:
        writer = _writer(relay)
        registry = build_registry(writer, InMemoryAssetStore())
        result = invoke(registry.route("create_su"), {"us": "12"}, ORCID)
        assert result.ok, result.message
    nodes = [o["node"] for o in relay.ops if o["op"] == "add_node"]
    assert any(n["id"] == "US12" for n in nodes), nodes


def test_the_3d_tool_does_not_go_through_the_wire_at_all():
    """It asks `/v1/photogrammetry` over HTTP, and this fix must not have
    changed that: a regression there would be invisible from the wire tests."""
    with FakeRelay() as relay:
        writer = _writer(relay)
        registry = build_registry(writer, InMemoryAssetStore())
        result = invoke(registry.route("build_model"), {"us": "12"}, ORCID)
    # the node is not there, so it refuses — but it refused over HTTP
    assert not result.ok
    assert result.data["reason"] == "unreachable"
    assert relay.ops == []


# ── 4 · against the REAL server, when one is up ──────────────────────────────

def _real_server():
    base = os.environ.get("EM_TEST_SERVER", "http://127.0.0.1:8000")
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(f"{base}/v1/health", timeout=2) as answer:
            return base if answer.status == 200 else None
    except (urllib.error.URLError, OSError):
        return None


def _dev_token():
    import subprocess
    helper = (Path(__file__).resolve().parent.parent.parent
              / "stratigraph-server" / "dev-stack" / "token.sh")
    if not helper.is_file():
        return None
    try:
        out = subprocess.run([str(helper)], capture_output=True, text=True,
                             timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


@pytest.mark.skipif(_real_server() is None,
                    reason="no StratiGraph Server at :8000 — start the dev stack "
                           "(stratigraph-server/dev-stack/fcn-up.sh) to measure this")
def test_a_card_lands_in_a_REAL_rooms_document():
    """The gate this whole fix exists for: not a 404 on `/op` any more.

    Creates a room, says one sentence through the registry, and reads the room's
    OWN document back over HTTP to see the node there.
    """
    import urllib.request

    base = _real_server()
    token = _dev_token()
    if not token:
        pytest.skip("dev-stack/token.sh did not produce a token")

    room = f"chatbot-wire-{int(time.time())}"
    request = urllib.request.Request(
        f"{base}/v1/rooms", method="POST",
        data=json.dumps({"room_id": room, "title": "Smoke · wire"}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=10) as answer:
        assert answer.status in (200, 201)

    writer = RoomWriter(base, room, token, timeout=15.0)
    registry = build_registry(writer, InMemoryAssetStore())
    result = invoke(registry.route("create_su"), {"us": "77"}, ORCID)
    assert result.ok, result.message
    assert not writer.degraded, writer.last_refusal

    # …and it is IN THE ROOM, read back from the server's own snapshot
    probe = urllib.request.Request(
        f"{base}/v1/rooms/{room}", method="GET",
        headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(probe, timeout=10) as answer:
        record = json.loads(answer.read())
    assert record["room_id"] == room

    found = _node_in_room(base, room, token, "US77")
    assert found, "US77 never appeared in the room's document"
    assert found["node_type"] in ("US", "us")
    # …and it is ATTRIBUTED to the token's identity, which the relay stamped —
    # the client never sent an author (see the wire test above)
    assert (found.get("data") or {}).get("created_by")

    # tidy after ourselves: a test that runs every day must not leave a room
    # every day. Archived rather than deleted — a room is a place, and the node
    # keeps its record.
    _archive(base, room, token)


def _archive(base, room, token):
    import urllib.error
    import urllib.request
    request = urllib.request.Request(
        f"{base}/v1/rooms/{room}/archive", method="POST", data=b"{}",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"})
    try:
        urllib.request.urlopen(request, timeout=10).read()
    except (urllib.error.URLError, OSError):
        pass                                   # tidying is not the measurement


def _node_in_room(base, room, token, node_id):
    """Read the room back over its own socket — the snapshot frame IS the
    document, which is what a second client would see."""
    from websockets.sync.client import connect

    ws = base.replace("http://", "ws://").replace("https://", "wss://")
    with connect(f"{ws}/v1/rooms/{room}/ws?token={token}",
                 open_timeout=10, max_size=None) as socket:
        for _ in range(5):
            message = json.loads(socket.recv())
            if message.get("type") != "snapshot":
                continue
            doc = (message.get("payload") or {}).get("doc") or {}
            for section in (doc.get("graphs") or {}).values():
                for node in (section or {}).get("nodes") or []:
                    if node.get("id") == node_id:
                        return node
    return None
