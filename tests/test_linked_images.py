"""Pictures that arrive as links or embeds (a GIF from the picker), and the planner refusing a job Jev read clearly."""
import asyncio
import io
from types import SimpleNamespace

from lib.features import chat_responder as cr

BOT = 1171842947440967770


def _msg(content="", embeds=None, attachments=None, author_id=1):
    return SimpleNamespace(content=content, embeds=embeds or [], attachments=attachments or [],
                           author=SimpleNamespace(id=author_id))


def _gif_embed(still="https://static.klipy.com/cat/still.png"):
    return SimpleNamespace(thumbnail=SimpleNamespace(url=still), image=None, title="fat cat")


def test_gif_link_embed_is_offered_as_a_picture():
    m = _msg("https://klipy.com/gifs/cat-123", embeds=[_gif_embed()])
    assert cr.own_image_attachments(m) == []
    imgs = cr.message_images(m)
    assert [i.url for i in imgs] == ["https://static.klipy.com/cat/still.png"]
    assert imgs[0].filename == "fat cat"


def test_bare_image_url_in_text_counts_when_there_is_no_embed():
    m = _msg("look https://example.com/a/cat.jpg?x=1 lol")
    assert [i.url for i in cr.linked_images(m)] == ["https://example.com/a/cat.jpg?x=1"]


def test_uploads_come_first_and_plain_text_has_no_pictures():
    att = SimpleNamespace(filename="a.png", content_type="image/png", url="https://cdn/a.png")
    m = _msg("x", embeds=[_gif_embed()], attachments=[att])
    assert cr.message_images(m)[0] is att
    assert cr.message_images(_msg("just words")) == []


def test_linked_images_compare_by_url():
    a, b = cr.LinkedImage("https://x/y.gif"), cr.LinkedImage("https://x/y.gif")
    assert a == b and a in [b] and a != cr.LinkedImage("https://x/z.gif")


def test_gif_is_read_as_a_still_png(monkeypatch):
    from PIL import Image
    buf = io.BytesIO()
    frames = [Image.new("P", (8, 8), c) for c in (1, 2)]
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:])
    monkeypatch.setattr(cr, "download_image_bytes", lambda url, **k: buf.getvalue())
    data = asyncio.run(cr.LinkedImage("https://x/y.gif").read())
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_png_bytes_pass_through_untouched():
    assert cr._still_png(b"\x89PNG rest") == b"\x89PNG rest"


def test_a_message_that_is_only_a_link_is_not_a_request():
    assert cr._request_words(_msg("https://klipy.com/gifs/cat-123"), BOT) == ""
    assert cr._request_words(_msg(f"<@{BOT}> make this cat fatter"), BOT) == "make this cat fatter"
    assert "hat" in cr._request_words(_msg("give it a hat https://x/y.png"), BOT)


def _signals(action, conf):
    return SimpleNamespace(action=action, action_confidence=conf)


def test_planner_refusal_does_not_override_a_confident_edit():
    plan = {"action": "reply", "request": "I can't edit external links or images not attached here.", "edit_source": None}
    cr.reconcile_plan_with_signals(plan, _signals("edit", 0.94), has_picture=True)
    assert plan["action"] == "edit" and plan["edit_source"] == "attachment" and plan["request"] == ""


def test_refused_edit_with_nothing_to_edit_becomes_a_fresh_picture():
    plan = {"action": "reply", "request": "Sorry, I cannot do that."}
    cr.reconcile_plan_with_signals(plan, _signals("edit", 0.9), has_picture=False)
    assert plan["action"] == "generate"


def test_an_ordinary_reply_plan_is_left_alone():
    plan = {"action": "reply", "request": "what is the weather in Leeds"}
    cr.reconcile_plan_with_signals(plan, _signals("edit", 0.99), has_picture=True)
    assert plan == {"action": "reply", "request": "what is the weather in Leeds"}


def test_an_unsure_jev_does_not_overrule_but_the_refusal_text_is_still_dropped():
    plan = {"action": "reply", "request": "I can't edit that."}
    cr.reconcile_plan_with_signals(plan, _signals("edit", 0.5), has_picture=True)
    assert plan["action"] == "reply" and plan["request"] == ""
    cr.reconcile_plan_with_signals({"action": "reply", "request": "I can't"}, None, has_picture=False)


def test_the_reply_persona_no_longer_sends_people_away_to_ask_again():
    from lib.features import vic_brain
    assert 'starting with "draw"' not in vic_brain.PERSONA
