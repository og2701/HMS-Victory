import discord
from database import DatabaseManager

async def handle_casino_stats_command(interaction: discord.Interaction, member: discord.Member = None):
    target_member = member or interaction.user
    user_id = str(target_member.id)
    
    # Query overall totals
    totals_query = """
        SELECT 
            COUNT(*),
            SUM(staked),
            SUM(payout),
            SUM(net),
            SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END),
            SUM(CASE WHEN result = 'loss' THEN 1 ELSE 0 END),
            SUM(CASE WHEN result = 'push' THEN 1 ELSE 0 END),
            MAX(net),
            MIN(net)
        FROM casino_results 
        WHERE user_id = ?
    """
    totals = DatabaseManager.fetch_one(totals_query, (user_id,))
    
    if not totals or totals[0] == 0:
        await interaction.response.send_message(
            f"❌ {target_member.mention} hasn't played any casino games yet!", 
            ephemeral=True
        )
        return
        
    # Query per-game breakdown
    games_query = """
        SELECT 
            game,
            COUNT(*),
            SUM(staked),
            SUM(payout),
            SUM(net),
            SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END),
            SUM(CASE WHEN result = 'loss' THEN 1 ELSE 0 END),
            SUM(CASE WHEN result = 'push' THEN 1 ELSE 0 END),
            MAX(net),
            MIN(net)
        FROM casino_results 
        WHERE user_id = ?
        GROUP BY game
    """
    games = DatabaseManager.fetch_all(games_query, (user_id,))
    
    total_played, total_staked, total_payout, total_net, wins, losses, pushes, max_win, max_loss = totals
    
    embed = discord.Embed(
        title=f"🎰 Casino Stats - {target_member.display_name}",
        color=0xD4AF37
    )
    embed.set_thumbnail(url=target_member.display_avatar.url)
    
    sign = "+" if total_net >= 0 else ""
    net_str = f"{sign}{total_net:,} UKPence"
    
    embed.description = (
        f"**Games Played**: {total_played:,}\n"
        f"**Overall Record**: {wins}W / {losses}L / {pushes}P\n"
        f"**Total Staked**: {total_staked:,} UKPence\n"
        f"**Total Payout**: {total_payout:,} UKPence\n"
        f"**Net Profit/Loss**: **{net_str}**\n"
        f"**Biggest Win**: +{max_win:,} UKPence\n"
        f"**Biggest Loss**: {max_loss:,} UKPence"
    )
    
    game_names = {
        "blackjack": "🎴 Blackjack",
        "higherlower": "🔼 Higher/Lower",
        "reddog": "🐕 Red Dog",
        "slots": "🎰 Slots",
        "videopoker": "🃏 Video Poker",
        "tcp": "♣️ 3-Card Poker",
        "roulette": "🎡 Roulette",
        "mines": "💣 Mines",
        "penalty": "⚽ Penalty Shootout",
        "chest": "🧰 Chest Upgrade",
        "blockade": "🚢 Blockade Run"
    }
    
    for row in games:
        game_key, g_played, g_staked, g_payout, g_net, g_wins, g_losses, g_pushes, g_max_win, g_max_loss = row
        game_name = game_names.get(game_key, game_key.capitalize())
        g_sign = "+" if g_net >= 0 else ""
        
        g_max_win = g_max_win or 0
        g_max_loss = g_max_loss or 0
        
        val = (
            f"• Record: {g_wins}W / {g_losses}L / {g_pushes}P\n"
            f"• Staked: {g_staked:,} UKP · Payout: {g_payout:,} UKP\n"
            f"• Net P/L: **{g_sign}{g_net:,} UKPence**\n"
            f"• Highs: +{g_max_win:,} / {g_max_loss:,} UKP"
        )
        embed.add_field(name=game_name, value=val, inline=True)
        
    embed.set_footer(text="HMS Victory Casino Stats • Gamble Responsibly")
    await interaction.response.send_message(embed=embed)
