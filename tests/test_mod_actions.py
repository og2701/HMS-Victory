"""The action buttons that sit on automated moderation reports.

One set of four serves every detector, so the tests cover both that a button does what it
says and that the report kind riding in the custom_id survives the round trip - a mix-up
there would tell a banned member the wrong reason, or hand Ban's job to Ignore.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord

from lib.core import mod_actions as M


class _Resp:
    status = 404
    reason = "Not Found"


def _run(coro):
    # Closed explicitly: an abandoned loop is finalised at interpreter shutdown, which
    # raises out of the selector and fails the whole run after every test has passed.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# --- the staff buttons -------------------------------------------------------------------

class FakeMessage:
    def __init__(self, embed):
        self.embeds = [embed]
        self.edits = []
        self.replies = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)

    async def reply(self, content=None, **kwargs):
        self.replies.append((content, kwargs))


class FakeResponse:
    def __init__(self, log):
        self.log = log

    async def send_message(self, content=None, **kwargs):
        self.log.append(("replied", content))

    async def defer(self, **kwargs):
        self.log.append(("deferred", None))


class FakeUser:
    def __init__(self, staff):
        self.id = 42
        self.mention = "<@42>"
        from lib.core.behaviour_watch import STAFF_ROLE_IDS
        self.roles = [type("R", (), {"id": r})()
                      for r in (list(STAFF_ROLE_IDS)[:1] if staff else [1])]

    def __str__(self):
        return "mod#0001"


class FakeMember:
    def __init__(self, uid, dms_open=True):
        self.id = uid
        self.dms_open = dms_open
        self.dms = []

    async def send(self, **kwargs):
        if not self.dms_open:
            raise RuntimeError("Cannot send messages to this user")
        self.dms.append(kwargs)


class FakeGuild:
    def __init__(self, member=None):
        self.bans = []
        self.member = member

    async def ban(self, obj, **kwargs):
        self.bans.append((obj.id, kwargs.get("reason")))

    def get_member(self, _uid):
        return self.member

    async def fetch_member(self, _uid):
        if self.member is None:
            raise discord.NotFound(_Resp(), "unknown member")
        return self.member


class FakeButtonInteraction:
    def __init__(self, staff=True, embed=None, member=None):
        self.log = []
        self.user = FakeUser(staff)
        self.guild = FakeGuild(member)
        self.response = FakeResponse(self.log)
        self.message = FakeMessage(embed or discord.Embed(title="report", description="x"))
        self.followup = type("F", (), {
            "send": lambda _s, content=None, **kw: _noop(self.log, content)})()


async def _noop(log, content):
    log.append(("followup", content))


def test_every_button_matches_only_its_own_template():
    """Four DynamicItems sharing a prefix - one sloppy pattern and Ban answers Ignore."""
    buttons = [M.ModBanButton("dmspam", 7), M.ModTimeoutButton("dmspam", 7),
               M.ModAnalyseButton("dmspam", 7), M.ModIgnoreButton("dmspam", 7)]
    for b in buttons:
        cid = b.item.custom_id
        pat = lambda o: o.__class__.__discord_ui_compiled_template__
        matched = [o.__class__.__name__ for o in buttons if pat(o).match(cid)]
        assert matched == [b.__class__.__name__], f"{cid} also matched {matched}"
        assert pat(b).match(cid)["uid"] == "7", cid


def test_the_alert_carries_all_four_actions():
    view = M.action_view("dmspam", 7)
    labels = [c.item.label for c in view.children]
    assert len(labels) == 4, labels
    assert any("Ban" in l for l in labels) and any("Time out" in l for l in labels)
    assert "Analyse" in labels and "Ignore" in labels


def test_a_non_staff_click_bans_nobody():
    i = FakeButtonInteraction(staff=False)
    _run(M.ModBanButton("dmspam", 7).callback(i))
    assert i.guild.bans == [], "a non-staff click went through"
    assert i.log and i.log[0] == ("replied", "Staff only.")


def test_banning_goes_by_id_so_it_works_after_they_leave():
    """get_member returns None here, as it would for someone already gone. A ban keyed on
    the member object would quietly fail exactly when it matters."""
    i = FakeButtonInteraction(staff=True)
    _run(M.ModBanButton("dmspam", 7).callback(i))
    assert i.guild.bans and i.guild.bans[0][0] == 7, i.guild.bans
    assert "unusual DM" in i.guild.bans[0][1]


def test_acting_greys_the_buttons_out_rather_than_removing_them():
    """A report that loses its buttons reads as though it was never actionable, and you can
    no longer see what the other options were."""
    i = FakeButtonInteraction(staff=True)
    _run(M.ModBanButton("dmspam", 7).callback(i))
    edit = i.message.edits[-1]
    view = edit["view"]
    assert view is not None and len(view.children) == 4, "the buttons were taken away"
    assert all(c.item.disabled for c in view.children), "the buttons are still clickable"
    handled = [f for f in edit["embed"].fields if f.name == "Handled"]
    assert handled and "<@42>" in handled[0].value, "the alert does not say who acted"


def test_a_fresh_alert_has_live_buttons():
    assert not any(c.item.disabled for c in M.action_view("dmspam", 7).children)


def test_the_appeal_dm_goes_out_before_the_ban_not_after():
    """Once the ban lands we no longer share a guild and Discord drops the DM, so an appeal
    notice sent afterwards reaches nobody at all."""
    m = FakeMember(7)
    i = FakeButtonInteraction(staff=True, member=m)
    _run(M.ModBanButton("dmspam", 7).callback(i))
    assert m.dms, "they were banned without ever being told why"
    assert i.guild.bans, "the DM went out but the ban did not"


def test_the_appeal_dm_explains_the_real_reason():
    """Reusing the cluster appeal wholesale would tell a hijacked account it joined in a
    batch, which is not true and not something they can answer."""
    body = M.DM_SPAM.ban_dm.lower()
    assert "unusual dm activity" in body, M.DM_SPAM.ban_dm
    assert "appeal" in body, "no route back for an account"
    assert "registered within a few minutes" not in body, "that is the cluster reason"


def test_closed_dms_do_not_stop_the_ban():
    m = FakeMember(7, dms_open=False)
    i = FakeButtonInteraction(staff=True, member=m)
    _run(M.ModBanButton("dmspam", 7).callback(i))
    assert i.guild.bans, "a closed DM blocked the ban"
    handled = [f for f in i.message.edits[-1]["embed"].fields if f.name == "Handled"][0]
    assert "could not DM" in handled.value, "the report does not say the notice failed"



def test_the_report_kind_survives_the_round_trip():
    """After a restart the button is rebuilt from the custom_id alone. Lose the kind and a
    DM-spam ban tells the member they answered onboarding like a script."""
    for slug in M.KINDS:
        b = M.ModBanButton(slug, 12345)
        m = M.ModBanButton.__discord_ui_compiled_template__.match(b.item.custom_id)
        assert m and m["kind"] == slug and m["uid"] == "12345", b.item.custom_id


def test_every_kind_explains_its_own_ban():
    """Sharing one notice would tell a hijacked account it answered onboarding like a bot."""
    bodies = [k.ban_dm for k in M.KINDS.values()]
    assert len(set(bodies)) == len(bodies), "two kinds share a ban notice"
    for k in M.KINDS.values():
        assert "appeal" in k.ban_dm.lower(), f"{k.slug} offers no way back"
    assert "onboarding" in M.ONBOARDING.ban_dm.lower()
    assert "unusual dm activity" in M.DM_SPAM.ban_dm.lower()


def test_an_unknown_kind_still_bans_and_still_appeals():
    """An old report from a detector since renamed must not crash the button."""
    i = FakeButtonInteraction(staff=True, member=FakeMember(7))
    _run(M.ModBanButton("somethingelse", 7).callback(i))
    assert i.guild.bans, "an unrecognised kind stopped the ban"
    assert i.guild.bans[0][1].startswith("Automated moderation report")


def test_the_onboarding_report_gets_the_same_row():
    view = M.action_view(M.ONBOARDING, 7)
    assert [c.item.custom_id for c in view.children] == [
        "mod:ban:onboard:7", "mod:to:onboard:7",
        "mod:analyse:onboard:7", "mod:ignore:onboard:7"]


def test_a_components_v2_report_is_answered_in_the_channel():
    """Join-watch reports keep their text in the layout, so there is no embed to annotate.
    Without this the button banned somebody and left no visible trace at all."""
    i = FakeButtonInteraction(staff=True, member=FakeMember(7))
    i.message.embeds = []
    _run(M.ModBanButton("joinwatch", 7).callback(i))
    assert i.guild.bans, "the ban did not happen"
    assert i.message.replies, "the report says nothing about having been actioned"
    assert "Banned by" in i.message.replies[0][0], i.message.replies


def test_the_join_watch_notice_is_about_what_they_posted():
    body = M.JOIN_WATCH.ban_dm.lower()
    assert "screened" in body and "appeal" in body, M.JOIN_WATCH.ban_dm
    assert "direct messages" not in body and "onboarding" not in body, "wrong reason"


def test_a_narrowed_row_stays_narrow_after_it_is_handled():
    """A report that never offered Ban must not sprout a greyed-out Ban the moment somebody
    presses Ignore - the row is part of how serious the finding reads."""
    view = M.action_view(M.VOICE_RUSH, 7,
                         only=(M.ModTimeoutButton, M.ModAnalyseButton, M.ModIgnoreButton))
    ignore = next(c for c in view.children if isinstance(c, M.ModIgnoreButton))
    i = FakeButtonInteraction(staff=True)
    _run(ignore.callback(i))
    after = i.message.edits[-1]["view"]
    assert len(after.children) == 3, [c.item.label for c in after.children]
    assert not any(isinstance(c, M.ModBanButton) for c in after.children)
    assert all(c.item.disabled for c in after.children)


def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {name}: {e}")
        except Exception as e:
            import traceback
            print(f"ERROR {name}: {e!r}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
