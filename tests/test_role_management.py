import unittest
from unittest.mock import Mock

from commands.role_management.role_management import updateRoleAssignments

class TestUpdateRoleAssignments(unittest.TestCase):

    async def test_sends_error_if_no_manage_roles_permission(self):
        interaction = Mock()
        interaction.user.guild_permissions.manage_guild = False
        
        await updateRoleAssignments(interaction, "Member")
        
        interaction.response.send_message.assert_called_with("You do not have permission to use this command.")

    async def test_sends_error_if_role_not_found(self):
        interaction = Mock()
        interaction.guild.roles = []
        
        await updateRoleAssignments(interaction, "Member")
        
        interaction.response.send_message.assert_called_with("Role 'Member' not found.")

    async def test_sends_message_if_all_members_have_role(self):
        interaction = Mock()
        interaction.guild.members = [Mock()]
        
        await updateRoleAssignments(interaction, "Member")
        
        interaction.response.send_message.assert_called_with("All members already have this role.")

    async def test_sends_initial_embed_with_member_list(self):
        member1 = Mock()
        member2 = Mock()
        interaction = Mock()
        interaction.guild.members = [member1, member2]
        
        await updateRoleAssignments(interaction, "Member")
        
        embed = interaction.response.send_message.call_args[0][0]
        self.assertEqual(embed.title, "Members without role __Member__")
        self.assertEqual(embed.description, "member1 | member2")

    async def test_sends_initial_embed_with_member_count_if_too_long(self):
        interaction = Mock()
        interaction.guild.members = [Mock() for i in range(60)]
        
        await updateRoleAssignments(interaction, "Member")
        
        embed = interaction.response.send_message.call_args[0][0]
        self.assertIn("There are 60 members", embed.description)

    async def test_calls_add_roles_in_batches(self):
        member1 = Mock()
        member2 = Mock()
        interaction = Mock()
        interaction.guild.members = [member1, member2]
        
        await updateRoleAssignments(interaction, "Member")
        
        member1.add_roles.assert_called_with(interaction.guild.roles[0])
        member2.add_roles.assert_called_with(interaction.guild.roles[0])

    async def test_sends_final_embed_with_members_updated_count(self):
        member1 = Mock()
        member2 = Mock()
        interaction = Mock()
        interaction.guild.members = [member1, member2]
        
        await updateRoleAssignments(interaction, "Member")
        
        embed = interaction.followup.edit_message.call_args[1]['embed']
        self.assertEqual(embed.title, "Role Assignment Complete")
        self.assertIn("Given role __Member__ to 2 members", embed.description)
        
