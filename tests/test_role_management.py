"""Bulk role grants must not quietly undo a quarantine."""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from commands.moderation import role_management as RM
from commands.moderation.anti_raid import QUARANTINE_ROLE_ID


def member(mid, roles=()):
    return types.SimpleNamespace(id=mid, roles=list(roles))


def test_a_quarantined_member_is_recognised():
    quarantine = types.SimpleNamespace(id=QUARANTINE_ROLE_ID)
    other = types.SimpleNamespace(id=1)
    assert RM._is_quarantined(member(1, [quarantine])) is True
    assert RM._is_quarantined(member(2, [other])) is False
    assert RM._is_quarantined(member(3, [])) is False


def test_quarantine_is_matched_by_id_not_by_position():
    """Roles are compared on id, so renaming or moving the role cannot bypass this."""
    lookalike = types.SimpleNamespace(id=QUARANTINE_ROLE_ID + 1, name="Quarantine")
    assert RM._is_quarantined(member(1, [lookalike])) is False


def test_a_member_with_several_roles_is_still_caught():
    quarantine = types.SimpleNamespace(id=QUARANTINE_ROLE_ID)
    assert RM._is_quarantined(
        member(1, [types.SimpleNamespace(id=9), quarantine, types.SimpleNamespace(id=8)])
    ) is True
