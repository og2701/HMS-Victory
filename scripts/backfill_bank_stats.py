import os
import re
import sqlite3
import discord
from dotenv import load_dotenv

# Add base directory to path so imports work correctly
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import CHANNELS
from database import DB_FILE, DatabaseManager

_LEDGER_AMOUNT = re.compile(r"`([\d,]+(?:\.\d+)?)`")

def parse_amount(text):
    m = _LEDGER_AMOUNT.search(text)
    if m:
        return int(float(m.group(1).replace(",", "")))
    return 0

class BackfillClient(discord.Client):
    async def on_ready(self):
        print(f"Logged in as {self.user}. Starting backfill scan...")
        
        # 1. Fetch current database table entries (unswept logs)
        print("Reading unswept database logs...")
        tax = 0
        bj_in = 0
        bj_out = 0
        
        try:
            unswept = DatabaseManager.fetch_all("SELECT log_text FROM economy_transactions")
            print(f"Found {len(unswept)} unswept logs in database.")
            for row in unswept:
                txt = row[0] or ""
                amt = parse_amount(txt)
                if not amt:
                    continue
                is_deposit = txt.startswith("🏦 Bank deposit:")
                is_withdrawal = txt.startswith("📉 Bank withdrawal:")
                
                if "Wealth tax" in txt and is_deposit:
                    tax += amt
                elif "Blackjack" in txt:
                    if is_deposit:
                        bj_in += amt
                    elif is_withdrawal:
                        bj_out += amt
            print(f"Database stats parsed: Tax={tax}, BJ In={bj_in}, BJ Out={bj_out}")
        except Exception as e:
            print(f"Error querying database economy_transactions: {e}")

        # 2. Scan the Discord channel history
        channel_id = CHANNELS.BOT_USAGE_LOG
        print(f"Fetching history from channel {channel_id}...")
        
        channel = self.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.fetch_channel(channel_id)
            except Exception as e:
                print(f"Error fetching channel: {e}")
                await self.close()
                return
        
        count = 0
        embed_count = 0
        field_count = 0
        
        try:
            async for msg in channel.history(limit=None):
                count += 1
                if count % 500 == 0:
                    print(f"Scanned {count} messages...")
                
                # We only parse messages sent by the bot/webhook that contain embeds
                if not msg.embeds:
                    continue
                
                for embed in msg.embeds:
                    if embed.title != "💰 Economy Activity":
                        continue
                    
                    embed_count += 1
                    for field in embed.fields:
                        field_count += 1
                        name = field.name or ""
                        val = field.value or ""
                        
                        amt = parse_amount(val)
                        if not amt:
                            continue
                        
                        is_deposit = "🏦 Bank deposit:" in val or " deposit:" in val
                        is_withdrawal = "📉 Bank withdrawal:" in val or " withdrawal:" in val
                        
                        if "Wealth tax" in name and is_deposit:
                            tax += amt
                        elif "Blackjack" in name:
                            if is_deposit:
                                bj_in += amt
                            elif is_withdrawal:
                                bj_out += amt
        except Exception as e:
            print(f"Error scanning Discord history: {e}")

        print(f"Scan complete. Total scanned messages: {count}, embeds: {embed_count}, fields: {field_count}")
        print(f"Calculated totals: Tax Collected = {tax:,} UKP, Blackjack In = {bj_in:,} UKP, Blackjack Out = {bj_out:,} UKP, Net = {bj_in - bj_out:,} UKP")
        
        # 3. Update SQLite database bank table
        print(f"Updating sqlite bank table at {DB_FILE}...")
        try:
            # We want to do a direct locked update
            with DatabaseManager.locked_connection() as conn:
                c = conn.cursor()
                c.execute('''
                    UPDATE bank
                    SET total_tax_collected = ?, total_blackjack_in = ?, total_blackjack_out = ?
                    WHERE id = 1
                ''', (tax, bj_in, bj_out))
                conn.commit()
            print("Database stats updated successfully!")
            
            # Double check the updated values
            row = DatabaseManager.fetch_one("SELECT total_tax_collected, total_blackjack_in, total_blackjack_out FROM bank WHERE id = 1")
            print("Verified new bank table stats:", row)
        except Exception as e:
            print(f"Failed to update database bank table: {e}")
            
        await self.close()

if __name__ == "__main__":
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("Error: DISCORD_TOKEN is missing in the environment.")
        sys.exit(1)
        
    intents = discord.Intents.default()
    intents.message_content = True
    client = BackfillClient(intents=intents)
    client.run(token)
