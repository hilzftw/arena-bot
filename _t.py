import os
os.environ.update(DISCORD_TOKEN="x", GUILD_ID="123", GUEST_ROLE_ID="1", FRIEND_ROLE_ID="2")
import config, db, core, ironforge, blizzard
from cogs.verification import VerifyPanel
from cogs.lfg import LFGJoinButton
# construct persistent components
vp = VerifyPanel(); print("VerifyPanel items:", [c.custom_id for c in vp.children])
b = LFGJoinButton(7); print("LFG custom_id:", b.item.custom_id, "| template:", LFGJoinButton.__discord_ui_template__.pattern)
print("rating->key:", ironforge.determine_role_key(1850), ironforge.determine_role_key(0))
print("parse:", core.parse_character_realm("Brutus-Whitemane"), core.parse_character_realm("bad"))
import bot as botmod
b2 = botmod.ArenaBot(); print("Bot intents members/voice:", b2.intents.members, b2.intents.voice_states)
print("ALL_OK")
