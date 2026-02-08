import discord
from discord import app_commands
from discord.ext import commands
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask
from threading import Thread

# --- DATABASE SETUP ---
DATABASE_URL = os.getenv('DATABASE_URL')

def init_db():
    """Создает таблицу, если она не существует"""
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS server_stats (
            guild_id TEXT PRIMARY KEY,
            level INTEGER DEFAULT 0,
            current_exp INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()

def get_stats(guild_id):
    gid = str(guild_id)
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT level, current_exp FROM server_stats WHERE guild_id = %s", (gid,))
    row = cur.fetchone()
    
    if not row:
        cur.execute("INSERT INTO server_stats (guild_id, level, current_exp) VALUES (%s, 0, 0)", (gid,))
        conn.commit()
        row = {"level": 0, "current_exp": 0}
    
    cur.close()
    conn.close()
    # Приводим ключи к твоему старому формату для совместимости с create_embed
    return {"level": row["level"], "current": row["current_exp"]}

def update_stats(guild_id, exp, level):
    gid = str(guild_id)
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    cur = conn.cursor()
    cur.execute(
        "UPDATE server_stats SET current_exp = %s, level = %s WHERE guild_id = %s",
        (exp, level, gid)
    )
    conn.commit()
    cur.close()
    conn.close()

# --- WEB SERVER ---
app = Flask('')
@app.route('/')
def home(): return "Bot is online!"

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    Thread(target=run).start()

# --- PROGRESS LOGIC ---
MAX_LEVEL = 12
EXP_THRESHOLDS = {
    0: 0, 1: 100, 2: 500, 3: 2000, 4: 4500, 5: 8000, 
    6: 12500, 7: 18000, 8: 24500, 9: 32000, 
    10: 40500, 11: 50000, 12: 60500
}

active_boards = {} 

def sync_level(exp):
    lvl = 0
    for l, val in sorted(EXP_THRESHOLDS.items()):
        if exp >= val: lvl = l
        else: break
    return lvl

def create_embed(stats, guild_name):
    lvl, curr = stats["level"], stats["current"]
    embed = discord.Embed(title=f"📊 Server Progress: {guild_name}", color=0x3498DB)
    
    if lvl >= MAX_LEVEL:
        bar = "🟦" * 15
        embed.add_field(name=f"Level {lvl} (MAX)", value=f"{bar} **100%**", inline=False)
        embed.add_field(name="Total Experience", value=f"💎 `{curr:,}`", inline=True)
    else:
        end = EXP_THRESHOLDS[lvl+1]
        progress_ratio = min(curr / end, 1.0)
        bar_count = int(progress_ratio * 15)
        bar = "🟦" * bar_count + "⬜" * (15 - bar_count)
        
        embed.add_field(
            name=f"Current Level: {lvl}", 
            value=f"{bar} **{int(progress_ratio * 100)}%**", 
            inline=False
        )
        embed.add_field(name="Experience", value=f"✨ `{curr:,}` / `{end:,}`", inline=True)
        embed.set_footer(text=f"Points to Level {lvl+1}: {max(0, end - curr):,} EXP")
    return embed

# --- BOT SETUP ---
class LevelBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        init_db() # Инициализируем базу данных при запуске
        await self.tree.sync()
        print(f"Logged in as {self.user}")

bot = LevelBot()

async def refresh_board(guild):
    gid = str(guild.id)
    if gid in active_boards:
        stats = get_stats(guild.id)
        try:
            await active_boards[gid].edit(embed=create_embed(stats, guild.name))
        except:
            del active_boards[gid]

# --- COMMANDS ---
@bot.tree.command(name="board", description="Show the current server level status")
async def board(interaction: discord.Interaction):
    stats = get_stats(interaction.guild_id)
    await interaction.response.send_message(embed=create_embed(stats, interaction.guild.name))
    active_boards[str(interaction.guild_id)] = await interaction.original_response()

@bot.tree.command(name="expadd", description="Add experience points")
@app_commands.checks.has_permissions(administrator=True)
async def expadd(interaction: discord.Interaction, amount: int):
    stats = get_stats(interaction.guild_id)
    new_exp = max(0, stats["current"] + amount)
    new_lvl = sync_level(new_exp)
    update_stats(interaction.guild_id, new_exp, new_lvl)
    
    await interaction.response.send_message(f"✅ Added {amount:,} EXP. Level: {new_lvl}", ephemeral=True)
    await refresh_board(interaction.guild)

@bot.tree.command(name="expremove", description="Remove experience points")
@app_commands.checks.has_permissions(administrator=True)
async def expremove(interaction: discord.Interaction, amount: int):
    stats = get_stats(interaction.guild_id)
    new_exp = max(0, stats["current"] - amount)
    new_lvl = sync_level(new_exp)
    update_stats(interaction.guild_id, new_exp, new_lvl)
    
    await interaction.response.send_message(f"🔻 Removed {amount:,} EXP. Level: {new_lvl}", ephemeral=True)
    await refresh_board(interaction.guild)

@bot.tree.command(name="expset", description="Set exact experience amount")
@app_commands.checks.has_permissions(administrator=True)
async def expset(interaction: discord.Interaction, amount: int):
    new_exp = max(0, amount)
    new_lvl = sync_level(new_exp)
    update_stats(interaction.guild_id, new_exp, new_lvl)
    
    await interaction.response.send_message(f"⚙️ Experience set to {amount:,}. Level: {new_lvl}", ephemeral=True)
    await refresh_board(interaction.guild)

if __name__ == "__main__":
    keep_alive()
    token = os.getenv('DISCORD_TOKEN')
    if token:
        bot.run(token)
    else:
        print("ERROR: DISCORD_TOKEN not found!")
  
