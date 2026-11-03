import unittest
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import discord

from config import ROLES
from lib.economy.bank_commands_ui import (
    ROLE_HANDOUT_CAP,
    UKPAddAmountModal,
    UKPAddPickerView,
    _PeopleSelect,
    _RoleAmountButton,
    _RoleSelect,
    _role_recipients,
)


def _member(uid, bot=False, deputy=False):
    m = MagicMock(spec=discord.Member)
    m.id, m.bot, m.mention = uid, bot, f"<@{uid}>"
    role = MagicMock(spec=discord.Role)
    role.id = ROLES.DEPUTY_PM if deputy else 1
    m.roles = [role]
    return m


def _role(members, rid=555):
    r = MagicMock(spec=discord.Role)
    r.id, r.name, r.mention, r.members = rid, "Halloween '26 Entrant", f"<@&{rid}>", members
    return r


def _interaction(user):
    i = MagicMock(spec=discord.Interaction)
    i.user = user
    i.response = MagicMock()
    i.response.send_message = AsyncMock()
    i.response.edit_message = AsyncMock()
    i.response.send_modal = AsyncMock()
    i.message = MagicMock()
    i.message.delete = AsyncMock()
    return i


def _items(view, cls):
    return [c for c in view.walk_children() if isinstance(c, cls)]


class TestUKPAddRoles(unittest.IsolatedAsyncioTestCase):
    def test_role_recipients_skip_bots(self):
        humans = [_member(1), _member(2)]
        self.assertEqual(_role_recipients(_role(humans + [_member(3, bot=True)])), humans)

    async def test_picker_offers_people_and_role_dropdowns(self):
        view = UKPAddPickerView(author_id=42)
        self.assertEqual(len(_items(view, _PeopleSelect)), 1)
        self.assertEqual(len(_items(view, _RoleSelect)), 1)
        self.assertEqual(_items(view, _RoleAmountButton), [])

    async def test_picking_a_role_shows_the_count_and_an_amount_button(self):
        role = _role([_member(1), _member(2), _member(3, bot=True)])
        select = _items(UKPAddPickerView(author_id=42), _RoleSelect)[0]
        interaction = _interaction(_member(42, deputy=True))
        with patch.object(_RoleSelect, "values", new_callable=PropertyMock, return_value=[role]):
            await select.callback(interaction)
        new_view = interaction.response.edit_message.call_args.kwargs["view"]
        self.assertEqual(len(_items(new_view, _RoleAmountButton)), 1)
        text = " ".join(t.content for t in _items(new_view, discord.ui.TextDisplay))
        self.assertIn("**2** people have this role", text)

    async def test_role_over_the_cap_is_refused(self):
        role = _role([_member(i) for i in range(ROLE_HANDOUT_CAP + 1)])
        select = _items(UKPAddPickerView(author_id=42), _RoleSelect)[0]
        interaction = _interaction(_member(42, deputy=True))
        with patch.object(_RoleSelect, "values", new_callable=PropertyMock, return_value=[role]):
            await select.callback(interaction)
        interaction.response.edit_message.assert_not_called()
        self.assertTrue(interaction.response.send_message.call_args.kwargs["ephemeral"])

    async def test_role_with_only_bots_is_refused(self):
        role = _role([_member(1, bot=True)])
        select = _items(UKPAddPickerView(author_id=42), _RoleSelect)[0]
        interaction = _interaction(_member(42, deputy=True))
        with patch.object(_RoleSelect, "values", new_callable=PropertyMock, return_value=[role]):
            await select.callback(interaction)
        interaction.response.edit_message.assert_not_called()
        self.assertIn("Nobody", interaction.response.send_message.call_args.args[0])

    async def test_amount_button_opens_the_modal_for_the_role_members(self):
        humans = [_member(1), _member(2)]
        role = _role(humans + [_member(3, bot=True)])
        button = _items(UKPAddPickerView(author_id=42, role=role), _RoleAmountButton)[0]
        interaction = _interaction(_member(42, deputy=True))
        await button.callback(interaction)
        modal = interaction.response.send_modal.call_args.args[0]
        self.assertIsInstance(modal, UKPAddAmountModal)
        self.assertEqual(modal.selected_members, humans)
        self.assertIs(modal.role, role)
        interaction.message.delete.assert_awaited()

    async def test_only_the_deputy_pm_who_asked_can_use_the_card(self):
        view = UKPAddPickerView(author_id=42)
        stranger = _interaction(_member(7, deputy=True))
        self.assertFalse(await view.interaction_check(stranger))
        not_deputy = _interaction(_member(42))
        self.assertFalse(await view.interaction_check(not_deputy))
        self.assertTrue(await view.interaction_check(_interaction(_member(42, deputy=True))))

    async def test_role_handout_pays_every_member_from_the_bank(self):
        humans = [_member(1), _member(2)]
        role = _role(humans)
        modal = UKPAddAmountModal(humans, role=role)
        modal.amount = MagicMock(value="1,000")
        interaction = _interaction(_member(42, deputy=True))
        interaction.user.name = "hadidas"
        with patch("lib.economy.bank_commands_ui.BankManager.get_balance", return_value=50_000), \
                patch("lib.economy.bank_commands_ui.BankManager.transfer_to_users", return_value=True) as transfer:
            await modal.on_submit(interaction)
        args, kwargs = transfer.call_args
        self.assertEqual(args[:2], ([1, 2], 1000))
        self.assertIn("Halloween '26 Entrant", kwargs["description"])
        embed = interaction.response.send_message.call_args.kwargs["embed"]
        self.assertIn("members of <@&555>", embed.description)


if __name__ == "__main__":
    unittest.main()
