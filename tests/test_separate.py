"""/separate: whichever of a pair is in a call hides that one channel from the other.

The plan tests pin the rules down without Discord; the sync tests run the real loop
against fake channels to check overwrites go on, move and come off exactly, including
putting back an overwrite the member already had.
"""
import asyncio
import os
import sys

import discord
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.features import separate as S

A, B, C = 111, 222, 333
X, Y, STAGE = 1001, 1002, S.EXEMPT_CHANNEL_ID
PAIR = [{"a": A, "b": B}]
BLOCK = discord.Permissions.view_channel.flag | discord.Permissions.connect.flag


# ------------------------------------------------------------ plan

def test_nobody_in_voice_blocks_nothing():
    assert S.plan(PAIR, {}, [], {}) == ([], set())


def test_one_in_a_call_hides_only_that_channel_from_the_other():
    assert S.plan(PAIR, {A: X}, [], {}) == ([], {(X, B)})
    assert S.plan(PAIR, {B: Y}, [], {}) == ([], {(Y, A)})


def test_the_stage_blocks_nobody():
    assert S.plan(PAIR, {A: STAGE}, [], {}) == ([], set())


def test_different_calls_block_each_other_and_disconnect_nobody():
    assert S.plan(PAIR, {A: X, B: Y}, [], {}) == ([], {(X, B), (Y, A)})


def test_one_on_stage_one_in_a_call_only_the_call_counts():
    assert S.plan(PAIR, {A: STAGE, B: X}, [], {}) == ([], {(X, A)})


def test_same_call_drops_the_latest_arrival():
    assert S.plan(PAIR, {A: X, B: X}, [], {A: 5.0, B: 9.0}) == ([B], {(X, B)})
    assert S.plan(PAIR, {A: X, B: X}, [], {A: 9.0, B: 5.0}) == ([A], {(X, A)})


def test_same_call_with_a_block_already_on_drops_the_one_it_was_on():
    """B was blocked from X, so A was there first, whatever the clock says."""
    stored = [{"channel": X, "member": B}]
    assert S.plan(PAIR, {A: X, B: X}, stored, {A: 9.0, B: 5.0}) == ([B], {(X, B)})


def test_same_call_with_nothing_to_go_on_drops_the_second_named():
    assert S.plan(PAIR, {A: X, B: X}, [], {}) == ([B], {(X, B)})


def test_unpaired_people_are_untouched():
    assert S.plan(PAIR, {C: X}, [], {}) == ([], set())


def test_one_member_in_two_pairs():
    pairs = [{"a": A, "b": B}, {"a": A, "b": C}]
    assert S.plan(pairs, {A: X}, [], {}) == ([], {(X, B), (X, C)})


# ------------------------------------------------------------ sync against fakes

class FakeHTTP:
    def __init__(self, guild):
        self.guild = guild
        self.calls = []

    async def edit_channel_permissions(self, channel_id, target_id, allow, deny, type, *, reason=None):
        assert type == 1 and reason == "separate"
        self.calls.append(("edit", channel_id, target_id))
        self.guild.channels[channel_id].ows[target_id] = (int(allow), int(deny))

    async def delete_channel_permissions(self, channel_id, target_id, *, reason=None):
        assert reason == "separate"
        self.calls.append(("delete", channel_id, target_id))
        self.guild.channels[channel_id].ows.pop(target_id, None)


class FakeState:
    def __init__(self, http):
        self.http = http


class FakeTarget:
    def __init__(self, id):
        self.id = id


class FakeChannel:
    def __init__(self, guild, id):
        self.id = id
        self.ows = {}  # member id -> (allow, deny)
        self._state = guild.state

    @property
    def overwrites(self):
        return {FakeTarget(mid): discord.PermissionOverwrite.from_pair(discord.Permissions(a), discord.Permissions(d))
                for mid, (a, d) in self.ows.items()}


class FakeVoice:
    def __init__(self, channel):
        self.channel = channel


class FakeMember:
    def __init__(self, guild, id):
        self.guild, self.id, self.voice, self.bot = guild, id, None, False

    async def move_to(self, channel, reason=None):
        self.guild.disconnected.append(self.id)
        self.voice = None


class FakeGuild:
    def __init__(self):
        self.state = FakeState(FakeHTTP(self))
        self.channels = {cid: FakeChannel(self, cid) for cid in (X, Y, STAGE)}
        self.members = {mid: FakeMember(self, mid) for mid in (A, B, C)}
        self.disconnected = []

    def get_channel(self, cid):
        return self.channels.get(cid)

    def get_member(self, mid):
        return self.members.get(mid)

    def put(self, mid, cid):
        self.members[mid].voice = FakeVoice(self.channels[cid]) if cid else None


class VS:
    def __init__(self, channel):
        self.channel = channel


@pytest.fixture
def guild(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "STATE_FILE", str(tmp_path / "separations.json"))
    S._join_times.clear()
    return FakeGuild()


def run(coro):
    return asyncio.run(coro)


async def _move(guild, mid, cid):
    member = guild.members[mid]
    before = VS(member.voice.channel if member.voice else None)
    guild.put(mid, cid)
    await S.on_voice_state_update(member, before, VS(guild.channels.get(cid)))


def test_block_follows_the_member_and_lifts_when_they_leave(guild):
    run(S.toggle(guild, A, B, 999))
    run(_move(guild, A, X))
    assert guild.channels[X].ows[B] == (0, BLOCK)
    run(_move(guild, A, Y))
    assert B not in guild.channels[X].ows and guild.channels[Y].ows[B] == (0, BLOCK)
    run(_move(guild, A, STAGE))
    assert all(B not in ch.ows for ch in guild.channels.values())
    assert S.load_state()["blocks"] == []
    assert guild.disconnected == []


def test_an_existing_overwrite_is_put_back_exactly(guild):
    speak = discord.Permissions.speak.flag
    stream = discord.Permissions.stream.flag
    view = discord.Permissions.view_channel.flag
    guild.channels[X].ows[B] = (speak | view, stream)
    run(S.toggle(guild, A, B, 999))
    run(_move(guild, A, X))
    allow, deny = guild.channels[X].ows[B]
    assert allow == speak and deny == stream | BLOCK
    run(_move(guild, A, None))
    assert guild.channels[X].ows[B] == (speak | view, stream)


def test_toggling_on_with_both_in_one_call_drops_one_and_toggling_off_lifts(guild):
    guild.put(A, X)
    guild.put(B, X)
    assert run(S.toggle(guild, B, A, 999)) is True  # either order
    assert guild.disconnected == [A]  # nothing to go on: the second named
    assert guild.channels[X].ows[A] == (0, BLOCK)
    assert run(S.toggle(guild, A, B, 999)) is False
    assert guild.channels[X].ows == {}
    assert S.load_state() == {"pairs": [], "blocks": []}


def test_a_race_into_the_same_call_drops_the_later_event(guild):
    run(S.toggle(guild, A, B, 999))

    async def both():
        guild.put(A, X)
        guild.put(B, X)
        t1 = asyncio.create_task(S.on_voice_state_update(guild.members[A], VS(None), VS(guild.channels[X])))
        t2 = asyncio.create_task(S.on_voice_state_update(guild.members[B], VS(None), VS(guild.channels[X])))
        await asyncio.gather(t1, t2)

    run(both())
    assert guild.disconnected == [B]
    assert guild.channels[X].ows == {B: (0, BLOCK)}


def test_reconcile_after_a_restart(guild, monkeypatch):
    run(S.toggle(guild, A, B, 999))
    run(_move(guild, A, X))
    # Bot was down while A moved to Y.
    guild.put(A, Y)
    S._join_times.clear()

    class Client:
        def get_guild(self, _):
            return guild

    run(S.reconcile(Client()))
    assert B not in guild.channels[X].ows and guild.channels[Y].ows[B] == (0, BLOCK)


def test_unpaired_voice_traffic_does_nothing(guild):
    run(S.toggle(guild, A, B, 999))
    run(_move(guild, C, X))
    assert guild.state.http.calls == []


def test_only_border_force_or_oggers():
    import config

    class R:
        def __init__(self, id):
            self.id = id

    class U:
        def __init__(self, id, roles):
            self.id, self.roles = id, roles

    assert S.can_use(U(1, [R(config.ROLES.BORDER_FORCE)]))
    assert S.can_use(U(config.USERS.OGGERS, []))
    assert not S.can_use(U(1, [R(config.ROLES.CABINET)]))
