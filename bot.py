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
# ARMAZENAMENTO SIMPLES (JSON) — PARA WARNS
# ---------------------------------------------------------

WARNS_FILE = "warns.json"


def carregar_warns():
    if not os.path.exists(WARNS_FILE):
        return {}
    with open(WARNS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def salvar_warns(dados):
    with open(WARNS_FILE, "w", encoding="utf-8") as f:
        json.dump(dados, f, indent=2, ensure_ascii=False)


def is_staff_member(user: discord.Member) -> bool:
    if user.guild_permissions.manage_guild:
        return True
    if STAFF_ROLE_ID and any(r.id == STAFF_ROLE_ID for r in getattr(user, "roles", [])):
        return True
    return False


@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Sincronizados {len(synced)} comandos.")
    except Exception as e:
        print(f"Erro ao sincronizar comandos: {e}")
    print(f"Bot online como {bot.user}")


@bot.event
async def on_connect():
    bot.add_view(TicketOpenView())
    bot.add_view(TicketCloseView())


# ===========================================================
# MODERAÇÃO
# ===========================================================

@bot.tree.command(name="kick", description="Expulsa um membro do servidor")
@app_commands.describe(membro="Membro a ser expulso", motivo="Motivo da expulsão")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, membro: discord.Member, motivo: str = "Não especificado"):
    await membro.kick(reason=motivo)
    embed = discord.Embed(title="👢 Membro Expulso", description=f"**{membro}** foi expulso do servidor.", color=discord.Color.orange())
    embed.add_field(name="Motivo", value=motivo)
    embed.add_field(name="Responsável", value=interaction.user.mention)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="ban", description="Bane um membro do servidor")
@app_commands.describe(membro="Membro a ser banido", motivo="Motivo do banimento")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, membro: discord.Member, motivo: str = "Não especificado"):
    await membro.ban(reason=motivo)
    embed = discord.Embed(title="🔨 Membro Banido", description=f"**{membro}** foi banido do servidor.", color=discord.Color.red())
    embed.add_field(name="Motivo", value=motivo)
    embed.add_field(name="Responsável", value=interaction.user.mention)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="unban", description="Remove o banimento de um usuário (use o ID)")
@app_commands.describe(user_id="ID do usuário a desbanir")
@app_commands.checks.has_permissions(ban_members=True)
async def unban(interaction: discord.Interaction, user_id: str):
    try:
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user)
        embed = discord.Embed(title="✅ Usuário Desbanido", description=f"**{user}** foi desbanido.", color=discord.Color.green())
        await interaction.response.send_message(embed=embed)
    except (ValueError, discord.NotFound):
        await interaction.response.send_message("❌ ID inválido ou usuário não está banido.", ephemeral=True)


@bot.tree.command(name="timeout", description="Silencia um membro por um tempo (em minutos)")
@app_commands.describe(membro="Membro a silenciar", minutos="Duração em minutos", motivo="Motivo")
@app_commands.checks.has_permissions(moderate_members=True)
async def timeout(interaction: discord.Interaction, membro: discord.Member, minutos: int, motivo: str = "Não especificado"):
    duracao = discord.utils.utcnow() + discord.timedelta(minutes=minutos)
    await membro.timeout(duracao, reason=motivo)
    embed = discord.Embed(title="🔇 Membro Silenciado", description=f"**{membro}** foi silenciado por {minutos} minuto(s).", color=discord.Color.gold())
    embed.add_field(name="Motivo", value=motivo)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="remover-timeout", description="Remove o silenciamento de um membro")
@app_commands.describe(membro="Membro a liberar")
@app_commands.checks.has_permissions(moderate_members=True)
async def remover_timeout(interaction: discord.Interaction, membro: discord.Member):
    await membro.timeout(None)
    await interaction.response.send_message(f"🔊 **{membro}** não está mais silenciado.")


@bot.tree.command(name="limpar", description="Apaga uma quantidade de mensagens do canal")
@app_commands.describe(quantidade="Número de mensagens a apagar (máx 100)")
@app_commands.checks.has_permissions(manage_messages=True)
async def limpar(interaction: discord.Interaction, quantidade: app_commands.Range[int, 1, 100]):
    await interaction.response.defer(ephemeral=True)
    apagadas = await interaction.channel.purge(limit=quantidade)
    await interaction.followup.send(f"🧹 {len(apagadas)} mensagens apagadas.", ephemeral=True)


@bot.tree.command(name="nuke", description="Limpa completamente o canal atual (recria do zero)")
@app_commands.checks.has_permissions(manage_channels=True)
async def nuke(interaction: discord.Interaction):
    canal = interaction.channel
    await interaction.response.send_message("💣 Reiniciando o canal...", ephemeral=True)
    novo_canal = await canal.clone(reason=f"Nuke por {interaction.user}")
    await novo_canal.edit(position=canal.position)
    await canal.delete()
    embed = discord.Embed(
        title="💥 Canal Limpo",
        description=f"Este canal foi reiniciado por {interaction.user.mention}.",
        color=discord.Color.dark_red()
    )
    await novo_canal.send(embed=embed)


@bot.tree.command(name="lock", description="Bloqueia o canal (ninguém pode enviar mensagens)")
@app_commands.checks.has_permissions(manage_channels=True)
async def lock(interaction: discord.Interaction):
    await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=False)
    embed = discord.Embed(description="🔒 Canal bloqueado.", color=discord.Color.red())
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="unlock", description="Desbloqueia o canal")
@app_commands.checks.has_permissions(manage_channels=True)
async def unlock(interaction: discord.Interaction):
    await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=True)
    embed = discord.Embed(description="🔓 Canal desbloqueado.", color=discord.Color.green())
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="slowmode", description="Define o modo lento do canal (em segundos, 0 desativa)")
@app_commands.describe(segundos="Tempo em segundos entre mensagens (0-21600)")
@app_commands.checks.has_permissions(manage_channels=True)
async def slowmode(interaction: discord.Interaction, segundos: app_commands.Range[int, 0, 21600]):
    await interaction.channel.edit(slowmode_delay=segundos)
    if segundos == 0:
        await interaction.response.send_message("🐇 Modo lento desativado.")
    else:
        await interaction.response.send_message(f"🐢 Modo lento definido para {segundos} segundos.")


@bot.tree.command(name="warn", description="Aplica uma advertência a um membro")
@app_commands.describe(membro="Membro a advertir", motivo="Motivo da advertência")
@app_commands.checks.has_permissions(moderate_members=True)
async def warn(interaction: discord.Interaction, membro: discord.Member, motivo: str):
    dados = carregar_warns()
    guild_id = str(interaction.guild.id)
    user_id = str(membro.id)
    dados.setdefault(guild_id, {}).setdefault(user_id, [])
    dados[guild_id][user_id].append({
        "motivo": motivo,
        "moderador": str(interaction.user),
        "data": discord.utils.utcnow().strftime("%d/%m/%Y %H:%M")
    })
    salvar_warns(dados)
    total = len(dados[guild_id][user_id])
    embed = discord.Embed(title="⚠️ Advertência Aplicada", color=discord.Color.yellow())
    embed.add_field(name="Membro", value=membro.mention)
    embed.add_field(name="Motivo", value=motivo)
    embed.add_field(name="Total de advertências", value=str(total))
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="warns", description="Lista as advertências de um membro")
@app_commands.describe(membro="Membro a consultar")
async def warns_cmd(interaction: discord.Interaction, membro: discord.Member):
    dados = carregar_warns()
    lista = dados.get(str(interaction.guild.id), {}).get(str(membro.id), [])
    if not lista:
        await interaction.response.send_message(f"{membro.mention} não tem advertências.", ephemeral=True)
        return
    embed = discord.Embed(title=f"⚠️ Advertências de {membro}", color=discord.Color.yellow())
    for i, w in enumerate(lista, start=1):
        embed.add_field(name=f"#{i} — {w['data']}", value=f"Motivo: {w['motivo']}\nPor: {w['moderador']}", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="remover-warn", description="Remove uma advertência específica de um membro (pelo número)")
@app_commands.describe(membro="Membro", numero="Número da advertência (veja em /warns)")
@app_commands.checks.has_permissions(moderate_members=True)
async def remover_warn(interaction: discord.Interaction, membro: discord.Member, numero: int):
    dados = carregar_warns()
    guild_id = str(interaction.guild.id)
    user_id = str(membro.id)
    lista = dados.get(guild_id, {}).get(user_id, [])
    if numero < 1 or numero > len(lista):
        await interaction.response.send_message("❌ Número de advertência inválido.", ephemeral=True)
        return
    removida = lista.pop(numero - 1)
    salvar_warns(dados)
    await interaction.response.send_message(f"✅ Advertência removida: {removida['motivo']}")


# Erros de permissão para os comandos de moderação
for cmd in [kick, ban, unban, timeout, remover_timeout, limpar, nuke, lock, unlock, slowmode, warn, remover_warn]:
    async def _err(interaction: discord.Interaction, error, _cmd=cmd):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ Você não tem permissão para usar este comando.", ephemeral=True)
        else:
            await interaction.response.send_message(f"⚠️ Erro: {error}", ephemeral=True)
    cmd.error(_err)


# ===========================================================
# UTILIDADE
# ===========================================================

@bot.tree.command(name="ping", description="Mostra a latência do bot")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 Pong! {round(bot.latency * 1000)}ms")


@bot.tree.command(name="avatar", description="Mostra o avatar de um usuário")
@app_commands.describe(membro="Membro (deixe vazio para ver o seu)")
async def avatar(interaction: discord.Interaction, membro: discord.Member = None):
    membro = membro or interaction.user
    embed = discord.Embed(title=f"Avatar de {membro}", color=discord.Color.blurple())
    embed.set_image(url=membro.display_avatar.url)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="userinfo", description="Mostra informações de um membro")
@app_commands.describe(membro="Membro (deixe vazio para ver o seu)")
async def userinfo(interaction: discord.Interaction, membro: discord.Member = None):
    membro = membro or interaction.user
    embed = discord.Embed(title=f"Informações de {membro}", color=membro.color)
    embed.set_thumbnail(url=membro.display_avatar.url)
    embed.add_field(name="ID", value=membro.id)
    embed.add_field(name="Entrou no servidor", value=discord.utils.format_dt(membro.joined_at, "R"))
    embed.add_field(name="Conta criada", value=discord.utils.format_dt(membro.created_at, "R"))
    cargos = [r.mention for r in membro.roles if r.name != "@everyone"]
    embed.add_field(name=f"Cargos ({len(cargos)})", value=" ".join(cargos) if cargos else "Nenhum", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="serverinfo", description="Mostra informações do servidor")
async def serverinfo(interaction: discord.Interaction):
    guild = interaction.guild
    embed = discord.Embed(title=guild.name, color=discord.Color.blurple())
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name="Dono", value=str(guild.owner))
    embed.add_field(name="Membros", value=guild.member_count)
    embed.add_field(name="Criado em", value=discord.utils.format_dt(guild.created_at, "R"))
    embed.add_field(name="Canais de texto", value=len(guild.text_channels))
    embed.add_field(name="Canais de voz", value=len(guild.voice_channels))
    embed.add_field(name="Cargos", value=len(guild.roles))
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="say", description="Faz o bot enviar uma mensagem no canal (staff)")
@app_commands.describe(mensagem="Texto a ser enviado")
@app_commands.checks.has_permissions(manage_messages=True)
async def say(interaction: discord.Interaction, mensagem: str):
    await interaction.channel.send(mensagem)
    await interaction.response.send_message("✅ Enviado.", ephemeral=True)


@bot.tree.command(name="ajuda", description="Lista todos os comandos do bot")
async def ajuda(interaction: discord.Interaction):
    embed = discord.Embed(title="📖 Central de Ajuda", description="Lista completa de comandos disponíveis.", color=discord.Color.blurple())
    embed.add_field(
        name="🛡️ Moderação",
        value=(
            "`/kick` `/ban` `/unban` `/timeout` `/remover-timeout`\n"
            "`/limpar` `/nuke` `/lock` `/unlock` `/slowmode`\n"
            "`/warn` `/warns` `/remover-warn`"
        ),
        inline=False
    )
    embed.add_field(
        name="🎫 Tickets",
        value=(
            "`/painel-ticket` — posta o painel de abertura\n"
            "`/editar-ticket` — edita o embed do ticket atual\n"
            "`/add-membro` `/remover-membro` — gerencia acesso ao ticket"
        ),
        inline=False
    )
    embed.add_field(
        name="🧰 Utilidade",
        value="`/ping` `/avatar` `/userinfo` `/serverinfo` `/say` `/embed`",
        inline=False
    )
    embed.set_footer(text="Use / no chat para ver a descrição de cada comando")
    await interaction.response.send_message(embed=embed)


# ===========================================================
# EMBEDS
# ===========================================================

@bot.tree.command(name="embed", description="Cria um embed personalizado")
@app_commands.describe(titulo="Título do embed", descricao="Texto do embed", cor="Cor em hexadecimal (ex: #5865F2)")
async def embed_command(interaction: discord.Interaction, titulo: str, descricao: str, cor: str = "#5865F2"):
    try:
        cor_int = int(cor.replace("#", ""), 16)
    except ValueError:
        cor_int = 0x5865F2
    embed = discord.Embed(title=titulo, description=descricao, color=cor_int)
    embed.set_footer(text=f"Criado por {interaction.user}", icon_url=interaction.user.display_avatar.url)
    await interaction.response.send_message(embed=embed)


# ===========================================================
# SISTEMA DE TICKETS
# ===========================================================

def canal_e_ticket(canal: discord.TextChannel) -> bool:
    return bool(canal.topic and canal.topic.startswith("Ticket de"))


class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Fechar Ticket", style=discord.ButtonStyle.danger, custom_id="fechar_ticket")
    async def fechar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Fechando o ticket em 5 segundos...")
        await interaction.channel.edit(name=f"fechado-{interaction.channel.name}")
        await interaction.channel.set_permissions(interaction.guild.default_role, view_channel=False)
        await asyncio.sleep(5)
        await interaction.channel.delete()

    @discord.ui.button(label="Assumir Ticket", style=discord.ButtonStyle.success, custom_id="assumir_ticket")
    async def assumir(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff_member(interaction.user):
            await interaction.response.send_message("❌ Apenas a equipe pode assumir tickets.", ephemeral=True)
            return
        mensagem = interaction.message
        if mensagem.embeds:
            embed = mensagem.embeds[0]
            embed.add_field(name="Atendido por", value=interaction.user.mention, inline=False)
            await mensagem.edit(embed=embed)
        await interaction.response.send_message(f"✅ {interaction.user.mention} assumiu este ticket.")


class TicketOpenView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎫 Abrir Ticket", style=discord.ButtonStyle.primary, custom_id="abrir_ticket")
    async def abrir(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        autor = interaction.user

        nome_canal = f"ticket-{autor.name}".lower().replace(" ", "-")
        existente = discord.utils.get(guild.text_channels, name=nome_canal)
        if existente:
            await interaction.response.send_message(f"Você já tem um ticket aberto: {existente.mention}", ephemeral=True)
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            autor: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }

        if STAFF_ROLE_ID:
            staff_role = guild.get_role(STAFF_ROLE_ID)
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        categoria = guild.get_channel(TICKET_CATEGORY_ID) if TICKET_CATEGORY_ID else None

        canal = await guild.create_text_channel(
            name=nome_canal,
            overwrites=overwrites,
            category=categoria,
            topic=f"Ticket de {autor.id}"
        )

        embed = discord.Embed(
            title="🎫 Ticket Aberto",
            description=f"Olá {autor.mention}! Descreva seu problema e a equipe vai te atender em breve.",
            color=discord.Color.blurple()
        )
        await canal.send(embed=embed, view=TicketCloseView())
        await interaction.response.send_message(f"✅ Ticket criado: {canal.mention}", ephemeral=True)


@bot.tree.command(name="painel-ticket", description="Envia o painel para abertura de tickets")
@app_commands.checks.has_permissions(manage_channels=True)
async def painel_ticket(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📩 Suporte",
        description="Clique no botão abaixo para abrir um ticket de atendimento.",
        color=discord.Color.blurple()
    )
    await interaction.response.send_message(embed=embed, view=TicketOpenView())


@bot.tree.command(name="editar-ticket", description="Edita o embed do ticket atual (use dentro do canal do ticket)")
@app_commands.describe(titulo="Novo título (deixe vazio para manter)", descricao="Nova descrição (deixe vazio para manter)", cor="Nova cor em hexadecimal")
async def editar_ticket(interaction: discord.Interaction, titulo: str = None, descricao: str = None, cor: str = None):
    if not canal_e_ticket(interaction.channel):
        await interaction.response.send_message("❌ Este comando só funciona dentro de um canal de ticket.", ephemeral=True)
        return
    if not is_staff_member(interaction.user):
        await interaction.response.send_message("❌ Apenas a equipe pode editar o ticket.", ephemeral=True)
        return

    mensagem_alvo = None
    async for msg in interaction.channel.history(limit=50, oldest_first=True):
        if msg.author == bot.user and msg.embeds:
            mensagem_alvo = msg
            break

    if not mensagem_alvo:
        await interaction.response.send_message("❌ Não encontrei o embed do ticket neste canal.", ephemeral=True)
        return

    embed = mensagem_alvo.embeds[0]
    if titulo:
        embed.title = titulo
    if descricao:
        embed.description = descricao
    if cor:
        try:
            embed.color = discord.Color(int(cor.replace("#", ""), 16))
        except ValueError:
            pass

    await mensagem_alvo.edit(embed=embed)
    await interaction.response.send_message("✅ Embed do ticket atualizado.", ephemeral=True)


@bot.tree.command(name="add-membro", description="Adiciona um membro ao ticket atual")
@app_commands.describe(membro="Membro a adicionar")
async def add_membro(interaction: discord.Interaction, membro: discord.Member):
    if not canal_e_ticket(interaction.channel):
        await interaction.response.send_message("❌ Use este comando dentro de um canal de ticket.", ephemeral=True)
        return
    if not is_staff_member(interaction.user):
        await interaction.response.send_message("❌ Apenas a equipe pode adicionar membros.", ephemeral=True)
        return
    await interaction.channel.set_permissions(membro, view_channel=True, send_messages=True, read_message_history=True)
    await interaction.response.send_message(f"✅ {membro.mention} foi adicionado ao ticket.")


@bot.tree.command(name="remover-membro", description="Remove um membro do ticket atual")
@app_commands.describe(membro="Membro a remover")
async def remover_membro(interaction: discord.Interaction, membro: discord.Member):
    if not canal_e_ticket(interaction.channel):
        await interaction.response.send_message("❌ Use este comando dentro de um canal de ticket.", ephemeral=True)
        return
    if not is_staff_member(interaction.user):
        await interaction.response.send_message("❌ Apenas a equipe pode remover membros.", ephemeral=True)
        return
    await interaction.channel.set_permissions(membro, overwrite=None)
    await interaction.response.send_message(f"✅ {membro.mention} foi removido do ticket.")


bot.run(TOKEN)
