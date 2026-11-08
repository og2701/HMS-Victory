import asyncio
from unittest.mock import MagicMock, patch

from commands.moderation import join_clusters as jc


def test_fast_voice_join_reports_are_paused():
    assert jc.VOICE_RUSH_REPORTS_ENABLED is False
    member = MagicMock(bot=False, id=1)
    with patch.object(jc, "_fast_join_seconds", return_value=30) as fast:
        assert asyncio.run(jc.report_fast_voice_join(MagicMock(), member, MagicMock())) is False
        fast.assert_not_called()
