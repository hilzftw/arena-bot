import os, asyncio
os.environ.update(DISCORD_TOKEN="x", GUILD_ID="123", GUEST_ROLE_ID="1", FRIEND_ROLE_ID="2", DB_PATH="/tmp/p3.db")
import bot as botmod
async def run():
    bt = botmod.ArenaBot()
    for ext in botmod.COGS:
        await bt.load_extension(ext)
    cmds = sorted(c.qualified_name for c in bt.tree.walk_commands())
    print("commands:", cmds)
    listeners = [k for k in bt.extra_events]
    print("listeners:", sorted(listeners))
    # stop any started loops cleanly
    for cog in list(bt.cogs.values()):
        await bt.remove_cog(cog.qualified_name)
    print("COG_OK")
asyncio.run(run())
