import discord
import logging
import asyncio
from subsonic import Song, Album, Playlist, get_album_art_file

logger = logging.getLogger(__name__)

class SysMsg:
    @staticmethod
    async def msg(channel_or_interaction,
                  header: str,
                  message: str = None,
                  thumbnail: str = None,
                  *,
                  ephemeral: bool = False) -> None:
        embed = discord.Embed(color=discord.Color(0x50C470), title=header, description=message)
        file = discord.utils.MISSING
        if thumbnail:
            try:
                file = discord.File(thumbnail, filename="image.png")
                embed.set_thumbnail(url="attachment://image.png")
            except Exception as e:
                logger.error(f"Failed to attach thumbnail: {e}")

        # Fresh interaction → use response/followup (only for immediate command replies)
        if isinstance(channel_or_interaction, discord.Interaction) and not channel_or_interaction.is_expired():
            try:
                if not channel_or_interaction.response.is_done():
                    await channel_or_interaction.response.send_message(embed=embed, file=file, ephemeral=ephemeral)
                else:
                    await channel_or_interaction.followup.send(embed=embed, file=file, ephemeral=ephemeral)
                return
            except:
                pass  # fall through

        # Stored text channel → background announcements (Now Playing, etc.)
        if isinstance(channel_or_interaction, discord.TextChannel):
            try:
                await channel_or_interaction.send(embed=embed, file=file)
            except:
                pass

    # ───── Background announcements (called from player) ─────
    @staticmethod
    async def now_playing(channel: discord.TextChannel | None, song: Song) -> None:
        if not channel: return
        cover_art = await get_album_art_file(song.cover_id)
        desc = f"**{song.title}** - *{song.artist}*\n{song.album} ({song.duration_printable})"
        await __class__.msg(channel, "Now Playing:", desc, cover_art)

    @staticmethod
    async def playback_ended(channel: discord.TextChannel | None) -> None:
        if channel: await __class__.msg(channel, "Playback ended")

    @staticmethod
    async def skipping(channel: discord.TextChannel | None) -> None:
        if channel: await __class__.msg(channel, "Skipped track")

    # ───── Immediate command responses (interaction still valid) ─────
    @staticmethod
    async def added_to_queue(interaction: discord.Interaction, song: Song) -> None:
        desc = f"**{song.title}** - *{song.artist}*\n{song.album} ({song.duration_printable})"
        cover_art = await get_album_art_file(song.cover_id)
        await __class__.msg(interaction, f"{interaction.user.display_name} added track to queue", desc, cover_art)

    @staticmethod
    async def added_album_to_queue(interaction: discord.Interaction, album: Album) -> None:
        desc = f"**{album.name}** - *{album.artist}*\n{album.song_count} songs ({album.duration} seconds)"
        cover_art = await get_album_art_file(album.cover_id)
        await __class__.msg(interaction, f"{interaction.user.display_name} added album to queue", desc, cover_art)

    @staticmethod
    async def added_playlist_to_queue(interaction: discord.Interaction, playlist: Playlist) -> None:
        desc = f"**{playlist.name}**\n{playlist.song_count} songs ({playlist.duration} seconds)"
        cover_art = await get_album_art_file(playlist.cover_id)
        await __class__.msg(interaction, f"{interaction.user.display_name} added playlist to queue", desc, cover_art)

    @staticmethod
    async def added_discography_to_queue(interaction: discord.Interaction, artist: str, albums: list[Album]) -> None:
        desc = f"**{artist}** — {len(albums)} albums\n\n"
        cover_art = await get_album_art_file(albums[0].cover_id) if albums else None
        for i, album in enumerate(albums):
            desc += f"**{i+1}. {album.name}** — {album.song_count} songs\n"
        if len(desc) > 4000:
            desc = desc[:3990] + "..."
        await __class__.msg(interaction, f"{interaction.user.display_name} added discography to queue", desc, cover_art)

    @staticmethod
    async def queue_cleared(interaction: discord.Interaction) -> None:
        await __class__.msg(interaction, f"{interaction.user.display_name} cleared the queue")

    @staticmethod
    async def starting_queue_playback(interaction: discord.Interaction) -> None:
        await __class__.msg(interaction, "Started queue playback")

    @staticmethod
    async def stopping_queue_playback(interaction: discord.Interaction) -> None:
        await __class__.msg(interaction, "Stopped queue playback")


class ErrMsg:
    # Keeping your original ErrMsg untouched (it only uses fresh interactions, so it's fine)
    @staticmethod
    async def msg(interaction: discord.Interaction, message: str) -> None:
        if interaction is None or interaction.guild is None:
            logger.warning("Cannot send error message: interaction is no longer valid")
            return

        embed = discord.Embed(color=discord.Color(0x50C470), title="Error", description=message)
        attempt = 0
        while attempt < 3:
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    return
                else:
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                    return
            except discord.NotFound:
                logger.warning("Attempt %d at sending an error message failed (NotFound)...", attempt+1)
                attempt += 1
                await asyncio.sleep(0.5)
            except discord.HTTPException as e:
                logger.warning("Attempt %d at sending an error message failed (HTTPException: %s)...", attempt+1, e)
                attempt += 1
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error("Unexpected error when sending error message: %s", e)
                attempt += 1
                await asyncio.sleep(0.5)
        logger.error("Failed to send error message after %d attempts. Message: %s", attempt, message)

    @staticmethod
    async def user_not_in_voice_channel(interaction: discord.Interaction) -> None:
        await __class__.msg(interaction, "You are not connected to a voice channel.")
    @staticmethod
    async def bot_not_in_voice_channel(interaction: discord.Interaction) -> None:
        await __class__.msg(interaction, "Not currently connected to a voice channel.")
    @staticmethod
    async def cannot_connect_to_voice_channel(interaction: discord.Interaction) -> None:
        await __class__.msg(interaction, "Cannot connect to voice channel.")
    @staticmethod
    async def queue_is_empty(interaction: discord.Interaction) -> None:
        await __class__.msg(interaction, "Queue is empty.")
    @staticmethod
    async def already_playing(interaction: discord.Interaction) -> None:
        await __class__.msg(interaction, "Already playing.")
    @staticmethod
    async def not_playing(interaction: discord.Interaction) -> None:
        await __class__.msg(interaction, "No track is playing.")


# ───── Rest of your file (unchanged) ─────
def parse_search_as_track_selection_embed(results: list[Song], query: str, page_num: int) -> discord.Embed:
    options_str = ""
    for song in results:
        tr_title = song.title
        tr_artist = song.artist
        tr_album = (song.album[:68] + "...") if len(song.album) > 68 else song.album
        top_str_length = len(song.title + " - " + song.artist)
        if top_str_length > 71:
            if len(tr_title) > len(tr_artist):
                tr_title = song.title[:(68 - top_str_length)] + '...'
            else:
                tr_artist = song.artist[:(68 - top_str_length)] + '...'
        options_str += f"**{tr_title}** - *{tr_artist}* \n*{tr_album}* ({song.duration_printable})\n\n"
    options_str += f"Current page: {page_num}"
    return discord.Embed(color=discord.Color.orange(), title=f"Results for: {query}", description=options_str)

def parse_search_as_track_selection_options(results: list[Song]) -> list[discord.SelectOption]:
    select_options = []
    for i, song in enumerate(results):
        select_option = discord.SelectOption(label=f"{song.title}", description=f"by {song.artist}", value=i)
        select_options.append(select_option)
    return select_options
