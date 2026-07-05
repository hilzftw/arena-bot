import os, asyncio
os.environ.update(DISCORD_TOKEN="x", GUILD_ID="123", GUEST_ROLE_ID="1", FRIEND_ROLE_ID="2")
import config, db, core, ironforge, blizzard
from cogs.lfg import LFGJoinButton
b = LFGJoinButton(7)
print("LFG custom_id:", b.item.custom_id)
print("template compiled:", LFGJoinButton.__discord_ui_compiled_template__.pattern)
print("rating->key:", ironforge.determine_role_key(2500), ironforge.determine_role_key(1850), ironforge.determine_role_key(1500), ironforge.determine_role_key(0))
print("parse ok/bad:", core.parse_character_realm("Brutus-Whitemane"), core.parse_character_realm("bad"))
import bot as botmod
bt = botmod.ArenaBot()
print("intents members/voice:", bt.intents.members, bt.intents.voice_states)
# exercise db against a temp file
os.environ["DB_PATH"]="/tmp/probe.db"
import importlib; importlib.reload(config); importlib.reload(db)
async def dbtest():
    await db.init_db()
    await db.upsert_user("100","Brutus","Whitemane","US","Alliance","Warrior","Arms",1850,"1800+","ironforge", core.guest_expiry_ts())
    u=await db.get_user("100"); print("user rating/expires set:", u["rating"], u["expires_at"] is not None)
    await db.set_expiry("100", None); u=await db.get_user("100"); print("friend expiry None:", u["expires_at"])
    eid=await db.add_lfg("100","2v2",1850,1800,2000,"m","c",30); e=await db.get_lfg(eid); print("lfg active:", e["active"])
    exp=await db.take_expired_lfg(now=99999999999); print("expired count:", len(exp))
    await db.add_blacklist("200","spam"); print("blacklisted:", await db.is_blacklisted("200"))
    await db.close()
asyncio.run(dbtest())
print("ALL_OK")
