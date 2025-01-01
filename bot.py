import discord
from discord.ext import commands
from discord import app_commands
import json
import os
from datetime import datetime

# Load config.json for settings
with open("config.json", "r") as config_file:
    config = json.load(config_file)

# Config values
GUILD_ID = int(config["guild_id"])  # The ID of your Discord server
TICKET_CATEGORY_ID = int(config["ticket_category"])  # The ID of the category where ticket channels will be created
TRANSCRIPT_CHANNEL_ID = int(config["transcript_channel"])  # The ID of the transcript log channel

# Intents setup
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.dm_messages = True
intents.message_content = True

# Global ticket counter
if os.path.exists("ticket_counter.json"):
    with open("ticket_counter.json", "r") as counter_file:
        ticket_counter = json.load(counter_file)
else:
    ticket_counter = {"ticket_number": 0}

# Bot setup
class ModmailBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.ticket_map = {}  # Tracks ticket channels and their associated users
        self.claimed_tickets = {}  # Tracks which staff member claimed a ticket
        self.conversation_logs = {}  # Tracks conversation logs for each ticket
        self.active_prompts = set()  # Tracks users currently interacting with the bot

    async def setup_hook(self):
        # Sync commands with the specific guild
        guild = discord.Object(id=GUILD_ID)
        await self.tree.sync(guild=guild)
        print(f"Slash commands synced to guild {GUILD_ID}.")

bot = ModmailBot()

# Function to format timestamps
def format_timestamp():
    return datetime.now().strftime("%m/%d/%y, %I:%M %p")

# Ticket Actions for Staff
class TicketActionsView(discord.ui.View):
    def __init__(self, channel_id, user):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.user = user

    @discord.ui.button(label="🙋‍♂️Claim", style=discord.ButtonStyle.green)
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.channel_id in bot.claimed_tickets:
            await interaction.response.send_message(
                f"This ticket has already been claimed by {bot.claimed_tickets[self.channel_id].mention}.",
                ephemeral=True
            )
            return

        # Mark the ticket as claimed
        bot.claimed_tickets[self.channel_id] = interaction.user

        # Notify the user in their DM
        try:
            embed = discord.Embed(
                title="👤 Staff member assigned",
                description=f"The staff member **{interaction.user.name}** has claimed this ticket.",
                color=discord.Color.blue()
            )
            embed.set_footer(text="Owned and operated by Digital Piano Community")
            embed.timestamp = discord.utils.utcnow()

            await self.user.send(embed=embed)
        except discord.Forbidden:
            pass

        # Notify the ticket channel
        await interaction.channel.send(f"**{interaction.user.name}** has claimed this ticket.")
        await interaction.response.send_message(f"Ticket claimed successfully.", ephemeral=True)

    @discord.ui.button(label="🔒Close", style=discord.ButtonStyle.red)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.channel.id not in bot.ticket_map:
            await interaction.response.send_message("❌ This channel is not a ticket channel.", ephemeral=True)
            return

        user_id = bot.ticket_map[interaction.channel.id]
        user = await bot.fetch_user(user_id)

        # Increment ticket counter
        global ticket_counter
        ticket_counter["ticket_number"] += 1
        ticket_number = ticket_counter["ticket_number"]
        with open("ticket_counter.json", "w") as counter_file:
            json.dump(ticket_counter, counter_file)

        # Generate the log
        log_file_path = f"Modmail-Thread#{ticket_number}.txt"
        with open(log_file_path, "w") as log_file:
            log_file.write(f"Modmail-Thread #{ticket_number} started with {user} ({user.id}) at {format_timestamp()}\n")
            log_file.write("\n".join(bot.conversation_logs.get(interaction.channel.id, [])))

        # Send the log to the transcript channel
        guild = bot.get_guild(GUILD_ID)
        transcript_channel = guild.get_channel(TRANSCRIPT_CHANNEL_ID)
        if transcript_channel:
            with open(log_file_path, "rb") as log_file:
                await transcript_channel.send(
                    embed=discord.Embed(
                        title=f"📄 Transcript for Modmail-Thread #{ticket_number}",
                        description=f"Transcript for ticket channel: **{interaction.channel.name}**",
                        color=discord.Color.purple()
                    ),
                    file=discord.File(log_file, filename=f"Modmail-Thread#{ticket_number}.txt")
                )

        # Send the log to the user
        try:
            with open(log_file_path, "rb") as log_file:
                await user.send(
                    embed=discord.Embed(
                        title=f"📄 Transcript for Modmail-Thread #{ticket_number}",
                        description="Here is the transcript of your ticket conversation. Thank you!",
                        color=discord.Color.green()
                    ),
                    file=discord.File(log_file, filename=f"Modmail-Thread#{ticket_number}.txt")
                )
        except discord.Forbidden:
            pass

        # Notify the user the ticket is closed
        try:
            embed = discord.Embed(
                title="🔒 Ticket Closed",
                description=(f"Thank you for reaching out to us. Your request has been resolved.\n\n"
                             f"**Warning:** Answering this message will open a new support request.\n\n"
                             f"**📄 Ticket Log:** You can find a log of your ticket attached."),
                color=discord.Color.red()
            )
            embed.set_footer(text="Owned and operated by Digital Piano Community")
            embed.timestamp = discord.utils.utcnow()
            await user.send(embed=embed)
        except discord.Forbidden:
            pass

        # Clean up logs
        os.remove(log_file_path)
        del bot.conversation_logs[interaction.channel.id]

        # Delete the ticket mapping and the channel
        del bot.ticket_map[interaction.channel.id]
        bot.claimed_tickets.pop(interaction.channel.id, None)  # Remove claim record
        await interaction.channel.delete(reason="Ticket closed by staff.")

# Topic Selection Menu
class TopicSelectionView(discord.ui.View):
    def __init__(self, user: discord.User):
        super().__init__(timeout=None)  # No timeout for the menu
        self.user = user
        self.topic_select = discord.ui.Select(
            placeholder="Select your topic...",
            options=[
                discord.SelectOption(label="In-Game Report", description="Report an issue with the game.", emoji="🎮"),
                discord.SelectOption(label="Bug Report", description="Report a bug or glitch.", emoji="🐞"),
                discord.SelectOption(label="Other", description="Other", emoji="📨"),
            ],
        )
        self.topic_select.callback = self.select_callback
        self.add_item(self.topic_select)

    async def select_callback(self, interaction: discord.Interaction):
        # Prevent duplicate tickets
        if self.user.id in bot.ticket_map.values():
            await interaction.response.send_message(
                "❌ You already have an open ticket.", ephemeral=True
            )
            return

        # Disable the dropdown to prevent further interaction
        for item in self.children:
            if isinstance(item, discord.ui.Select):
                item.disabled = True

        await interaction.response.edit_message(view=self)

        # Increment the ticket counter
        global ticket_counter
        ticket_counter["ticket_number"] += 1
        ticket_number = ticket_counter["ticket_number"]
        with open("ticket_counter.json", "w") as counter_file:
            json.dump(ticket_counter, counter_file)

        # Create a ticket channel under the specified category
        guild = interaction.client.get_guild(GUILD_ID)
        if not guild:
            print("Guild not found!")
            return

        ticket_category = guild.get_channel(TICKET_CATEGORY_ID)
        if not ticket_category or not isinstance(ticket_category, discord.CategoryChannel):
            print("Ticket category not found or invalid!")
            return

        # Create the channel with permissions for the user and staff
        ticket_channel = await ticket_category.create_text_channel(
            name=f"ModMail-Thread#{ticket_number}",
            overwrites={
                guild.default_role: discord.PermissionOverwrite(read_messages=False),  # Deny access to everyone
                self.user: discord.PermissionOverwrite(read_messages=True, send_messages=False),  # Grant access to the user
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),  # Allow bot access
            },
            reason=f"Ticket created for {self.user} with topic: {self.topic_select.values[0]}"
        )

        # Map the ticket channel to the user
        bot.ticket_map[ticket_channel.id] = self.user.id
        bot.conversation_logs[ticket_channel.id] = [f"[{format_timestamp()}] Topic: {self.topic_select.values[0]}"]

        # Create the embed
        embed = discord.Embed(
            title="📨 Ticket Created",
            description=(
                f"Thank you for taking the time to open a ticket. Your request is important to us.\n\n"
                f"**Topic:** {self.topic_select.values[0]}\n"
                "A staff member will assist you shortly. To ensure a smooth resolution process, please make sure to "
                "have all relevant proof and information readily available to share with our team. We appreciate your cooperation and patience."
            ),
            color=discord.Color.green()
        )
        embed.set_footer(text="Owned and operated by Digital Piano Community")
        embed.timestamp = discord.utils.utcnow()

        # Notify the user in their DM
        await self.user.send(embed=embed)

        # Add buttons for staff in the ticket channel
        await ticket_channel.send(
            embed=discord.Embed(
                description=(
                    f"{self.user.mention} has created a ticket.\n\n"
                    f"**Topic:** {self.topic_select.values[0]}\n"
                    "Claim the ticket to be able to respond."
                ),
                color=discord.Color.orange()
            ),
            view=TicketActionsView(ticket_channel.id, self.user)
        )

@bot.event
async def on_ready():
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.watching, name="DM for in-game support")
    )
    print(f"Bot is online as {bot.user}")

@bot.event
async def on_message(message):
    # Ignore bot messages
    if message.author.bot:
        return

    # Handle DMs
    if message.guild is None:
        if message.author.id in bot.active_prompts:
            return

        # Check if a ticket already exists
        for channel_id, user_id in bot.ticket_map.items():
            if user_id == message.author.id:
                # Forward the user's message to the ticket channel
                guild = bot.get_guild(GUILD_ID)
                ticket_channel = guild.get_channel(channel_id)
                if ticket_channel:
                    log_entry = f"[{format_timestamp()}] [USER] {message.author.name}: {message.content}"
                    bot.conversation_logs[channel_id].append(log_entry)

                    # Forward attachments
                    files = [await attachment.to_file() for attachment in message.attachments if attachment.size <= 8 * 1024 * 1024]
                    if len(files) < len(message.attachments):
                        await message.channel.send("Some files were too large and could not be forwarded.")
                    await ticket_channel.send(content=f"{message.author.name}: {message.content}", files=files)
                    await message.add_reaction("✅")
                return

        # Send the initial response with the dropdown menu
        bot.active_prompts.add(message.author.id)
        embed = discord.Embed(
            title="📨 Ticket Support",
            description="Thank you for reaching out! Please select your topic below to get started.",
            color=discord.Color.blue()
        )
        view = TopicSelectionView(user=message.author)
        await message.channel.send(embed=embed, view=view)
        bot.active_prompts.remove(message.author.id)

    # Handle ticket channel messages from staff
    elif message.guild.id == GUILD_ID and message.channel.id in bot.ticket_map:
        # Check if the ticket is claimed
        if message.channel.id not in bot.claimed_tickets:
            await message.channel.send("❌ Please claim this ticket before responding.")
            return

        user_id = bot.ticket_map[message.channel.id]
        user = await bot.fetch_user(user_id)
        if not user:
            await message.channel.send("❌ Error: Could not fetch the user.")
            return

        # Forward the moderator's message to the user's DM
        try:
            log_entry = f"[{format_timestamp()}] [STAFF] {message.author.name}: {message.content}"
            bot.conversation_logs[message.channel.id].append(log_entry)

            # Forward attachments
            files = [await attachment.to_file() for attachment in message.attachments if attachment.size <= 8 * 1024 * 1024]
            if len(files) < len(message.attachments):
                await message.channel.send("Some files were too large and could not be forwarded.")
            await user.send(content=message.content, files=files)
            await message.add_reaction("✅")
        except discord.Forbidden:
            await message.channel.send("❌ Could not send the reply. The user may have DMs disabled.")

# Run the bot
bot.run(config["bot_token"])
