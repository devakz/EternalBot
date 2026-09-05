import os
import json
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", "0"))
STAFF_ROLE_ID = int(os.getenv("STAFF_ROLE_ID", "0"))

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

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
    bot.add_view(EmbedView())


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
    duration = discord.utils.utcnow() + discord.timedelta(minutes=minutes)
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
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"🧹 {len(deleted)} messages deleted.", ephemeral=True)


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
            pass  # position adjustment failing is not critical

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
        await interaction.response.send_message(f"{member.mention} has no warnings.", ephemeral=True)
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


for cmd in [kick, ban, unban, timeout, remove_timeout, clear, nuke, lock, unlock, slowmode, warn, remove_warning]:
    async def _err(interaction: discord.Interaction, error, _cmd=cmd):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        else:
            await interaction.response.send_message(f"⚠️ Error: {error}", ephemeral=True)
    cmd.error(_err)


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
        name="🎫 Tickets",
        value=(
            "`/ticket-panel` — post the ticket panel\n"
            "`/edit-ticket` — edit the current ticket's embed (form)\n"
            "`/add-member` · `/remove-member` — manage ticket access\n"
            "Buttons inside a ticket: **Close Ticket**, **Claim Ticket**"
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
        value="`/embed` — opens a form to build an embed (title, description, color, image, thumbnail). Every embed gets an **Edit** button.",
        inline=False
    )
    embed.set_footer(text="Type / in the chat to see each command's parameters")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ===========================================================
# MODERN EMBED BUILDER (MODAL-BASED)
# ===========================================================

class EmbedModal(discord.ui.Modal):
    def __init__(self, existing_embed: discord.Embed = None):
        super().__init__(title="Edit Embed" if existing_embed else "Create Embed")
        self.editing = existing_embed is not None

        current_color = f"#{existing_embed.color.value:06X}" if existing_embed and existing_embed.color else None

        self.title_input = discord.ui.TextInput(
            label="Title", required=False, max_length=256,
            default=existing_embed.title if existing_embed else None
        )
        self.description_input = discord.ui.TextInput(
            label="Description", style=discord.TextStyle.paragraph, required=False, max_length=4000,
            default=existing_embed.description if existing_embed else None
        )
        self.color_input = discord.ui.TextInput(
            label="Color (hex, e.g. #5865F2)", required=False, max_length=7,
            default=current_color
        )
        self.image_input = discord.ui.TextInput(
            label="Image URL", required=False,
            default=existing_embed.image.url if existing_embed and existing_embed.image else None
        )
        self.thumbnail_input = discord.ui.TextInput(
            label="Thumbnail URL", required=False,
            default=existing_embed.thumbnail.url if existing_embed and existing_embed.thumbnail else None
        )

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

        if self.editing and interaction.message is not None:
            await interaction.response.edit_message(embed=embed, view=EmbedView())
        else:
            await interaction.response.send_message(embed=embed, view=EmbedView())


class EmbedView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Edit", emoji="✏️", style=discord.ButtonStyle.secondary, custom_id="embed_edit_button")
    async def edit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        existing = interaction.message.embeds[0] if interaction.message.embeds else None
        await interaction.response.send_modal(EmbedModal(existing_embed=existing))


@bot.tree.command(name="embed", description="Create a custom embed using an interactive form")
async def embed_command(interaction: discord.Interaction):
    await interaction.response.send_modal(EmbedModal())


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


class TicketEditModal(discord.ui.Modal, title="Edit Ticket Embed"):
    def __init__(self, target_message: discord.Message):
        super().__init__()
        self.target_message = target_message
        existing = target_message.embeds[0] if target_message.embeds else None
        current_color = f"#{existing.color.value:06X}" if existing and existing.color else None

        self.title_input = discord.ui.TextInput(label="Title", required=False, default=existing.title if existing else None)
        self.description_input = discord.ui.TextInput(
            label="Description", style=discord.TextStyle.paragraph, required=False,
            default=existing.description if existing else None
        )
        self.color_input = discord.ui.TextInput(label="Color (hex)", required=False, default=current_color)

        self.add_item(self.title_input)
        self.add_item(self.description_input)
        self.add_item(self.color_input)

    async def on_submit(self, interaction: discord.Interaction):
        embed = self.target_message.embeds[0] if self.target_message.embeds else discord.Embed()
        if self.title_input.value:
            embed.title = self.title_input.value
        if self.description_input.value:
            embed.description = self.description_input.value
        if self.color_input.value:
            embed.color = parse_color(self.color_input.value, fallback=embed.color or discord.Color.blurple())

        await self.target_message.edit(embed=embed)
        await interaction.response.send_message("✅ Ticket embed updated.", ephemeral=True)


@bot.tree.command(name="edit-ticket", description="Edit the current ticket's embed using a form")
async def edit_ticket(interaction: discord.Interaction):
    if not is_ticket_channel(interaction.channel):
        await interaction.response.send_message("❌ This command only works inside a ticket channel.", ephemeral=True)
        return
    if not is_staff_member(interaction.user):
        await interaction.response.send_message("❌ Only staff can edit the ticket.", ephemeral=True)
        return

    target_message = None
    async for msg in interaction.channel.history(limit=50, oldest_first=True):
        if msg.author == bot.user and msg.embeds:
            target_message = msg
            break

    if not target_message:
        await interaction.response.send_message("❌ Couldn't find the ticket embed in this channel.", ephemeral=True)
        return

    await interaction.response.send_modal(TicketEditModal(target_message))


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


bot.run(TOKEN)
