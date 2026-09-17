import asyncio
import os
import unittest
from unittest.mock import patch

import aiohttp

from lib.features import mention_signals as ms
from lib.features.mention_signals import (
    MentionSignals,
    QUESTIONS,
    build_state,
    judge_mention,
    parse_signals,
)


def _answers(action="reply", confidence=0.95, probs=None, count=None, count_conf=0.9, **nouls):
    base = {k: 0.05 for k in ms._NOUL_KEYS}
    base.update(nouls)
    out = {
        "action": {
            "type": "choice",
            "choice": action,
            "confidence": confidence,
            "probabilities": probs or {"generate": 0.02, "edit": 0.03, "reply": 0.95},
        },
        "group_count": {
            "type": "choice",
            "choice": "not_given" if count is None else str(count),
            "confidence": count_conf,
            "probabilities": {},
        },
    }
    for k, v in base.items():
        out[k] = {"type": "noul", "noul": v}
    return out


def _signals(**kw) -> MentionSignals:
    return parse_signals(_answers(**kw), {"input_tokens": 300, "output_tokens": 0})


def _with_data(answers, metric="shutcoins", metric_conf=0.9, shape="leaderboard", shape_conf=0.9,
               limit="10", limit_conf=0.9, window="all_time", game="any", subject="not_applicable", lowest=0.05):
    answers["data_metric"] = {"type": "choice", "choice": metric, "confidence": metric_conf, "probabilities": {}}
    answers["data_shape"] = {"type": "choice", "choice": shape, "confidence": shape_conf, "probabilities": {}}
    answers["data_limit"] = {"type": "choice", "choice": limit, "confidence": limit_conf, "probabilities": {}}
    answers["data_window"] = {"type": "choice", "choice": window, "confidence": 0.9, "probabilities": {}}
    answers["data_game"] = {"type": "choice", "choice": game, "confidence": 0.9, "probabilities": {}}
    answers["data_subject"] = {"type": "choice", "choice": subject, "confidence": 0.9, "probabilities": {}}
    answers["data_lowest"] = {"type": "noul", "noul": lowest}
    return answers


class _FakeResponse:
    def __init__(self, status, body=None, text=""):
        self.status = status
        self._body = body
        self._text = text

    async def json(self):
        return self._body

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    """Stands in for aiohttp.ClientSession: records payloads, replays a queue of responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class TestQuestionsAndState(unittest.TestCase):
    def test_question_batch_covers_every_signal(self):
        for k in ms._NOUL_KEYS:
            self.assertEqual(QUESTIONS[k]["type"], "noul", k)
        self.assertEqual(QUESTIONS["action"]["type"], "choice")
        self.assertEqual(set(QUESTIONS["action"]["criteria"]), set(ms.ACTIONS))
        self.assertEqual(QUESTIONS["group_count"]["type"], "choice")
        self.assertIn("not_given", QUESTIONS["group_count"]["criteria"])
        self.assertIn("12", QUESTIONS["group_count"]["criteria"])
        self.assertIn("more_than_12", QUESTIONS["group_count"]["criteria"])

    def test_build_state_names_every_part(self):
        state = build_state(
            "pls do this",
            caller_name="Oggers",
            mentioned_names=["Johnny"],
            has_reply_ref=True,
            replied_to_text="make him a fursona",
            replied_to_author="Kim",
            reply_chain=[("Kim", "make him a fursona"), ("HMS Victory", "[image]")],
            has_attached_image=True,
            has_recent_bot_image=True,
            recent_bot_image_prompt="Johnny as a goblin",
            recent_history="Oggers: draw johnny\nHMS Victory: [Generated Image: goblin]",
        )
        self.assertEqual(state["message"]["text"], "pls do this")
        self.assertEqual(state["message"]["author"], "Oggers")
        self.assertEqual(state["message"]["mentioned_users"], ["Johnny"])
        self.assertTrue(state["message"]["is_discord_reply"])
        self.assertTrue(state["message"]["attached_image"])
        self.assertEqual(state["replied_to"], {"author": "Kim", "text": "make him a fursona"})
        self.assertEqual(state["reply_chain"][1], {"author": "HMS Victory", "text": "[image]"})
        self.assertEqual(state["bot_recent_image"], {"posted": True, "description": "Johnny as a goblin"})
        self.assertIn("draw johnny", state["recent_chat"])
        self.assertIn("HMS Victory", state["bot"])

    def test_build_state_without_a_reply_or_image(self):
        state = build_state("hello", caller_name="Oggers")
        self.assertIsNone(state["replied_to"])
        self.assertEqual(state["reply_chain"], [])
        self.assertEqual(state["bot_recent_image"], {"posted": False, "description": None})
        self.assertFalse(state["message"]["attached_image"])


class TestParseSignals(unittest.TestCase):
    def test_parses_a_full_answer_set(self):
        sig = _signals(action="generate", confidence=0.8, bot_self=0.9, live_query=0.1, count=5)
        self.assertEqual(sig.action, "generate")
        self.assertAlmostEqual(sig.action_confidence, 0.8)
        self.assertAlmostEqual(sig.bot_self, 0.9)
        self.assertEqual(sig.group_count, 5)
        self.assertEqual(sig.input_tokens, 300)
        self.assertEqual(sig.output_tokens, 0)

    def test_missing_noul_is_no_verdict(self):
        answers = _answers()
        del answers["live_query"]
        self.assertIsNone(parse_signals(answers, {}))

    def test_unknown_action_is_no_verdict(self):
        self.assertIsNone(parse_signals(_answers(action="refuse"), {}))

    def test_group_count_needs_a_peaked_distribution(self):
        self.assertIsNone(_signals(count=5, count_conf=0.3).group_count)
        self.assertEqual(_signals(count=5, count_conf=0.9).group_count, 5)
        self.assertEqual(_signals(count="more_than_12").group_count, 12)
        self.assertIsNone(_signals().group_count)

    def test_says_uses_the_threshold_and_only_for_nouls(self):
        sig = _signals(text_creation=0.5, delegation=0.49)
        self.assertTrue(sig.says("text_creation"))
        self.assertFalse(sig.says("delegation"))
        with self.assertRaises(KeyError):
            sig.says("action")

    def test_confident_reply_gate(self):
        self.assertTrue(_signals(action="reply", confidence=0.9).confident_reply())
        self.assertFalse(_signals(action="reply", confidence=0.6).confident_reply())
        self.assertFalse(_signals(action="generate", confidence=0.99).confident_reply())

    def test_as_intent_matches_the_classifier_shape(self):
        sig = _signals(action="generate", bot_self=0.8)
        intent = sig.as_intent()
        self.assertEqual(intent["intent"], "generate")
        self.assertEqual(intent["subject"], "bot")
        self.assertIsNone(intent["subject_name"])
        self.assertEqual(intent["input_tokens"], 0)
        self.assertEqual(_signals(action="generate", random_pick=0.7).as_intent()["subject"], "random")
        self.assertEqual(_signals(action="generate", group=0.7).as_intent()["subject"], "group")
        self.assertIsNone(_signals(action="generate").as_intent()["subject"])


class TestDataSignals(unittest.TestCase):
    def test_batch_offers_every_metric_and_shape(self):
        crit = QUESTIONS["data_metric"]["criteria"]
        from lib.features.data_queries import METRICS
        self.assertEqual(set(crit), set(METRICS) | {"none"})
        self.assertEqual(set(QUESTIONS["data_shape"]["criteria"]), {"leaderboard", "person", "compare", "total", "list", "closest", "between", "none"})
        self.assertIn("blackjack", QUESTIONS["data_game"]["criteria"])
        self.assertIn("connect4", QUESTIONS["data_game"]["criteria"])

    def test_absent_data_answers_mean_not_a_records_question(self):
        sig = _signals()
        self.assertEqual(sig.data_metric, "none")
        self.assertEqual(sig.data_shape, "none")
        self.assertIsNone(sig.data_limit)
        self.assertIsNone(sig.data_game)
        self.assertFalse(sig.data_query_requested())

    def test_parses_a_records_question(self):
        sig = parse_signals(_with_data(_answers(), metric="casino_net", shape="leaderboard", limit="5", window="week",
                                       game="blackjack", lowest=0.9), {})
        self.assertEqual(sig.data_metric, "casino_net")
        self.assertEqual(sig.data_shape, "leaderboard")
        self.assertEqual(sig.data_limit, 5)
        self.assertEqual(sig.data_window, "week")
        self.assertEqual(sig.data_game, "blackjack")
        self.assertTrue(sig.says("data_lowest"))
        self.assertTrue(sig.data_query_requested())
        self.assertIn("data=casino_net", sig.summary())

    def test_records_question_needs_both_picks_to_be_confident(self):
        self.assertFalse(parse_signals(_with_data(_answers(), metric_conf=0.4), {}).data_query_requested())
        self.assertFalse(parse_signals(_with_data(_answers(), shape_conf=0.4), {}).data_query_requested())
        self.assertFalse(parse_signals(_with_data(_answers(), shape="none"), {}).data_query_requested())
        self.assertTrue(parse_signals(_with_data(_answers(), metric_conf=0.5, shape_conf=0.5), {}).data_query_requested())

    def test_a_sure_shape_lowers_the_metric_bar_a_little(self):
        # "how much has snake paid kim": between at 1.00, paid_out at 0.48 (split with paid_in)
        sig = parse_signals(_with_data(_answers(), metric="paid_out", metric_conf=0.48, shape="between", shape_conf=1.0), {})
        self.assertTrue(sig.metric_sure)
        self.assertTrue(sig.data_query_requested())
        self.assertFalse(parse_signals(_with_data(_answers(), metric="paid_out", metric_conf=0.30, shape="between", shape_conf=1.0), {}).data_query_requested())
        self.assertFalse(parse_signals(_with_data(_answers(), metric="paid_out", metric_conf=0.48, shape="leaderboard", shape_conf=0.7), {}).data_query_requested())
        self.assertFalse(parse_signals(_with_data(_answers(), metric="none", metric_conf=0.99, shape="between", shape_conf=1.0), {}).data_query_requested())

    def test_a_picture_request_is_never_a_records_question(self):
        # "draw the top 5 richest as pigs": the roster gets drawn, not tabulated
        sig = parse_signals(_with_data(_answers(action="generate", probs={"generate": 0.9, "edit": 0.05, "reply": 0.05}), metric="ukpence"), {})
        self.assertFalse(sig.data_query_requested())

    def test_unknown_picks_fall_back_to_defaults(self):
        sig = parse_signals(_with_data(_answers(), metric="bitcoin", shape="graph", limit="99", window="decade", game="chess", subject="dog"), {})
        self.assertEqual(sig.data_metric, "none")
        self.assertEqual(sig.data_shape, "none")
        self.assertIsNone(sig.data_limit)
        self.assertEqual(sig.data_window, "all_time")
        self.assertIsNone(sig.data_game)
        self.assertEqual(sig.data_subject, "not_applicable")

    def test_flat_limit_is_ignored(self):
        self.assertIsNone(parse_signals(_with_data(_answers(), limit="5", limit_conf=0.3), {}).data_limit)

    def test_single_member_question_gets_a_short_board(self):
        # "who has used the most shutcoins" is one person plus context, not the top 10
        self.assertEqual(parse_signals(_with_data(_answers(), limit="single"), {}).data_limit, ms.SINGLE_ANSWER_LIMIT)
        self.assertIsNone(parse_signals(_with_data(_answers(), limit="single", limit_conf=0.3), {}).data_limit)
        self.assertIn("single", QUESTIONS["data_limit"]["criteria"])

    def test_list_questions_and_sources(self):
        from lib.features.data_queries import LISTS, SOURCES
        self.assertEqual(set(QUESTIONS["data_list"]["criteria"]), set(LISTS) | {"none"})
        self.assertEqual(set(QUESTIONS["data_source"]["criteria"]), set(SOURCES) | {"all"})
        self.assertIn("list", QUESTIONS["data_shape"]["criteria"])
        answers = _with_data(_answers(), metric="none", shape="list")
        answers["data_list"] = {"type": "choice", "choice": "badges", "confidence": 0.9}
        answers["data_source"] = {"type": "choice", "choice": "chatting", "confidence": 0.9}
        sig = parse_signals(answers, {})
        self.assertEqual(sig.data_list, "badges")
        self.assertEqual(sig.data_source, "chatting")
        self.assertTrue(sig.data_query_requested())      # a list needs no metric
        answers["data_list"]["confidence"] = 0.3
        self.assertFalse(parse_signals(answers, {}).data_query_requested())
        answers["data_list"] = {"choice": "none", "confidence": 0.9}
        self.assertFalse(parse_signals(answers, {}).data_query_requested())
        self.assertIsNone(_signals().data_source)

    def test_a_confident_list_pick_beats_a_weak_shape(self):
        # "what's the lottery pot": shape total, no metric, list lottery -> a list
        answers = _with_data(_answers(), metric="none", shape="total", shape_conf=0.62)
        answers["data_list"] = {"choice": "lottery", "confidence": 0.83}
        sig = parse_signals(answers, {})
        self.assertEqual(sig.effective_shape, "list")
        self.assertTrue(sig.data_query_requested())
        # a confident metric with a non-list shape keeps its shape even if a list is also plausible
        answers = _with_data(_answers(), metric="ukp_earned", metric_conf=0.99, shape="person", shape_conf=0.99)
        answers["data_list"] = {"choice": "casino_breakdown", "confidence": 0.7}
        self.assertEqual(parse_signals(answers, {}).effective_shape, "person")
        # a weak shape with a confident metric and no list is still not answered
        answers = _with_data(_answers(), metric="first_seen", metric_conf=0.98, shape="person", shape_conf=0.38)
        self.assertEqual(parse_signals(answers, {}).effective_shape, "none")
        self.assertFalse(parse_signals(answers, {}).data_query_requested())


class TestJudgeNamedSubject(unittest.IsolatedAsyncioTestCase):
    async def test_no_candidates_or_key_means_nobody(self):
        session = _FakeSession([])
        self.assertEqual(await ms.judge_named_subject("steven's xp", [], api_key="k", session=session), (None, 0, 0))
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TYPESAFE_API_KEY", None)
            self.assertEqual(await ms.judge_named_subject("steven's xp", [("Steven", 2)], session=session), (None, 0, 0))
        self.assertEqual(session.calls, [])

    async def test_picks_a_member_and_returns_usage(self):
        body = {"answers": {"which": {"type": "choice", "choice": "Steven <3", "confidence": 0.93, "probabilities": {}}},
                "usage": {"input_tokens": 120, "output_tokens": 0}}
        session = _FakeSession([_FakeResponse(200, body)])
        res = await ms.judge_named_subject("how much xp has steven got", [("Oggers", 1), ("Steven <3", 2), ("Kim", 3)],
                                           api_key="k", session=session)
        self.assertEqual(res, (2, 120, 0))
        q = session.calls[0]["json"]["questions"]["which"]
        self.assertEqual(set(q["criteria"]), {"none", "Oggers", "Steven <3", "Kim"})
        self.assertEqual(session.calls[0]["json"]["state"]["members"], ["Oggers", "Steven <3", "Kim"])

    async def test_duplicate_names_are_disambiguated_by_id(self):
        body = {"answers": {"which": {"choice": "Steven (5)", "confidence": 0.9}}}
        session = _FakeSession([_FakeResponse(200, body)])
        res = await ms.judge_named_subject("steven", [("Steven", 2), ("Steven", 5)], api_key="k", session=session)
        self.assertEqual(res[0], 5)

    async def test_judge_pick_returns_the_name(self):
        body = {"answers": {"which": {"choice": "Yorkshire", "confidence": 0.9}}, "usage": {"input_tokens": 90}}
        session = _FakeSession([_FakeResponse(200, body)])
        res = await ms.judge_pick("who owns yorks", ["London", "Yorkshire", "Devon"], what="counties", api_key="k", session=session)
        self.assertEqual(res, ("Yorkshire", 90, 0))
        self.assertEqual(session.calls[0]["json"]["state"]["counties"], ["London", "Yorkshire", "Devon"])
        self.assertIn("none", session.calls[0]["json"]["questions"]["which"]["criteria"])

    async def test_low_confidence_or_none_is_nobody(self):
        for body in ({"answers": {"which": {"choice": "Steven", "confidence": 0.4}}},
                     {"answers": {"which": {"choice": "none", "confidence": 0.95}}}):
            session = _FakeSession([_FakeResponse(200, body)])
            res = await ms.judge_named_subject("who", [("Steven", 2)], api_key="k", session=session)
            self.assertIsNone(res[0])


class TestJudgeMention(unittest.IsolatedAsyncioTestCase):
    async def test_no_key_means_no_verdict_and_no_request(self):
        session = _FakeSession([])
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TYPESAFE_API_KEY", None)
            res = await judge_mention("draw me", caller_name="Oggers", session=session)
        self.assertIsNone(res)
        self.assertEqual(session.calls, [])

    async def test_empty_prompt_means_no_verdict(self):
        session = _FakeSession([])
        self.assertIsNone(await judge_mention("   ", caller_name="Oggers", api_key="k", session=session))
        self.assertEqual(session.calls, [])

    async def test_posts_the_batch_and_parses_the_answer(self):
        body = {"model": "jev-latest", "answers": _answers(action="generate", confidence=0.7, bot_self=0.9), "usage": {"input_tokens": 410, "output_tokens": 0}}
        session = _FakeSession([_FakeResponse(200, body)])
        res = await judge_mention(
            "draw yourself", caller_name="Oggers", api_key="test-key", session=session,
            has_recent_bot_image=True, recent_bot_image_prompt="a goblin",
        )
        self.assertIsNotNone(res)
        self.assertEqual(res.action, "generate")
        self.assertTrue(res.says("bot_self"))
        self.assertEqual(res.input_tokens, 410)

        call = session.calls[0]
        self.assertEqual(call["url"], ms.TYPESAFE_URL)
        self.assertEqual(call["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(call["json"]["model"], ms.MENTION_SIGNALS_MODEL)
        self.assertIs(call["json"]["questions"], QUESTIONS)
        self.assertEqual(call["json"]["state"]["message"]["text"], "draw yourself")
        self.assertTrue(call["json"]["state"]["bot_recent_image"]["posted"])

    async def test_retries_once_on_5xx_then_gives_up(self):
        session = _FakeSession([_FakeResponse(529, text="overloaded"), _FakeResponse(503, text="down")])
        with patch("lib.features.mention_signals.asyncio.sleep", return_value=None):
            res = await judge_mention("draw me", caller_name="Oggers", api_key="k", session=session)
        self.assertIsNone(res)
        self.assertEqual(len(session.calls), 2)

    async def test_recovers_on_the_retry(self):
        body = {"answers": _answers(), "usage": {}}
        session = _FakeSession([_FakeResponse(429, text="slow down"), _FakeResponse(200, body)])
        with patch("lib.features.mention_signals.asyncio.sleep", return_value=None):
            res = await judge_mention("hello", caller_name="Oggers", api_key="k", session=session)
        self.assertIsNotNone(res)
        self.assertEqual(res.action, "reply")
        self.assertEqual(len(session.calls), 2)

    async def test_no_retry_on_4xx(self):
        session = _FakeSession([_FakeResponse(422, text="bad question"), _FakeResponse(200, {"answers": _answers()})])
        res = await judge_mention("hello", caller_name="Oggers", api_key="k", session=session)
        self.assertIsNone(res)
        self.assertEqual(len(session.calls), 1)

    async def test_timeout_is_no_verdict(self):
        session = _FakeSession([asyncio.TimeoutError(), aiohttp.ClientConnectionError("nope")])
        with patch("lib.features.mention_signals.asyncio.sleep", return_value=None):
            res = await judge_mention("hello", caller_name="Oggers", api_key="k", session=session)
        self.assertIsNone(res)
        self.assertEqual(len(session.calls), 2)

    async def test_malformed_body_is_no_verdict(self):
        session = _FakeSession([_FakeResponse(200, {"answers": {"action": {"choice": "reply"}}})])
        self.assertIsNone(await judge_mention("hello", caller_name="Oggers", api_key="k", session=session))


if __name__ == "__main__":
    unittest.main()
