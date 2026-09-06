import os
import re
import json
import random
import asyncio
from datetime import timedelta
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", "0"))
STAFF_ROLE_ID = int(os.getenv("STAFF_ROLE_ID", "0"))
VERIFIED_ROLE_ID = int(os.getenv("VERIFIED_ROLE_ID", "0"))

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

MESSAGE_LINK_RE = re.compile(r"channels/(\d+)/(\d+)/(\d+)")

# message_id -> GiveawayView (in-memory only; lost on restart)
active_giveaways = {}

# ---------------------------------------------------------
# SIMPLE JSON STORAGE — WARNINGS
# ---------------------------------------------------------

WARNINGS_FILE = "warnings.json"


def load_warnings():
    if not os.path.exists(WARNINGS_FILE):
        return {}
    with open(WARNINGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_warnings(data):
    with open(WARNINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def is_staff_member(user: discord.Member) -> bool:
    if user.guild_permissions.manage_guild:
        return True
    if STAFF_ROLE_ID and any(r.id == STAFF_ROLE_ID for r in getattr(user, "roles", [])):
        return True
    return False


def is_ticket_channel(channel: discord.TextChannel) -> bool:
    return bool(channel.topic and channel.topic.startswith("Ticket for"))


def parse_color(value: str, fallback=discord.Color.blurple()) -> discord.Color:
    if not value:
        return fallback
    try:
        return discord.Color(int(value.strip().replace("#", ""), 16))
    except ValueError:
        return fallback


async def resolve_message_from_url(interaction: discord.Interaction, url: str):
    """Parses a Discord message link and returns (message, error_text)."""
    match = MESSAGE_LINK_RE.search(url)
    if not match:
        return None, "❌ That doesn't look like a valid message link. Right-click the embed message → **Copy Message Link**."

    guild_id, channel_id, message_id = map(int, match.groups())

    if guild_id != interaction.guild.id:
        return None, "❌ That message is from a different server."

    channel = interaction.guild.get_channel(channel_id)
    if channel is None:
        return None, "❌ I can't find that channel (maybe I don't have access to it)."

    try:
        message = await channel.fetch_message(message_id)
    except discord.NotFound:
        return None, "❌ Message not found — double-check the link."
    except discord.Forbidden:
        return None, "❌ I don't have permission to view that channel."

    if message.author.id != bot.user.id:
        return None, "❌ I can only edit embeds that I sent myself."

    if not message.embeds:
        return None, "❌ That message doesn't have an embed."

    return message, None


async def rename_message_buttons(message: discord.Message, labels: list):
    """Renames buttons on a message, in order. Entries in `labels` that are
    None/empty are left unchanged. Returns how many buttons were renamed."""
    labels = list(labels)
    applied = 0

    live_view = active_giveaways.get(message.id)
    if live_view:
        idx = 0
        for child in live_view.children:
            if isinstance(child, discord.ui.Button):
                if idx < len(labels) and labels[idx]:
                    child.label = labels[idx]
                    applied += 1
                idx += 1
        if applied:
            await message.edit(view=live_view)
        return applied

    if not message.components:
        return 0

    new_view = discord.ui.View(timeout=None)
    idx = 0
    for row in message.components:
        for comp in row.children:
            if isinstance(comp, discord.Button):
                new_label = comp.label
                if idx < len(labels) and labels[idx]:
                    new_label = labels[idx]
                    applied += 1
                new_view.add_item(discord.ui.Button(
                    style=comp.style,
                    label=new_label,
                    emoji=comp.emoji,
                    custom_id=comp.custom_id,
                    url=comp.url,
                    disabled=comp.disabled,
                ))
                idx += 1

    if applied:
        await message.edit(view=new_view)
    return applied


@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands.")
    except Exception as e:
        print(f"Error syncing commands: {e}")
    print(f"Bot online as {bot.user}")


@bot.event
async def on_connect():
    bot.add_view(TicketOpenView())
    bot.add_view(TicketCloseView())
    bot.add_view(VerifyView())


# ===========================================================
# MODERATION
# ===========================================================

@bot.tree.command(name="kick", description="Kick a member from the server")
@app_commands.describe(member="Member to kick", reason="Reason for the kick")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "Not specified"):
    await member.kick(reason=reason)
    embed = discord.Embed(title="👢 Member Kicked", description=f"**{member}** was kicked from the server.", color=discord.Color.orange())
    embed.add_field(name="Reason", value=reason)
    embed.add_field(name="Moderator", value=interaction.user.mention)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="ban", description="Ban a member from the server")
@app_commands.describe(member="Member to ban", reason="Reason for the ban")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "Not specified"):
    await member.ban(reason=reason)
    embed = discord.Embed(title="🔨 Member Banned", description=f"**{member}** was banned from the server.", color=discord.Color.red())
    embed.add_field(name="Reason", value=reason)
    embed.add_field(name="Moderator", value=interaction.user.mention)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="unban", description="Unban a user by their ID")
@app_commands.describe(user_id="ID of the user to unban")
@app_commands.checks.has_permissions(ban_members=True)
async def unban(interaction: discord.Interaction, user_id: str):
    try:
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user)
        embed = discord.Embed(title="✅ User Unbanned", description=f"**{user}** has been unbanned.", color=discord.Color.green())
        await interaction.response.send_message(embed=embed)
    except (ValueError, discord.NotFound):
        await interaction.response.send_message("❌ Invalid ID or the user is not banned.", ephemeral=True)


@bot.tree.command(name="timeout", description="Timeout a member for a number of minutes")
@app_commands.describe(member="Member to timeout", minutes="Duration in minutes", reason="Reason")
@app_commands.checks.has_permissions(moderate_members=True)
async def timeout(interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "Not specified"):
    duration = discord.utils.utcnow() + timedelta(minutes=minutes)
    await member.timeout(duration, reason=reason)
    embed = discord.Embed(title="🔇 Member Timed Out", description=f"**{member}** was timed out for {minutes} minute(s).", color=discord.Color.gold())
    embed.add_field(name="Reason", value=reason)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="remove-timeout", description="Remove a member's timeout")
@app_commands.describe(member="Member to release")
@app_commands.checks.has_permissions(moderate_members=True)
async def remove_timeout(interaction: discord.Interaction, member: discord.Member):
    await member.timeout(None)
    await interaction.response.send_message(f"🔊 **{member}** is no longer timed out.")


@bot.tree.command(name="clear", description="Delete a number of messages from the channel")
@app_commands.describe(amount="Number of messages to delete (max 100)")
@app_commands.checks.has_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]):
    await interaction.response.defer()
    deleted = await interaction.channel.purge(limit=amount)
    embed = discord.Embed(description=f"🧹 {len(deleted)} messages deleted by {interaction.user.mention}.", color=discord.Color.blurple())
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="nuke", description="Completely clears the current channel by recreating it")
@app_commands.checks.has_permissions(manage_channels=True)
async def nuke(interaction: discord.Interaction):
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message("❌ This command only works in text channels.", ephemeral=True)
        return

    bot_member = interaction.guild.me
    if not channel.permissions_for(bot_member).manage_channels:
        await interaction.response.send_message(
            "❌ I don't have the **Manage Channels** permission, so I can't nuke this channel. "
            "Ask a server admin to grant it to my role.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    try:
        position = channel.position
        new_channel = await channel.clone(reason=f"Nuke by {interaction.user}")
        await channel.delete(reason=f"Nuke by {interaction.user}")
        try:
            await new_channel.edit(position=position)
        except discord.HTTPException:
            pass

        embed = discord.Embed(
            title="💥 Channel Nuked",
            description=f"This channel was cleared by {interaction.user.mention}.",
            color=discord.Color.dark_red()
        )
        await new_channel.send(embed=embed)
        await interaction.followup.send(f"✅ Done: {new_channel.mention}", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ Discord refused the action — check that my role is above other role restrictions "
            "and that I have Manage Channels in this category.",
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(f"⚠️ Unexpected error while nuking the channel: `{e}`", ephemeral=True)


@bot.tree.command(name="lock", description="Lock the channel (no one can send messages)")
@app_commands.checks.has_permissions(manage_channels=True)
async def lock(interaction: discord.Interaction):
    await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=False)
    embed = discord.Embed(description="🔒 Channel locked.", color=discord.Color.red())
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="unlock", description="Unlock the channel")
@app_commands.checks.has_permissions(manage_channels=True)
async def unlock(interaction: discord.Interaction):
    await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=True)
    embed = discord.Embed(description="🔓 Channel unlocked.", color=discord.Color.green())
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="slowmode", description="Set the channel's slowmode (0 disables it)")
@app_commands.describe(seconds="Seconds between messages (0-21600)")
@app_commands.checks.has_permissions(manage_channels=True)
async def slowmode(interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 21600]):
    await interaction.channel.edit(slowmode_delay=seconds)
    if seconds == 0:
        await interaction.response.send_message("🐇 Slowmode disabled.")
    else:
        await interaction.response.send_message(f"🐢 Slowmode set to {seconds} seconds.")


@bot.tree.command(name="warn", description="Warn a member")
@app_commands.describe(member="Member to warn", reason="Reason for the warning")
@app_commands.checks.has_permissions(moderate_members=True)
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str):
    data = load_warnings()
    guild_id = str(interaction.guild.id)
    user_id = str(member.id)
    data.setdefault(guild_id, {}).setdefault(user_id, [])
    data[guild_id][user_id].append({
        "reason": reason,
        "moderator": str(interaction.user),
        "date": discord.utils.utcnow().strftime("%Y-%m-%d %H:%M")
    })
    save_warnings(data)
    total = len(data[guild_id][user_id])
    embed = discord.Embed(title="⚠️ Warning Issued", color=discord.Color.yellow())
    embed.add_field(name="Member", value=member.mention)
    embed.add_field(name="Reason", value=reason)
    embed.add_field(name="Total warnings", value=str(total))
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="warnings", description="List a member's warnings")
@app_commands.describe(member="Member to check")
async def warnings_cmd(interaction: discord.Interaction, member: discord.Member):
    data = load_warnings()
    entries = data.get(str(interaction.guild.id), {}).get(str(member.id), [])
    if not entries:
        await interaction.response.send_message(f"{member.mention} has no warnings.")
        return
    embed = discord.Embed(title=f"⚠️ Warnings for {member}", color=discord.Color.yellow())
    for i, w in enumerate(entries, start=1):
        embed.add_field(name=f"#{i} — {w['date']}", value=f"Reason: {w['reason']}\nBy: {w['moderator']}", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="remove-warning", description="Remove a specific warning from a member (by number)")
@app_commands.describe(member="Member", number="Warning number (see /warnings)")
@app_commands.checks.has_permissions(moderate_members=True)
async def remove_warning(interaction: discord.Interaction, member: discord.Member, number: int):
    data = load_warnings()
    guild_id = str(interaction.guild.id)
    user_id = str(member.id)
    entries = data.get(guild_id, {}).get(user_id, [])
    if number < 1 or number > len(entries):
        await interaction.response.send_message("❌ Invalid warning number.", ephemeral=True)
        return
    removed = entries.pop(number - 1)
    save_warnings(data)
    await interaction.response.send_message(f"✅ Warning removed: {removed['reason']}")


# ===========================================================
# SECURITY
# ===========================================================

@bot.tree.command(name="account-age", description="Check how old a member's Discord account is")
@app_commands.describe(member="Member to check (leave empty for yourself)")
async def account_age(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    age_days = (discord.utils.utcnow() - member.created_at).days
    suspicious = age_days < 7

    embed = discord.Embed(
        title=f"🔍 Account Age — {member}",
        color=discord.Color.red() if suspicious else discord.Color.green()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Account created", value=discord.utils.format_dt(member.created_at, "R"))
    embed.add_field(name="Age in days", value=str(age_days))
    if suspicious:
        embed.add_field(name="⚠️ Warning", value="This account is very new — could be a raid or alt account.", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="recent-joins", description="List members who joined in the last N hours (raid check)")
@app_commands.describe(hours="How many hours back to check (default 24)")
@app_commands.checks.has_permissions(moderate_members=True)
async def recent_joins(interaction: discord.Interaction, hours: app_commands.Range[int, 1, 168] = 24):
    cutoff = discord.utils.utcnow() - timedelta(hours=hours)
    members = [m for m in interaction.guild.members if m.joined_at and m.joined_at > cutoff]
    members.sort(key=lambda m: m.joined_at, reverse=True)

    if not members:
        await interaction.response.send_message(f"No members joined in the last {hours} hour(s).")
        return

    embed = discord.Embed(title=f"👥 Joined in the last {hours}h ({len(members)} total)", color=discord.Color.blurple())
    lines = [f"{m.mention} — account created {discord.utils.format_dt(m.created_at, 'R')}" for m in members[:25]]
    embed.description = "\n".join(lines)
    if len(members) > 25:
        embed.set_footer(text=f"Showing 25 of {len(members)} members")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="lockdown", description="Emergency: lock every text channel in the server")
@app_commands.checks.has_permissions(administrator=True)
async def lockdown(interaction: discord.Interaction):
    await interaction.response.defer()
    count = 0
    for channel in interaction.guild.text_channels:
        try:
            await channel.set_permissions(interaction.guild.default_role, send_messages=False)
            count += 1
        except discord.Forbidden:
            continue
    embed = discord.Embed(
        title="🚨 Server Lockdown Activated",
        description=f"{count} channel(s) locked by {interaction.user.mention}.",
        color=discord.Color.dark_red()
    )
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="unlock-all", description="Lift a server-wide lockdown")
@app_commands.checks.has_permissions(administrator=True)
async def unlock_all(interaction: discord.Interaction):
    await interaction.response.defer()
    count = 0
    for channel in interaction.guild.text_channels:
        try:
            await channel.set_permissions(interaction.guild.default_role, send_messages=True)
            count += 1
        except discord.Forbidden:
            continue
    embed = discord.Embed(
        title="✅ Lockdown Lifted",
        description=f"{count} channel(s) unlocked by {interaction.user.mention}.",
        color=discord.Color.green()
    )
    await interaction.followup.send(embed=embed)


for cmd in [kick, ban, unban, timeout, remove_timeout, clear, nuke, lock, unlock, slowmode,
            warn, remove_warning, recent_joins, lockdown, unlock_all]:
    async def _err(interaction: discord.Interaction, error, _cmd=cmd):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        else:
            await interaction.response.send_message(f"⚠️ Error: {error}", ephemeral=True)
    cmd.error(_err)


# ===========================================================
# VERIFICATION
# ===========================================================

class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ Verify", style=discord.ButtonStyle.success, custom_id="verify_button")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not VERIFIED_ROLE_ID:
            await interaction.response.send_message(
                "⚠️ Verification isn't configured yet. Ask an admin to set VERIFIED_ROLE_ID.",
                ephemeral=True
            )
            return

        role = interaction.guild.get_role(VERIFIED_ROLE_ID)
        if not role:
            await interaction.response.send_message("⚠️ The verification role couldn't be found.", ephemeral=True)
            return

        if role in interaction.user.roles:
            await interaction.response.send_message("You're already verified! ✅", ephemeral=True)
            return

        try:
            await interaction.user.add_roles(role, reason="Self-verification")
            await interaction.response.send_message("✅ You're verified! Welcome to the server.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have permission to assign that role. Ask an admin to check my role position.",
                ephemeral=True
            )


@bot.tree.command(name="verify-panel", description="Post the verification panel")
@app_commands.checks.has_permissions(manage_roles=True)
async def verify_panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🔒 Server Verification",
        description="Click the button below to verify yourself and unlock full access to the server.",
        color=discord.Color.green()
    )
    embed.set_footer(text="This helps keep out bots and raid accounts.")
    await interaction.response.send_message(embed=embed, view=VerifyView())


@verify_panel.error
async def verify_panel_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
    else:
        await interaction.response.send_message(f"⚠️ Error: {error}", ephemeral=True)


# ===========================================================
# UTILITY
# ===========================================================

@bot.tree.command(name="ping", description="Show the bot's latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 Pong! {round(bot.latency * 1000)}ms")


@bot.tree.command(name="avatar", description="Show a user's avatar")
@app_commands.describe(member="Member (leave empty to see your own)")
async def avatar(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    embed = discord.Embed(title=f"{member}'s avatar", color=discord.Color.blurple())
    embed.set_image(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="userinfo", description="Show information about a member")
@app_commands.describe(member="Member (leave empty to see your own)")
async def userinfo(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    embed = discord.Embed(title=f"Info about {member}", color=member.color)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID", value=member.id)
    embed.add_field(name="Joined server", value=discord.utils.format_dt(member.joined_at, "R"))
    embed.add_field(name="Account created", value=discord.utils.format_dt(member.created_at, "R"))
    roles = [r.mention for r in member.roles if r.name != "@everyone"]
    embed.add_field(name=f"Roles ({len(roles)})", value=" ".join(roles) if roles else "None", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="serverinfo", description="Show information about the server")
async def serverinfo(interaction: discord.Interaction):
    guild = interaction.guild
    embed = discord.Embed(title=guild.name, color=discord.Color.blurple())
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name="Owner", value=str(guild.owner))
    embed.add_field(name="Members", value=guild.member_count)
    embed.add_field(name="Created on", value=discord.utils.format_dt(guild.created_at, "R"))
    embed.add_field(name="Text channels", value=len(guild.text_channels))
    embed.add_field(name="Voice channels", value=len(guild.voice_channels))
    embed.add_field(name="Roles", value=len(guild.roles))
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="say", description="Make the bot send a message in this channel (staff)")
@app_commands.describe(message="Text to send")
@app_commands.checks.has_permissions(manage_messages=True)
async def say(interaction: discord.Interaction, message: str):
    await interaction.channel.send(message)
    await interaction.response.send_message("✅ Sent.", ephemeral=True)


@bot.tree.command(name="help", description="List all bot commands")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📖 Help Center",
        description="Here's everything I can do, organized by category.",
        color=discord.Color.blurple()
    )
    embed.add_field(
        name="🛡️ Moderation",
        value=(
            "`/kick` · `/ban` · `/unban`\n"
            "`/timeout` · `/remove-timeout`\n"
            "`/clear` · `/nuke`\n"
            "`/lock` · `/unlock` · `/slowmode`\n"
            "`/warn` · `/warnings` · `/remove-warning`"
        ),
        inline=False
    )
    embed.add_field(
        name="🔐 Security",
        value=(
            "`/verify-panel` — post the verification panel\n"
            "`/account-age` — check if an account looks suspicious\n"
            "`/recent-joins` — spot possible raids\n"
            "`/lockdown` · `/unlock-all` — server-wide emergency lock"
        ),
        inline=False
    )
    embed.add_field(
        name="🎫 Tickets",
        value=(
            "`/ticket-panel` — post the ticket panel\n"
            "`/add-member` · `/remove-member` — manage ticket access\n"
            "Buttons inside a ticket: **Close Ticket**, **Claim Ticket**"
        ),
        inline=False
    )
    embed.add_field(
        name="🎉 Giveaways",
        value=(
            "`/giveaway` — opens a form to customize the embed, plus optional required/blocked roles\n"
            "`/edit-giveaway` — change prize, winners, roles or time on an active giveaway\n"
            "`/giveaway-reroll` — pick new winner(s)\n"
            "Buttons: **Enter Giveaway** and **Participants** (see who's in)"
        ),
        inline=False
    )
    embed.add_field(
        name="🧰 Utility",
        value="`/ping` · `/avatar` · `/userinfo` · `/serverinfo` · `/say`",
        inline=False
    )
    embed.add_field(
        name="🎨 Embeds",
        value=(
            "`/embed` — opens a form to build an embed (title, description, color, image, thumbnail)\n"
            "`/edit-embed` — paste a message link to edit ANY embed I sent (regular embed, ticket, or giveaway); "
            "you can also rename its buttons right from this command"
        ),
        inline=False
    )
    embed.set_footer(text="Type / in the chat to see each command's parameters")
    await interaction.response.send_message(embed=embed)


# ===========================================================
# EMBED BUILDER + UNIVERSAL EDITOR (URL-BASED)
# ===========================================================

class EmbedModal(discord.ui.Modal, title="Create Embed"):
    def __init__(self):
        super().__init__()
        self.title_input = discord.ui.TextInput(label="Title", required=False, max_length=256)
        self.description_input = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, required=False, max_length=4000)
        self.color_input = discord.ui.TextInput(label="Color (hex, e.g. #5865F2)", required=False, max_length=7)
        self.image_input = discord.ui.TextInput(label="Image URL", required=False)
        self.thumbnail_input = discord.ui.TextInput(label="Thumbnail URL", required=False)

        for item in [self.title_input, self.description_input, self.color_input, self.image_input, self.thumbnail_input]:
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title=self.title_input.value or None,
            description=self.description_input.value or None,
            color=parse_color(self.color_input.value)
        )
        if self.image_input.value:
            embed.set_image(url=self.image_input.value)
        if self.thumbnail_input.value:
            embed.set_thumbnail(url=self.thumbnail_input.value)
        embed.set_footer(text=f"Created by {interaction.user}", icon_url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed)


@bot.tree.command(name="embed", description="Create a custom embed using an interactive form")
async def embed_command(interaction: discord.Interaction):
    await interaction.response.send_modal(EmbedModal())


class EditEmbedModal(discord.ui.Modal, title="Edit Embed"):
    def __init__(self, target_message: discord.Message, button_labels: list = None):
        super().__init__()
        self.target_message = target_message
        self.button_labels = button_labels or []
        existing = target_message.embeds[0]
        current_color = f"#{existing.color.value:06X}" if existing.color else None

        self.title_input = discord.ui.TextInput(label="Title", required=False, max_length=256, default=existing.title)
        self.description_input = discord.ui.TextInput(
            label="Description", style=discord.TextStyle.paragraph, required=False, max_length=4000,
            default=existing.description
        )
        self.color_input = discord.ui.TextInput(label="Color (hex)", required=False, max_length=7, default=current_color)
        self.image_input = discord.ui.TextInput(label="Image URL", required=False, default=existing.image.url if existing.image else None)
        self.thumbnail_input = discord.ui.TextInput(label="Thumbnail URL", required=False, default=existing.thumbnail.url if existing.thumbnail else None)

        for item in [self.title_input, self.description_input, self.color_input, self.image_input, self.thumbnail_input]:
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        embed = self.target_message.embeds[0]
        embed.title = self.title_input.value or None
        embed.description = self.description_input.value or None
        embed.color = parse_color(self.color_input.value, fallback=embed.color or discord.Color.blurple())
        embed.set_image(url=self.image_input.value) if self.image_input.value else embed.set_image(url=None)
        embed.set_thumbnail(url=self.thumbnail_input.value) if self.thumbnail_input.value else embed.set_thumbnail(url=None)

        await self.target_message.edit(embed=embed)

        note = ""
        if any(self.button_labels):
            renamed = await rename_message_buttons(self.target_message, self.button_labels)
            if renamed:
                note = f"\n🔘 Renamed {renamed} button(s)."
            else:
                note = "\n⚠️ No buttons found on that message to rename."

        await interaction.response.send_message("✅ Embed updated." + note)


@bot.tree.command(name="edit-embed", description="Edit any embed I sent by pasting its message link")
@app_commands.describe(
    url="Right-click the message with the embed → Copy Message Link",
    button_1_label="Rename the message's first button (optional)",
    button_2_label="Rename the message's second button (optional)"
)
@app_commands.checks.has_permissions(manage_messages=True)
async def edit_embed(interaction: discord.Interaction, url: str, button_1_label: str = None, button_2_label: str = None):
    message, error = await resolve_message_from_url(interaction, url)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return
    await interaction.response.send_modal(
        EditEmbedModal(message, button_labels=[button_1_label, button_2_label])
    )


@edit_embed.error
async def edit_embed_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
    else:
        await interaction.response.send_message(f"⚠️ Error: {error}", ephemeral=True)


# ===========================================================
# TICKET SYSTEM
# ===========================================================

class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id="close_ticket")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Closing this ticket in 5 seconds...")
        await interaction.channel.edit(name=f"closed-{interaction.channel.name}")
        await interaction.channel.set_permissions(interaction.guild.default_role, view_channel=False)
        await asyncio.sleep(5)
        await interaction.channel.delete()

    @discord.ui.button(label="Claim Ticket", style=discord.ButtonStyle.success, custom_id="claim_ticket")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff_member(interaction.user):
            await interaction.response.send_message("❌ Only staff can claim tickets.", ephemeral=True)
            return
        message = interaction.message
        if message.embeds:
            embed = message.embeds[0]
            embed.add_field(name="Claimed by", value=interaction.user.mention, inline=False)
            await message.edit(embed=embed)
        await interaction.response.send_message(f"✅ {interaction.user.mention} claimed this ticket.")


class TicketOpenView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎫 Open Ticket", style=discord.ButtonStyle.primary, custom_id="open_ticket")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        author = interaction.user

        channel_name = f"ticket-{author.name}".lower().replace(" ", "-")
        existing = discord.utils.get(guild.text_channels, name=channel_name)
        if existing:
            await interaction.response.send_message(f"You already have an open ticket: {existing.mention}", ephemeral=True)
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            author: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }

        if STAFF_ROLE_ID:
            staff_role = guild.get_role(STAFF_ROLE_ID)
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        category = guild.get_channel(TICKET_CATEGORY_ID) if TICKET_CATEGORY_ID else None

        channel = await guild.create_text_channel(
            name=channel_name,
            overwrites=overwrites,
            category=category,
            topic=f"Ticket for {author.id}"
        )

        embed = discord.Embed(
            title="🎫 Ticket Opened",
            description=f"Hi {author.mention}! Describe your issue and staff will be with you shortly.",
            color=discord.Color.blurple()
        )
        await channel.send(embed=embed, view=TicketCloseView())
        await interaction.response.send_message(f"✅ Ticket created: {channel.mention}", ephemeral=True)


@bot.tree.command(name="ticket-panel", description="Send the ticket opening panel")
@app_commands.checks.has_permissions(manage_channels=True)
async def ticket_panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📩 Support",
        description="Click the button below to open a support ticket.",
        color=discord.Color.blurple()
    )
    await interaction.response.send_message(embed=embed, view=TicketOpenView())


@bot.tree.command(name="add-member", description="Add a member to the current ticket")
@app_commands.describe(member="Member to add")
async def add_member(interaction: discord.Interaction, member: discord.Member):
    if not is_ticket_channel(interaction.channel):
        await interaction.response.send_message("❌ Use this command inside a ticket channel.", ephemeral=True)
        return
    if not is_staff_member(interaction.user):
        await interaction.response.send_message("❌ Only staff can add members.", ephemeral=True)
        return
    await interaction.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
    await interaction.response.send_message(f"✅ {member.mention} was added to the ticket.")


@bot.tree.command(name="remove-member", description="Remove a member from the current ticket")
@app_commands.describe(member="Member to remove")
async def remove_member(interaction: discord.Interaction, member: discord.Member):
    if not is_ticket_channel(interaction.channel):
        await interaction.response.send_message("❌ Use this command inside a ticket channel.", ephemeral=True)
        return
    if not is_staff_member(interaction.user):
        await interaction.response.send_message("❌ Only staff can remove members.", ephemeral=True)
        return
    await interaction.channel.set_permissions(member, overwrite=None)
    await interaction.response.send_message(f"✅ {member.mention} was removed from the ticket.")


# ===========================================================
# GIVEAWAYS
# ===========================================================

def format_giveaway_description(prize: str) -> str:
    return f"Prize: **{prize}**\nClick the button below to enter!"


class GiveawayView(discord.ui.View):
    def __init__(self, prize: str, winners_count: int, host: discord.Member,
                 ends_at, required_role_id: int = None, blocked_role_id: int = None):
        # timeout=None: we manage ending ourselves (so duration can be edited later)
        super().__init__(timeout=None)
        self.prize = prize
        self.winners_count = winners_count
        self.host = host
        self.ends_at = ends_at
        self.required_role_id = required_role_id
        self.blocked_role_id = blocked_role_id
        self.entrants = set()
        self.message = None
        self.updater_task = None
        self.end_task = None
        self.ended = False

        enter_button = discord.ui.Button(label="🎉 Enter Giveaway", style=discord.ButtonStyle.blurple)
        enter_button.callback = self.enter_callback
        self.add_item(enter_button)

        participants_button = discord.ui.Button(label="👥 Participants", style=discord.ButtonStyle.secondary)
        participants_button.callback = self.view_participants_callback
        self.add_item(participants_button)

    async def enter_callback(self, interaction: discord.Interaction):
        member = interaction.user

        if self.blocked_role_id and any(r.id == self.blocked_role_id for r in member.roles):
            await interaction.response.send_message("❌ You're not allowed to enter this giveaway.", ephemeral=True)
            return

        if self.required_role_id and not any(r.id == self.required_role_id for r in member.roles):
            await interaction.response.send_message(
                f"❌ You need the <@&{self.required_role_id}> role to enter this giveaway.", ephemeral=True
            )
            return

        if member.id in self.entrants:
            self.entrants.discard(member.id)
            await interaction.response.send_message("❌ You left the giveaway.", ephemeral=True)
        else:
            self.entrants.add(member.id)
            await interaction.response.send_message("✅ You entered the giveaway! Click again to leave.", ephemeral=True)

        await self.refresh_embed()

    async def view_participants_callback(self, interaction: discord.Interaction):
        if not self.entrants:
            await interaction.response.send_message("No one has entered yet.", ephemeral=True)
            return
        entrants_list = list(self.entrants)
        mentions = "\n".join(f"<@{uid}>" for uid in entrants_list[:50])
        extra = f"\n...and {len(entrants_list) - 50} more" if len(entrants_list) > 50 else ""
        await interaction.response.send_message(f"**Participants ({len(entrants_list)}):**\n{mentions}{extra}", ephemeral=True)

    async def refresh_embed(self):
        if not self.message or not self.message.embeds:
            return
        embed = self.message.embeds[0]
        for i, field in enumerate(embed.fields):
            if field.name == "Entries":
                embed.set_field_at(i, name="Entries", value=str(len(self.entrants)), inline=field.inline)
            elif field.name == "Time Remaining":
                embed.set_field_at(i, name="Time Remaining", value=discord.utils.format_dt(self.ends_at, "R"), inline=field.inline)
        try:
            await self.message.edit(embed=embed)
        except discord.HTTPException:
            pass

    async def update_loop(self):
        # Keeps "Time Remaining" and "Entries" fresh even if no one clicks the button
        try:
            while not self.ended:
                await asyncio.sleep(30)
                if self.ended:
                    break
                await self.refresh_embed()
        except asyncio.CancelledError:
            pass

    def schedule_end(self):
        """(Re)schedules the giveaway's end based on the current self.ends_at.
        Safe to call again after the duration is edited."""
        if self.end_task:
            self.end_task.cancel()
        self.end_task = asyncio.create_task(self._wait_and_end())

    async def _wait_and_end(self):
        try:
            while True:
                remaining = (self.ends_at - discord.utils.utcnow()).total_seconds()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(remaining, 30))
            await self.end_giveaway()
        except asyncio.CancelledError:
            pass

    async def end_giveaway(self):
        if self.ended:
            return
        self.ended = True

        if self.updater_task:
            self.updater_task.cancel()

        for child in self.children:
            child.disabled = True

        if self.message and self.message.embeds:
            embed = self.message.embeds[0]
            for i, field in enumerate(embed.fields):
                if field.name == "Time Remaining":
                    embed.set_field_at(i, name="Time Remaining", value="Ended", inline=field.inline)
                elif field.name == "Entries":
                    embed.set_field_at(i, name="Entries", value=str(len(self.entrants)), inline=field.inline)
            try:
                await self.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass

        self.stop()

        channel = self.message.channel if self.message else None
        if not channel:
            return

        await channel.send(embed=self.build_winner_embed())

    def build_winner_embed(self) -> discord.Embed:
        if not self.entrants:
            return discord.Embed(
                title="🎉 Giveaway Ended",
                description=f"No one entered the giveaway for **{self.prize}**.",
                color=discord.Color.red()
            )
        winners_count = min(self.winners_count, len(self.entrants))
        winners = random.sample(list(self.entrants), winners_count)
        mentions = ", ".join(f"<@{w}>" for w in winners)
        return discord.Embed(
            title="🎉 Giveaway Ended",
            description=f"Congratulations {mentions}! You won **{self.prize}**!",
            color=discord.Color.green()
        )


class GiveawayEmbedModal(discord.ui.Modal, title="Customize Giveaway Embed"):
    def __init__(self, prize: str, duration_minutes: int, winners_count: int, host: discord.Member,
                 required_role: discord.Role = None, blocked_role: discord.Role = None):
        super().__init__()
        self.prize = prize
        self.duration_minutes = duration_minutes
        self.winners_count = winners_count
        self.host = host
        self.required_role = required_role
        self.blocked_role = blocked_role

        self.title_input = discord.ui.TextInput(
            label="Title", required=False, max_length=256, default="🎉 GIVEAWAY 🎉"
        )
        self.description_input = discord.ui.TextInput(
            label="Description", style=discord.TextStyle.paragraph, required=False, max_length=4000,
            default=format_giveaway_description(prize)
        )
        self.color_input = discord.ui.TextInput(label="Color (hex, e.g. #5865F2)", required=False, max_length=7)
        self.image_input = discord.ui.TextInput(label="Image URL", required=False)
        self.thumbnail_input = discord.ui.TextInput(label="Thumbnail URL", required=False)

        for item in [self.title_input, self.description_input, self.color_input, self.image_input, self.thumbnail_input]:
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        ends_at = discord.utils.utcnow() + timedelta(minutes=self.duration_minutes)

        embed = discord.Embed(
            title=self.title_input.value or None,
            description=self.description_input.value or None,
            color=parse_color(self.color_input.value)
        )
        if self.image_input.value:
            embed.set_image(url=self.image_input.value)
        if self.thumbnail_input.value:
            embed.set_thumbnail(url=self.thumbnail_input.value)

        embed.add_field(name="Winners", value=str(self.winners_count))
        embed.add_field(name="Time Remaining", value=discord.utils.format_dt(ends_at, "R"))
        embed.add_field(name="Entries", value="0")
        embed.add_field(name="Hosted by", value=self.host.mention)
        if self.required_role:
            embed.add_field(name="Required Role", value=self.required_role.mention)
        if self.blocked_role:
            embed.add_field(name="Blocked Role", value=self.blocked_role.mention)
        embed.set_footer(text="Click the button to enter — click again to leave")

        view = GiveawayView(
            prize=self.prize,
            winners_count=self.winners_count,
            host=self.host,
            ends_at=ends_at,
            required_role_id=self.required_role.id if self.required_role else None,
            blocked_role_id=self.blocked_role.id if self.blocked_role else None,
        )
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()
        active_giveaways[view.message.id] = view
        view.updater_task = asyncio.create_task(view.update_loop())
        view.schedule_end()


@bot.tree.command(name="giveaway", description="Start a giveaway")
@app_commands.describe(
    prize="What you're giving away",
    duration_minutes="How long the giveaway lasts, in minutes",
    winners="Number of winners (default 1)",
    required_role="Only members with this role can enter (optional)",
    blocked_role="Members with this role cannot enter (optional)"
)
@app_commands.checks.has_permissions(manage_guild=True)
async def giveaway(
    interaction: discord.Interaction,
    prize: str,
    duration_minutes: app_commands.Range[int, 1, 10080],
    winners: app_commands.Range[int, 1, 20] = 1,
    required_role: discord.Role = None,
    blocked_role: discord.Role = None
):
    await interaction.response.send_modal(
        GiveawayEmbedModal(
            prize=prize,
            duration_minutes=duration_minutes,
            winners_count=winners,
            host=interaction.user,
            required_role=required_role,
            blocked_role=blocked_role
        )
    )


async def resolve_giveaway_from_url(interaction: discord.Interaction, url: str):
    match = MESSAGE_LINK_RE.search(url)
    if not match:
        return None, None, "❌ That doesn't look like a valid message link. Right-click the giveaway message → **Copy Message Link**."

    guild_id, channel_id, message_id = map(int, match.groups())
    if guild_id != interaction.guild.id:
        return None, None, "❌ That message is from a different server."

    view = active_giveaways.get(message_id)
    if not view:
        return None, None, (
            "❌ I couldn't find that giveaway in memory. This happens if the bot restarted "
            "since it was created, or the link doesn't point to a giveaway message."
        )

    channel = interaction.guild.get_channel(channel_id)
    if channel is None:
        return None, None, "❌ I can't find that channel."

    try:
        message = await channel.fetch_message(message_id)
    except (discord.NotFound, discord.Forbidden):
        return None, None, "❌ Couldn't fetch that message."

    return view, message, None


@bot.tree.command(name="edit-giveaway", description="Edit an active giveaway (prize, winners, roles, time)")
@app_commands.describe(
    url="Link to the giveaway message",
    prize="New prize (optional)",
    winners="New number of winners (optional)",
    add_minutes="Add minutes to the remaining time — use a negative number to reduce it (optional)",
    required_role="Set the required role (optional)",
    blocked_role="Set the blocked role (optional)"
)
@app_commands.checks.has_permissions(manage_guild=True)
async def edit_giveaway(
    interaction: discord.Interaction,
    url: str,
    prize: str = None,
    winners: app_commands.Range[int, 1, 20] = None,
    add_minutes: int = None,
    required_role: discord.Role = None,
    blocked_role: discord.Role = None
):
    view, message, error = await resolve_giveaway_from_url(interaction, url)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    if view.ended:
        await interaction.response.send_message("❌ This giveaway has already ended — use `/giveaway-reroll` instead.", ephemeral=True)
        return

    if prize is None and winners is None and add_minutes is None and required_role is None and blocked_role is None:
        await interaction.response.send_message("⚠️ Provide at least one field to change.", ephemeral=True)
        return

    changes = []
    embed = message.embeds[0]

    if prize:
        view.prize = prize
        embed.description = format_giveaway_description(prize)
        changes.append(f"Prize → **{prize}**")

    if winners:
        view.winners_count = winners
        changes.append(f"Winners → {winners}")

    if add_minutes:
        view.ends_at += timedelta(minutes=add_minutes)
        view.schedule_end()
        action = "Added" if add_minutes > 0 else "Removed"
        changes.append(f"{action} {abs(add_minutes)} minute(s) — new end time updated")

    if required_role:
        view.required_role_id = required_role.id
        changes.append(f"Required role → {required_role.mention}")

    if blocked_role:
        view.blocked_role_id = blocked_role.id
        changes.append(f"Blocked role → {blocked_role.mention}")

    field_names = [f.name for f in embed.fields]
    for i, field in enumerate(embed.fields):
        if field.name == "Winners" and winners:
            embed.set_field_at(i, name="Winners", value=str(view.winners_count), inline=field.inline)
        elif field.name == "Time Remaining":
            embed.set_field_at(i, name="Time Remaining", value=discord.utils.format_dt(view.ends_at, "R"), inline=field.inline)
        elif field.name == "Required Role" and required_role:
            embed.set_field_at(i, name="Required Role", value=required_role.mention, inline=field.inline)
        elif field.name == "Blocked Role" and blocked_role:
            embed.set_field_at(i, name="Blocked Role", value=blocked_role.mention, inline=field.inline)

    if required_role and "Required Role" not in field_names:
        embed.add_field(name="Required Role", value=required_role.mention)
    if blocked_role and "Blocked Role" not in field_names:
        embed.add_field(name="Blocked Role", value=blocked_role.mention)

    await message.edit(embed=embed)
    await interaction.response.send_message("✅ Giveaway updated:\n" + "\n".join(changes))


@bot.tree.command(name="giveaway-reroll", description="Reroll the winner(s) of a giveaway")
@app_commands.describe(url="Link to the giveaway message")
@app_commands.checks.has_permissions(manage_guild=True)
async def giveaway_reroll(interaction: discord.Interaction, url: str):
    view, message, error = await resolve_giveaway_from_url(interaction, url)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    if not view.entrants:
        await interaction.response.send_message("❌ No participants to reroll from.", ephemeral=True)
        return

    winners_count = min(view.winners_count, len(view.entrants))
    winners = random.sample(list(view.entrants), winners_count)
    mentions = ", ".join(f"<@{w}>" for w in winners)
    embed = discord.Embed(
        title="🔁 Giveaway Rerolled",
        description=f"New winner(s) for **{view.prize}**: {mentions}",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed)


for cmd in [giveaway, edit_giveaway, giveaway_reroll]:
    async def _giveaway_err(interaction: discord.Interaction, error, _cmd=cmd):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        else:
            await interaction.response.send_message(f"⚠️ Error: {error}", ephemeral=True)
    cmd.error(_giveaway_err)


bot.run(TOKEN)
