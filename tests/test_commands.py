import unittest
from unittest.mock import Mock

from lib.commands import updateRoleAssignments

class TestUpdateRoleAssignments(unittest.TestCase):

    async def test_calls_update_roles_on_guild(self):
        interaction = Mock()
        guild = Mock()
        interaction.guild = guild

        await updateRoleAssignments(interaction, "Member", ["Role1", "Role2"])

        guild.update_roles.assert_called_once_with(interaction.user, ["Role1", "Role2"])

    async def test_sends_confirmation_on_success(self):
        interaction = Mock()
        interaction.followup = Mock()
        guild = Mock()
        interaction.guild = guild
        guild.update_roles.return_value = True

        await updateRoleAssignments(interaction, "Member", ["Role1"])

        interaction.followup.send.assert_called_once_with("Roles updated!", ephemeral=True)

    async def test_sends_failure_message_on_error(self):
        interaction = Mock()
        interaction.followup = Mock()
        guild = Mock()
        interaction.guild = guild
        guild.update_roles.return_value = False

        await updateRoleAssignments(interaction, "Member", ["Role1"])

        interaction.followup.send.assert_called_once_with("Failed to update roles", ephemeral=True)

if __name__ == '__main__':
    unittest.main()
