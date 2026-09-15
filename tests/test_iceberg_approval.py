import unittest
import asyncio
import discord
from unittest.mock import AsyncMock, MagicMock, patch
from config import ROLES
from database import DatabaseManager
from lib.economy.shop_items import (
    IcebergApprovalView,
    _is_iceberg_staff,
    ICEBERG_STAFF_ROLES,
)


def _mock_member_with_roles(*role_ids):
    member = MagicMock(spec=discord.Member)
    roles = []
    for r_id in role_ids:
        r = MagicMock(spec=discord.Role)
        r.id = r_id
        roles.append(r)
    member.roles = roles
    member.display_name = "StaffMember"
    member.mention = "<@12345>"
    return member


class TestIcebergApproval(unittest.IsolatedAsyncioTestCase):

    def test_is_iceberg_staff_permissions(self):
        """Verify that PCSO, Cabinet, Deputy PM, and Minister are recognized as staff, and normal members are not."""
        pcso = _mock_member_with_roles(ROLES.PCSO)
        self.assertTrue(_is_iceberg_staff(pcso))

        cabinet = _mock_member_with_roles(ROLES.CABINET)
        self.assertTrue(_is_iceberg_staff(cabinet))

        deputy_pm = _mock_member_with_roles(ROLES.DEPUTY_PM)
        self.assertTrue(_is_iceberg_staff(deputy_pm))

        minister = _mock_member_with_roles(ROLES.MINISTER)
        self.assertTrue(_is_iceberg_staff(minister))

        regular = _mock_member_with_roles(ROLES.MEMBER)
        self.assertFalse(_is_iceberg_staff(regular))

    def test_iceberg_approval_view_custom_ids(self):
        """Verify view buttons have persistent custom IDs formatted correctly."""
        view = IcebergApprovalView(submission_id=42)
        self.assertEqual(view.approve_button.custom_id, "iceberg_approve:42")
        self.assertEqual(view.deny_button.custom_id, "iceberg_deny:42")
        self.assertEqual(view.edit_text_button.custom_id, "iceberg_edit_text:42")
        self.assertEqual(view.amend_level_button.custom_id, "iceberg_amend_level:42")

    async def test_non_staff_cannot_interact(self):
        """Verify non-staff members get rejected on all buttons."""
        view = IcebergApprovalView(submission_id=1)
        regular = _mock_member_with_roles(ROLES.MEMBER)

        interaction = MagicMock(spec=discord.Interaction)
        interaction.user = regular
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        # Test approve
        await view.approve_button.callback(interaction)
        interaction.response.send_message.assert_called_with(
            "❌ Only staff and PCSOs can approve iceberg entries.", ephemeral=True
        )

        # Test deny
        interaction.response.send_message.reset_mock()
        await view.deny_button.callback(interaction)
        interaction.response.send_message.assert_called_with(
            "❌ Only staff and PCSOs can deny iceberg entries.", ephemeral=True
        )

        # Test edit text
        interaction.response.send_message.reset_mock()
        await view.edit_text_button.callback(interaction)
        interaction.response.send_message.assert_called_with(
            "❌ Only staff and PCSOs can edit iceberg entries.", ephemeral=True
        )

        # Test amend level
        interaction.response.send_message.reset_mock()
        await view.amend_level_button.callback(interaction)
        interaction.response.send_message.assert_called_with(
            "❌ Only staff and PCSOs can amend iceberg levels.", ephemeral=True
        )

    async def test_edit_text_modal_updates_db_and_embed(self):
        """Verify editing text updates the database row and the message embed."""
        view = IcebergApprovalView(submission_id=10)
        staff = _mock_member_with_roles(ROLES.PCSO)

        db_state = {"text": "Original lore", "level": 3, "status": "pending"}

        def mock_fetch_one(query, params=()):
            if "pending_iceberg_submissions" in query:
                if "SELECT status FROM" in query:
                    return (db_state["status"],)
                return ("12345", db_state["text"], db_state["level"], db_state["status"])
            return None

        def mock_execute(query, params=()):
            if "UPDATE pending_iceberg_submissions SET text" in query:
                db_state["text"] = params[0]
                return 1
            return 0

        with patch.object(DatabaseManager, "fetch_one", side_effect=mock_fetch_one), \
             patch.object(DatabaseManager, "execute", side_effect=mock_execute):

            # Trigger edit button
            interaction = MagicMock(spec=discord.Interaction)
            interaction.user = staff
            interaction.response = MagicMock()
            interaction.response.send_modal = AsyncMock()

            await view.edit_text_button.callback(interaction)
            interaction.response.send_modal.assert_called_once()
            modal = interaction.response.send_modal.call_args[0][0]

            self.assertEqual(modal.text_input.default, "Original lore")

            # Simulate modal submit
            modal_interaction = MagicMock(spec=discord.Interaction)
            modal_interaction.user = staff
            modal_interaction.response = MagicMock()
            modal_interaction.response.defer = AsyncMock()
            modal_interaction.edit_original_response = AsyncMock()
            modal_interaction.followup = MagicMock()
            modal_interaction.followup.send = AsyncMock()

            embed = discord.Embed(title="🧊 Iceberg Submission Request")
            embed.add_field(name="Text", value="Original lore", inline=False)
            embed.add_field(name="Level", value=3, inline=True)
            modal_interaction.message = MagicMock()
            modal_interaction.message.embeds = [embed]

            modal.text_input._value = "Updated cool lore"
            await modal.on_submit(modal_interaction)

            self.assertEqual(db_state["text"], "Updated cool lore")

            modal_interaction.edit_original_response.assert_called_once()
            edited_embed = modal_interaction.edit_original_response.call_args[1]["embed"]
            self.assertEqual(edited_embed.fields[0].value, "Updated cool lore")
            self.assertIn("Last edited by", edited_embed.footer.text)
            self.assertIn("StaffMember", edited_embed.footer.text)

            modal_interaction.followup.send.assert_called_once()
            self.assertIn("Updated cool lore", modal_interaction.followup.send.call_args[0][0])

    async def test_amend_level_modal_updates_db_and_embed(self):
        """Verify amending level validates 1-6, updates the database, and updates the embed."""
        view = IcebergApprovalView(submission_id=10)
        pcso = _mock_member_with_roles(ROLES.PCSO)

        db_state = {"text": "Original lore", "level": 2, "status": "pending"}

        def mock_fetch_one(query, params=()):
            if "pending_iceberg_submissions" in query:
                if "SELECT status FROM" in query:
                    return (db_state["status"],)
                return ("12345", db_state["text"], db_state["level"], db_state["status"])
            return None

        def mock_execute(query, params=()):
            if "UPDATE pending_iceberg_submissions SET level" in query:
                db_state["level"] = params[0]
                return 1
            return 0

        with patch.object(DatabaseManager, "fetch_one", side_effect=mock_fetch_one), \
             patch.object(DatabaseManager, "execute", side_effect=mock_execute):

            interaction = MagicMock(spec=discord.Interaction)
            interaction.user = pcso
            interaction.response = MagicMock()
            interaction.response.send_modal = AsyncMock()

            await view.amend_level_button.callback(interaction)
            interaction.response.send_modal.assert_called_once()
            modal = interaction.response.send_modal.call_args[0][0]

            self.assertEqual(modal.level_input.default, "2")

            # Simulate invalid level submit
            modal_interaction = MagicMock(spec=discord.Interaction)
            modal_interaction.user = pcso
            modal_interaction.response = MagicMock()
            modal_interaction.response.send_message = AsyncMock()

            modal.level_input._value = "9"
            await modal.on_submit(modal_interaction)
            modal_interaction.response.send_message.assert_called_with(
                "❌ Level must be an integer between 1 and 6.", ephemeral=True
            )
            self.assertEqual(db_state["level"], 2)

            # Simulate valid level submit
            modal_interaction_valid = MagicMock(spec=discord.Interaction)
            modal_interaction_valid.user = pcso
            modal_interaction_valid.response = MagicMock()
            modal_interaction_valid.response.defer = AsyncMock()
            modal_interaction_valid.edit_original_response = AsyncMock()
            modal_interaction_valid.followup = MagicMock()
            modal_interaction_valid.followup.send = AsyncMock()

            embed = discord.Embed(title="🧊 Iceberg Submission Request")
            embed.add_field(name="Text", value="Original lore", inline=False)
            embed.add_field(name="Level", value=2, inline=True)
            modal_interaction_valid.message = MagicMock()
            modal_interaction_valid.message.embeds = [embed]

            modal.level_input._value = "5"
            await modal.on_submit(modal_interaction_valid)

            self.assertEqual(db_state["level"], 5)
            modal_interaction_valid.edit_original_response.assert_called_once()
            edited_embed = modal_interaction_valid.edit_original_response.call_args[1]["embed"]
            self.assertEqual(edited_embed.fields[1].value, "5")
            self.assertIn("Last edited by", edited_embed.footer.text)
            self.assertIn("StaffMember", edited_embed.footer.text)

    async def test_pcso_can_approve_amended_submission(self):
        """Verify PCSO can approve, and approval uses the amended DB text/level."""
        view = IcebergApprovalView(submission_id=10)
        pcso = _mock_member_with_roles(ROLES.PCSO)

        db_state = {"text": "Amended lore", "level": 4, "status": "pending"}

        def mock_fetch_one(query, params=()):
            if "pending_iceberg_submissions" in query:
                return ("12345", db_state["text"], db_state["level"], db_state["status"])
            return None

        def mock_execute(query, params=()):
            if "UPDATE pending_iceberg_submissions SET status = 'approved'" in query:
                db_state["status"] = "approved"
                return 1
            return 0

        interaction = MagicMock(spec=discord.Interaction)
        interaction.user = pcso
        interaction.response = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup = MagicMock()
        interaction.followup.send = AsyncMock()
        interaction.client = MagicMock()
        interaction.client.fetch_user = AsyncMock(return_value=None)

        embed = discord.Embed(title="🧊 Iceberg Submission Request")
        interaction.message = MagicMock()
        interaction.message.embeds = [embed]
        interaction.message.edit = AsyncMock()

        mock_add_iceberg = AsyncMock(return_value=True)

        with patch.object(DatabaseManager, "fetch_one", side_effect=mock_fetch_one), \
             patch.object(DatabaseManager, "execute", side_effect=mock_execute), \
             patch("commands.creative.iceberg.add_to_iceberg.add_iceberg_text", mock_add_iceberg), \
             patch("lib.economy.shop_items._store_shop_notice"):

            await view.approve_button.callback(interaction)

            # Assert add_iceberg_text called with the amended text and level
            mock_add_iceberg.assert_called_once_with(interaction, "Amended lore", 4, show_image=False)
            self.assertEqual(db_state["status"], "approved")
            interaction.message.edit.assert_called_once()
