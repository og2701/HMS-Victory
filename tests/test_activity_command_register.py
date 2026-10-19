"""The activity's launch commands are registered through Discord's commands API, which rate-limits bursts:
a 429 is waited out and retried, so a longer command list still all gets created."""
import asyncio

from lib.activities import auth, launcher


class FakeResp:
    def __init__(self, status, body):
        self.status, self._body, self.content_type = status, body, "application/json"

    async def json(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeSession:
    """Answers POSTs with 429 once every third call (until retried), GET with one stale command."""
    def __init__(self):
        self.calls, self.created, self.deleted, self.limited = [], [], [], set()

    def request(self, method, url, **kw):
        self.calls.append(method)
        if method == "POST":
            name = kw["json"]["name"]
            if len(self.created) % 3 == 2 and name not in self.limited:
                self.limited.add(name)
                return FakeResp(429, {"retry_after": 0.01})
            self.created.append(name)
            return FakeResp(200, {})
        if method == "GET":
            return FakeResp(200, [{"id": "9", "type": 1, "name": "test-old"}, {"id": "1", "type": 4, "name": "launch"}])
        self.deleted.append(url.rsplit("/", 1)[-1])
        return FakeResp(204, None)


def test_rate_limited_registrations_are_retried(monkeypatch):
    monkeypatch.setattr(auth, "_env", lambda k: "x")
    wanted = {f"g{i}": ("wordle", "a game") for i in range(7)}
    monkeypatch.setattr(launcher, "_commands", lambda: wanted)
    s = FakeSession()
    asyncio.run(launcher._register(s))
    assert s.created == list(wanted)                 # every command made it, the limited ones on a retry
    assert len(s.limited) >= 2 and s.deleted == ["9"]   # the stale typed command goes; the Entry Point stays


def test_commands_discord_already_has_are_not_sent_again(monkeypatch):
    """A plain restart shouldn't spend Discord's rate limit re-sending identical commands."""
    monkeypatch.setattr(auth, "_env", lambda k: "x")
    monkeypatch.setattr(launcher, "_commands", lambda: {"games": ("home", "Open HMS Games"),
                                                         "wordle": ("wordle", "Play Wordle")})

    class Session(FakeSession):
        def request(self, method, url, **kw):
            if method == "GET":
                self.calls.append(method)
                return FakeResp(200, [
                    {"id": "1", "type": 1, "name": "games", "description": "Open HMS Games",
                     "integration_types": [0], "contexts": [0]},
                    {"id": "2", "type": 1, "name": "wordle", "description": "An old description",
                     "integration_types": [0], "contexts": [0]},
                    {"id": "3", "type": 4, "name": "launch"},
                ])
            return super().request(method, url, **kw)

    s = Session()
    asyncio.run(launcher._register(s))
    assert s.created == ["wordle"]      # only the one whose description changed
    assert s.deleted == []              # nothing stale, and the Entry Point stays
