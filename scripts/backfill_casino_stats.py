import os
import re
import sqlite3
import discord
from dotenv import load_dotenv
import time

# Add base directory to path so imports work correctly
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import CHANNELS
from database import DB_FILE, DatabaseManager

# Regex patterns
_STAKE_PATTERN = re.compile(r"💸 <@(\d+)> paid `?([\d,]+)`? UKP")
_PAYOUT_PATTERN = re.compile(r"⚖️ <@(\d+)> balance set to `?([\d,]+)`? UKP \(was `?([\d,]+)`?\)")
_TIME_PATTERN = re.compile(r"<t:(\d+):T>")

def parse_int(val_str):
    return int(val_str.replace(",", ""))

def get_game_key(reason):
    reason_lower = reason.lower()
    if "blackjack" in reason_lower:
        return "blackjack"
    elif "higher-lower" in reason_lower or "higherlower" in reason_lower:
        return "higherlower"
    elif "slots" in reason_lower or "fruit machine" in reason_lower:
        return "slots"
    elif "red dog" in reason_lower:
        return "reddog"
    elif "3-card poker" in reason_lower or "tcp" in reason_lower:
        return "tcp"
    elif "video poker" in reason_lower:
        return "videopoker"
    return None

class BackfillCasinoClient(discord.Client):
    async def on_ready(self):
        print(f"Logged in as {self.user}. Starting casino results backfill scan...")
        
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
        events = []
        
        try:
            async for msg in channel.history(limit=None):
                count += 1
                if count % 1000 == 0:
                    print(f"Scanned {count} messages...")
                
                if not msg.embeds:
                    continue
                
                for embed in msg.embeds:
                    if embed.title != "💰 Economy Activity":
                        continue
                    
                    for field in embed.fields:
                        name = field.name or ""
                        val = field.value or ""
                        
                        # Extract timestamp
                        time_match = _TIME_PATTERN.search(name)
                        if not time_match:
                            continue
                        timestamp = int(time_match.group(1))
                        
                        # Extract reason (part after " - ")
                        if " - " in name:
                            reason = name.split(" - ", 1)[1]
                        else:
                            reason = name
                            
                        game_key = get_game_key(reason)
                        if not game_key:
                            continue
                            
                        # Parse stake or payout
                        stake_match = _STAKE_PATTERN.search(val)
                        if stake_match:
                            user_id = stake_match.group(1)
                            amount = parse_int(stake_match.group(2))
                            events.append({
                                "type": "stake",
                                "user_id": user_id,
                                "game": game_key,
                                "amount": amount,
                                "timestamp": timestamp,
                                "reason": reason
                            })
                            continue
                            
                        payout_match = _PAYOUT_PATTERN.search(val)
                        if payout_match:
                            user_id = payout_match.group(1)
                            new_bal = parse_int(payout_match.group(2))
                            old_bal = parse_int(payout_match.group(3))
                            payout_amt = new_bal - old_bal
                            if payout_amt > 0:
                                events.append({
                                    "type": "payout",
                                    "user_id": user_id,
                                    "game": game_key,
                                    "amount": payout_amt,
                                    "timestamp": timestamp,
                                    "reason": reason
                                })
                                
        except Exception as e:
            print(f"Error scanning Discord history: {e}")
            
        print(f"Scanned {count} messages. Found {len(events)} relevant casino events.")
        
        # Sort events chronologically
        events.sort(key=lambda x: x["timestamp"])
        
        # Group into rounds
        rounds = []
        active_rounds = {} # key: (user_id, game)
        
        for ev in events:
            user_id = ev["user_id"]
            game = ev["game"]
            ev_type = ev["type"]
            amount = ev["amount"]
            ts = ev["timestamp"]
            reason = ev["reason"]
            
            key = (user_id, game)
            
            if ev_type == "stake":
                # Check if there is an active round that is recent
                active = active_rounds.get(key)
                if active and (ts - active["last_ts"]) < 300:
                    # Add to staked amount of the active round
                    active["staked"] += amount
                    active["last_ts"] = ts
                else:
                    # Settle old active round if any
                    if active:
                        rounds.append(active)
                    # Start new round
                    active_rounds[key] = {
                        "user_id": user_id,
                        "game": game,
                        "bet": amount,
                        "staked": amount,
                        "payout": 0,
                        "timestamp": ts,
                        "last_ts": ts,
                        "outcome": ev.get("reason", "")
                    }
            elif ev_type == "payout":
                active = active_rounds.get(key)
                if active and (ts - active["last_ts"]) < 300:
                    active["payout"] = amount
                    active["last_ts"] = ts
                    active["outcome"] = reason
                    rounds.append(active)
                    active_rounds[key] = None
                else:
                    if active:
                        rounds.append(active)
                        active_rounds[key] = None
                    # Stray payout (e.g. payout with no bet log found)
                    rounds.append({
                        "user_id": user_id,
                        "game": game,
                        "bet": 0,
                        "staked": 0,
                        "payout": amount,
                        "timestamp": ts,
                        "last_ts": ts,
                        "outcome": reason
                    })
                    
        # Flush remaining active rounds as losses
        for active in active_rounds.values():
            if active:
                rounds.append(active)
                
        print(f"Reconstructed {len(rounds)} casino rounds.")
        
        # Insert into database
        if rounds:
            print("Populating database table casino_results...")
            try:
                with DatabaseManager.locked_connection() as conn:
                    c = conn.cursor()
                    c.execute("DELETE FROM casino_results")
                    
                    inserted = 0
                    for r in rounds:
                        net = r["payout"] - r["staked"]
                        result = "win" if net > 0 else ("loss" if net < 0 else "push")
                        c.execute('''
                            INSERT INTO casino_results 
                            (user_id, game, bet, staked, payout, net, outcome, result, timestamp)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (r["user_id"], r["game"], r["bet"], r["staked"], r["payout"], net, r["outcome"], result, r["timestamp"]))
                        inserted += 1
                    conn.commit()
                print(f"Successfully backfilled {inserted} rows into casino_results table!")
            except Exception as e:
                print(f"Database insertion failed: {e}")
        else:
            print("No rounds to insert.")
            
        await self.close()

if __name__ == "__main__":
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("Error: DISCORD_TOKEN is missing in the environment.")
        sys.exit(1)
        
    intents = discord.Intents.default()
    intents.message_content = True
    client = BackfillCasinoClient(intents=intents)
    client.run(token)
