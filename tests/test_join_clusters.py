"""Batch-creation clustering: accounts made together that now arrive together."""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from commands.moderation import join_clusters as JC

NOW = 1_760_000_000


def rec(uid, created_at, joined_ago=60, name=None):
    return {"user_id": str(uid), "username": name or f"user{uid}",
            "joined_at": NOW - joined_ago, "account_created_at": created_at}


def test_accounts_created_minutes_apart_are_clustered():
    base = NOW - 120 * 86400          # three months old, like the real ones
    records = [rec(1, base), rec(2, base + 260), rec(3, base + 900), rec(4, base + 1500)]
    clusters = JC.find_clusters(records, now=NOW)
    assert len(clusters) == 1
    assert len(clusters[0]["members"]) == 4
    assert clusters[0]["spread"] == 1500


def test_a_paused_script_is_still_one_batch():
    """Real batch: 01:49, 01:53, 02:03, 02:35 - every step small, 46m end to end.
    Measuring from the first account would drop the last one."""
    base = NOW - 120 * 86400
    records = [rec(1, base), rec(2, base + 263), rec(3, base + 891), rec(4, base + 2768)]
    clusters = JC.find_clusters(records, now=NOW)
    assert len(clusters) == 1
    assert len(clusters[0]["members"]) == 4


def test_a_long_gap_starts_a_new_batch():
    """Two runs hours apart on the same day are separate registrations."""
    base = NOW - 120 * 86400
    records = [rec(1, base), rec(2, base + 120), rec(3, base + 300),
               rec(4, base + 23000), rec(5, base + 23100), rec(6, base + 23300)]
    assert len(JC.find_clusters(records, now=NOW)) == 2


def test_a_scripted_spread_is_marked_as_such():
    base = NOW - 90 * 86400
    tight = JC.find_clusters([rec(1, base), rec(2, base + 60), rec(3, base + 120)], now=NOW)
    assert tight[0]["tight"] is True and tight[0]["max_gap"] == 60
    loose = JC.find_clusters([rec(1, base), rec(2, base + 600), rec(3, base + 1500)], now=NOW)
    assert loose[0]["tight"] is False


def test_unrelated_accounts_are_not_a_cluster():
    """Ordinary joiners have creation dates scattered over years."""
    records = [rec(1, NOW - 900 * 86400), rec(2, NOW - 400 * 86400),
               rec(3, NOW - 30 * 86400), rec(4, NOW - 5 * 86400)]
    assert JC.find_clusters(records, now=NOW) == []


def test_two_accounts_alone_are_never_a_cluster():
    """Two friends signing up together is a real thing and must not trip this."""
    base = NOW - 200 * 86400
    assert JC.find_clusters([rec(1, base), rec(2, base + 30)], now=NOW) == []


def test_old_joins_fall_out_of_the_window():
    base = NOW - 200 * 86400
    stale = JC.JOIN_WINDOW_SECONDS + 3600
    records = [rec(1, base, joined_ago=stale), rec(2, base + 60, joined_ago=stale),
               rec(3, base + 120, joined_ago=stale)]
    assert JC.find_clusters(records, now=NOW) == []


def test_separate_batches_are_reported_separately():
    feb = NOW - 190 * 86400
    apr = NOW - 120 * 86400
    records = [rec(1, feb), rec(2, feb + 100), rec(3, feb + 200),
               rec(4, apr), rec(5, apr + 100), rec(6, apr + 200)]
    clusters = JC.find_clusters(records, now=NOW)
    assert len(clusters) == 2
    assert {len(c["members"]) for c in clusters} == {3}


def test_the_id_list_is_deduped_and_ordered():
    base = NOW - 150 * 86400
    clusters = JC.find_clusters([rec(7, base), rec(8, base + 60), rec(9, base + 90)], now=NOW)
    assert JC.cluster_user_ids(clusters) == ["7", "8", "9"]


def test_the_card_shows_the_ids_and_offers_no_automatic_action():
    base = NOW - 150 * 86400
    clusters = JC.find_clusters([rec(1, base), rec(2, base + 60), rec(3, base + 90)], now=NOW)
    import json
    payload = json.dumps(JC.build_cluster_view(clusters).to_components())
    for uid in ("1", "2", "3"):
        assert f"`{uid}`" in payload
    # The destructive control is present but it is a button, not something already done.
    assert "joincluster:massban" in payload
    assert "not proof" in payload, "the card must caveat the evidence"


def test_a_banned_report_drops_the_ban_button():
    """Once actioned the card must not offer to ban the same people again."""
    base = NOW - 150 * 86400
    clusters = JC.find_clusters([rec(1, base), rec(2, base + 60), rec(3, base + 90)], now=NOW)
    import json
    payload = json.dumps(JC.build_cluster_view(clusters, banned=["1", "2", "3"]).to_components())
    assert "joincluster:massban" not in payload
    assert "banned" in payload


def test_staff_check_rejects_ordinary_members():
    class R:
        def __init__(self, rid): self.id = rid

    class U:
        def __init__(self, roles): self.roles = roles

    assert JC._is_staff(U([R(next(iter(JC.STAFF_ROLE_IDS)))])) is True
    assert JC._is_staff(U([R(1)])) is False
    assert JC._is_staff(U([])) is False


def test_one_member_rejoining_is_never_a_cluster():
    """A leave-and-rejoin wrote a record each time, so one person read as several accounts
    created 0s apart - which is exactly the shape flagged as scripted."""
    base = NOW - 5 * 86400
    same = [rec(1, base, joined_ago=600), rec(1, base, joined_ago=400),
            rec(1, base, joined_ago=200), rec(1, base, joined_ago=60)]
    assert JC.find_clusters(same, now=NOW) == []


def test_duplicates_do_not_inflate_a_real_cluster():
    base = NOW - 120 * 86400
    records = [rec(1, base), rec(1, base, joined_ago=30), rec(1, base, joined_ago=10),
               rec(2, base + 120), rec(3, base + 300), rec(3, base + 300, joined_ago=5)]
    clusters = JC.find_clusters(records, now=NOW)
    assert len(clusters) == 1
    assert len(clusters[0]["members"]) == 3
    # Ordered by join time, not id - that is the order the card lists them in.
    assert sorted(JC.cluster_user_ids(clusters)) == ["1", "2", "3"]


def test_the_latest_join_is_the_one_kept():
    base = NOW - 100 * 86400
    records = [rec(1, base, joined_ago=9000), rec(1, base, joined_ago=60),
               rec(2, base + 60), rec(3, base + 120)]
    clusters = JC.find_clusters(records, now=NOW)
    member = next(m for m in clusters[0]["members"] if m["user_id"] == "1")
    assert member["joined_at"] == NOW - 60


def test_the_card_leads_with_the_claim_and_ranks_the_worst_batch_first():
    import json
    base_loose = NOW - 200 * 86400
    base_tight = NOW - 200 * 86400 - 900000
    records = ([rec(1, base_loose), rec(2, base_loose + 600), rec(3, base_loose + 1500)]
               + [rec(4, base_tight), rec(5, base_tight + 30), rec(6, base_tight + 60)])
    clusters = JC.find_clusters(records, now=NOW)
    payload = json.dumps(JC.build_cluster_view(clusters, now=NOW).to_components())
    # Says what it means before it says what it found.
    assert "were made together" in payload
    assert "account farm" in payload
    # The scripted batch is labelled and comes before the loose one.
    assert "LIKELY SCRIPTED" in payload and "WORTH A LOOK" in payload
    assert payload.index("LIKELY SCRIPTED") < payload.index("WORTH A LOOK")


def test_each_batch_gets_its_own_ban_button():
    """Five batches of differing strength must not force an all-or-nothing call."""
    import json
    a = NOW - 200 * 86400
    b = NOW - 100 * 86400
    records = [rec(1, a), rec(2, a + 60), rec(3, a + 120),
               rec(4, b), rec(5, b + 60), rec(6, b + 120)]
    clusters = JC.find_clusters(records, now=NOW)
    payload = json.dumps(JC.build_cluster_view(clusters, now=NOW).to_components())
    assert payload.count("joincluster:ban:") == 2, "one button per batch"
    assert "joincluster:massban" in payload, "plus the ban-all"


def test_a_part_banned_report_only_offers_what_is_left():
    import json
    a = NOW - 200 * 86400
    clusters = JC.find_clusters([rec(1, a), rec(2, a + 60), rec(3, a + 120)], now=NOW)
    payload = json.dumps(JC.build_cluster_view(clusters, banned=["1"], now=NOW).to_components())
    assert "Ban these 2" in payload
    done = json.dumps(JC.build_cluster_view(clusters, banned=["1", "2", "3"],
                                            now=NOW).to_components())
    assert "joincluster:" not in done
    assert "have been banned" in done


def _payload(clusters, **kw):
    import json
    # ensure_ascii=False so emoji markers are searchable as themselves.
    return json.dumps(JC.build_cluster_view(clusters, now=NOW, **kw).to_components(),
                      ensure_ascii=False)


def _one_batch():
    base = NOW - 200 * 86400
    return JC.find_clusters([rec(1, base), rec(2, base + 60), rec(3, base + 120)], now=NOW)


def test_banning_is_not_the_only_option():
    """A batch a coincidence away from innocent needs a reversible response."""
    payload = _payload(_one_batch())
    for control in ("joincluster:ban:", "joincluster:quar:",
                    "joincluster:watch", "joincluster:dismiss", "joincluster:massban"):
        assert control in payload, control


def test_the_buttons_explain_what_each_one_does():
    payload = _payload(_one_batch())
    assert "restricts them but leaves them here" in payload
    assert "screens their first messages" in payload


def test_quarantined_and_watched_members_are_marked_not_struck_through():
    quar = _payload(_one_batch(), quarantined=["1"])
    assert "🔒" in quar
    watched = _payload(_one_batch(), watching=["2"])
    assert "👁️" in watched
    # Neither is a removal, so the row must stay actionable.
    assert "joincluster:ban:" in quar and "joincluster:ban:" in watched


def test_dismissing_records_who_and_removes_every_control():
    payload = _payload(_one_batch(), dismissed_by="4321")
    assert "not a raid" in payload.lower()
    assert "<@4321>" in payload
    assert "joincluster:" not in payload, "a dismissed report must not still offer actions"


def test_each_batch_ban_sits_inline_with_its_own_batch():
    """A Components V2 Section pins the button to the text it acts on, so there is no
    ambiguity about which accounts 'Ban these 3' means."""
    comps = JC.build_cluster_view(_one_batch(), now=NOW).to_components()

    def sections(node):
        if isinstance(node, dict):
            if node.get("type") == 9:          # Section
                yield node
            for key in ("components", "accessory"):
                yield from sections(node.get(key))
        elif isinstance(node, list):
            for item in node:
                yield from sections(item)

    found = list(sections(comps))
    assert found, "the batch should render as a Section, not a bare TextDisplay"
    accessory = found[0].get("accessory") or {}
    assert accessory.get("custom_id", "").startswith("joincluster:ban:")


# ---------------------------------------------------------------------------
# A report that has scrolled away is a report nobody acts on.
# ---------------------------------------------------------------------------
class _FakeMsg:
    def __init__(self, mid, channel):
        self.id = mid
        self.channel = channel
        self.jump_url = f"https://discord.com/channels/1/2/{mid}"
        self.edits = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)


class _FakeChannel:
    """A police station with `busy` messages posted after the last report."""

    def __init__(self, busy=0, existing=None):
        self.id = 2
        self.busy = busy
        self.sent = []
        self.existing = existing
        self._next = 1000

    async def fetch_message(self, mid):
        if self.existing is None or int(mid) != self.existing.id:
            raise RuntimeError("not found")
        return self.existing

    def history(self, after=None, limit=None):
        count = min(self.busy, limit or self.busy)

        async def gen():
            for i in range(count):
                yield _FakeMsg(9000 + i, self)
        return gen()

    async def send(self, **kwargs):
        self._next += 1
        msg = _FakeMsg(self._next, self)
        self.sent.append(kwargs)
        return msg


def test_a_quiet_channel_edits_the_report_in_place(monkeypatch, tmp_path):
    import asyncio
    monkeypatch.setattr(JC, "CLUSTER_STATE_FILE", str(tmp_path / "clusters.json"))
    previous = _FakeMsg(500, None)
    channel = _FakeChannel(busy=3, existing=previous)
    JC._save_state({"message_id": 500, "channel_id": 2, "signature": "old", "clusters": []})

    async def fake_channel(_client, _cid):
        return channel

    monkeypatch.setattr(JC, "_get_channel", fake_channel)
    base = NOW - 200 * 86400
    records = [rec(1, base), rec(2, base + 60), rec(3, base + 120)]
    asyncio.run(JC.evaluate_joins(object(), records, now=NOW))

    assert previous.edits, "a visible report should be updated where it is"
    assert not channel.sent, "and must not be reposted"


def test_a_buried_report_is_reposted_and_the_old_one_points_at_it(monkeypatch, tmp_path):
    import asyncio, json
    monkeypatch.setattr(JC, "CLUSTER_STATE_FILE", str(tmp_path / "clusters.json"))
    previous = _FakeMsg(500, None)
    channel = _FakeChannel(busy=JC.REPOST_AFTER_MESSAGES, existing=previous)
    JC._save_state({"message_id": 500, "channel_id": 2, "signature": "old", "clusters": []})

    async def fake_channel(_client, _cid):
        return channel

    monkeypatch.setattr(JC, "_get_channel", fake_channel)
    base = NOW - 200 * 86400
    records = [rec(1, base), rec(2, base + 60), rec(3, base + 120)]
    asyncio.run(JC.evaluate_joins(object(), records, now=NOW))

    assert channel.sent, "a buried report must be posted again at the bottom"
    stub = json.dumps(previous.edits[-1]["view"].to_components(), ensure_ascii=False)
    assert "Superseded" in stub
    assert "https://discord.com/channels/" in stub
    # The stale card must not still offer to ban anyone.
    assert "joincluster:" not in stub
    # State now tracks the new message.
    assert JC._load_state()["message_id"] == channel._next


def test_rows_mention_the_account_so_staff_can_open_the_profile():
    payload = _payload(_one_batch())
    for uid in ("1", "2", "3"):
        assert f"<@{uid}>" in payload, f"row for {uid} should mention them"
        assert f"`{uid}`" in payload, "the raw id stays for manual follow-up"
    # The name recorded at join survives a later rename or a leave.
    assert "user1" in payload


# ---------------------------------------------------------------------------
# Appeals: this ban is issued on a pattern, so the one case it cannot rule out
# is the innocent one.
# ---------------------------------------------------------------------------
class _FakeMember:
    def __init__(self, uid):
        self.id = uid
        self.dms = []

    async def send(self, **kwargs):
        self.dms.append(kwargs)


class _FakeGuild:
    def __init__(self, members=(), dm_fails=False):
        self._members = {m.id: m for m in members}
        self.banned = []
        self.unbanned = []
        self.dm_fails = dm_fails

    def get_member(self, uid):
        m = self._members.get(int(uid))
        if m and self.dm_fails:
            async def boom(**kwargs):
                raise RuntimeError("DMs closed")
            m.send = boom
        return m

    async def ban(self, obj, reason=None, delete_message_seconds=0):
        self.banned.append(int(obj.id))

    async def unban(self, obj, reason=None):
        self.unbanned.append(int(obj.id))


def test_the_dm_goes_out_before_the_ban_or_it_never_arrives(monkeypatch, tmp_path):
    """Once banned we no longer share a guild, so Discord refuses the DM."""
    import asyncio
    monkeypatch.setattr(JC, "APPEALS_FILE", str(tmp_path / "appeals.json"))
    member = _FakeMember(7)
    guild = _FakeGuild([member])
    order = []
    real_ban = guild.ban

    async def tracked_ban(obj, **kw):
        order.append("ban")
        await real_ban(obj, **kw)

    guild.ban = tracked_ban
    original_send = member.send

    async def tracked_send(**kw):
        order.append("dm")
        await original_send(**kw)

    member.send = tracked_send
    banned, failed = asyncio.run(JC.ban_ids(guild, ["7"], _FakeMember(1)))
    assert banned == ["7"] and not failed
    assert order == ["dm", "ban"], "the notice must precede the ban"


def test_a_closed_dm_does_not_stop_the_ban(monkeypatch, tmp_path):
    import asyncio
    monkeypatch.setattr(JC, "APPEALS_FILE", str(tmp_path / "appeals.json"))
    guild = _FakeGuild([_FakeMember(8)], dm_fails=True)
    banned, failed = asyncio.run(JC.ban_ids(guild, ["8"], _FakeMember(1)))
    assert banned == ["8"] and guild.banned == [8]


def test_the_dm_carries_an_appeal_button_keyed_to_the_user():
    import json
    payload = json.dumps(JC._ban_dm_view(4242).to_components(), ensure_ascii=False)
    assert "joincluster:appeal:4242" in payload
    assert "isn't you" in payload, "it must invite them to say it was wrong"
    assert "no time limit" in payload


def test_the_appeal_button_survives_a_restart_from_its_custom_id():
    """No in-memory state: the id travels in the custom_id, so an old DM still works."""
    match = JC.AppealButton.__discord_ui_compiled_template__.fullmatch(
        "joincluster:appeal:99")
    assert match and match["uid"] == "99"


def test_a_second_appeal_is_refused_once_one_is_pending(monkeypatch, tmp_path):
    monkeypatch.setattr(JC, "APPEALS_FILE", str(tmp_path / "appeals.json"))
    JC._save_appeals({"5": {"status": "pending", "text": "please", "at": 1}})
    assert JC._load_appeals()["5"]["status"] == "pending"
    JC._save_appeals({"5": {"status": "rejected"}})
    assert JC._load_appeals()["5"]["status"] == "rejected"
