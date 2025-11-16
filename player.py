''' A player object that handles playback and data for its respective guild '''
import asyncio
import discord
import data
import ui
import logging
from subsonic import Song, APIError, get_random_songs, get_similar_songs, stream
from discord.errors import ConnectionClosed

logger = logging.getLogger(__name__)

# Default player data
_default_data: dict[str, any] = {
    "current-song": None,
    "current-position": 0,
    "queue": [],
}

class Player:
    ''' Class that represents an audio player '''
    def __init__(self) -> None:
        self._data = _default_data.copy()
        self._player_loop = None

    @property
    def current_song(self) -> Song:
        return self._data["current-song"]

    @current_song.setter
    def current_song(self, song: Song) -> None:
        self._data["current-song"] = song

    @property
    def current_position(self) -> int:
        return self._data["current-position"]

    @current_position.setter
    def current_position(self, position: int) -> None:
        self._data["current-position"] = position

    @property
    def queue(self) -> list[Song]:
        return self._data["queue"]

    @queue.setter
    def queue(self, value: list) -> None:
        self._data["queue"] = value

    @property
    def player_loop(self) -> asyncio.AbstractEventLoop:
        return self._player_loop

    @player_loop.setter
    def player_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._player_loop = loop

    # ──────────────────────────────────────────────────────────────
    # ROBUST VOICE JOIN (NEW) – Fixes handshake timeout
    # ──────────────────────────────────────────────────────────────
    async def join_voice(self, channel: discord.VoiceChannel) -> discord.VoiceClient:
        """Join voice with retry on handshake failure (1006/4006)."""
        max_retries = 5
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"[Voice] Joining {channel.name} (attempt {attempt})")
                vc = await channel.connect(timeout=20.0, reconnect=True, self_deaf=True)
                await asyncio.wait_for(vc.ws.wait(), timeout=15.0)
                logger.info(f"[Voice] Connected to {channel.name}")
                return vc
            except asyncio.TimeoutError:
                logger.warning(f"[Voice] Handshake timeout (attempt {attempt})")
            except ConnectionClosed as e:
                if e.code in (1006, 4006):
                    logger.warning(f"[Voice] Session dropped ({e.code}) — retrying...")
                else:
                    logger.error(f"[Voice] Closed: {e.code} {e.reason}")
                    raise
            except Exception as e:
                logger.error(f"[Voice] Join error: {e}")
                raise

            if attempt < max_retries:
                await asyncio.sleep(min(2 ** attempt, 10))

        raise RuntimeError("Failed to join voice after retries")

    # ──────────────────────────────────────────────────────────────
    # ORIGINAL: stream_track (with reconnection fix)
    # ──────────────────────────────────────────────────────────────
    async def stream_track(self, interaction: discord.Interaction, song: Song, voice_client: discord.VoiceClient) -> None:
        if voice_client is None:
            await ui.ErrMsg.bot_not_in_voice_channel(interaction)
            return

        if not voice_client.is_connected():
            logger.error("Voice client is not connected")
            await ui.ErrMsg.msg(interaction, "Voice connection was lost. Please try again.")
            return

        if voice_client.is_playing():
            await ui.ErrMsg.already_playing(interaction)
            return

        ffmpeg_options = {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "options": "-filter:a volume=replaygain=track"
        }

        try:
            stream_url = await stream(song.song_id)
            if not stream_url:
                logger.error("Failed to get stream URL")
                await ui.ErrMsg.msg(interaction, "Failed to get audio stream. Please try again.")
                return

            audio_src = discord.FFmpegOpusAudio(stream_url, **ffmpeg_options)
        except APIError as err:
            logger.error(f"API Error streaming song, Code {err.errorcode}: {err.message}")
            await ui.ErrMsg.msg(interaction, f"API error while streaming song: {err.message}")
            return
        except Exception as e:
            logger.error(f"Unexpected error getting audio stream: {e}")
            await ui.ErrMsg.msg(interaction, "An error occurred while preparing the audio. Please try again.")
            return

        loop = asyncio.get_event_loop()
        self.player_loop = loop

        async def playback_finished(error):
            if error:
                logger.error(f"Playback error: {error}")
                if "Not connected to voice" in str(error) and interaction.user.voice:
                    try:
                        new_vc = await self.join_voice(interaction.user.voice.channel)
                        future = asyncio.run_coroutine_threadsafe(
                            self.play_audio_queue(interaction, new_vc), loop
                        )
                    except Exception as e:
                        logger.error(f"Reconnect failed: {e}")
                return

            logger.debug("Playback finished.")
            if voice_client and voice_client.is_connected():
                future = asyncio.run_coroutine_threadsafe(
                    self.play_audio_queue(interaction, voice_client), loop
                )
                future.add_done_callback(
                    lambda f: logger.error(f"Queue error: {f.exception()}") if f.exception() else None
                )

        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                if not voice_client.is_connected():
                    await ui.ErrMsg.msg(interaction, "Voice connection lost before playing.")
                    return
                voice_client.play(audio_src, after=lambda e: loop.create_task(playback_finished(e)))
                logger.info(f"Started playing: {song.title} by {song.artist}")
                return
            except discord.ClientException as e:
                logger.error(f"Play failed (attempt {attempt+1}): {e}")
                if attempt == max_attempts - 1:
                    await ui.ErrMsg.msg(interaction, "Failed to play after retries.")
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Play error: {e}")
                await ui.ErrMsg.msg(interaction, "Playback error.")
                return

    # ──────────────────────────────────────────────────────────────
    # ORIGINAL: handle_autoplay, play_audio_queue, skip_track (unchanged)
    # ──────────────────────────────────────────────────────────────
    async def handle_autoplay(self, interaction: discord.Interaction, prev_song_id: str = None) -> bool:
        autoplay_mode = data.guild_properties(interaction.guild_id).autoplay_mode
        queue = data.guild_data(interaction.guild_id).player.queue
        logger.debug("Handling autoplay...")
        if queue or autoplay_mode is data.AutoplayMode.NONE:
            return False
        if prev_song_id is None:
            autoplay_mode = data.AutoplayMode.RANDOM
        songs = []
        try:
            if autoplay_mode == data.AutoplayMode.RANDOM:
                songs = await get_random_songs(size=1)
            elif autoplay_mode == data.AutoplayMode.SIMILAR:
                songs = await get_similar_songs(song_id=prev_song_id, count=1)
        except APIError as err:
            logger.error(f"Autoplay API error: {err}")
        if not songs:
            await ui.ErrMsg.msg(interaction, "Failed to get autoplay song.")
            return False
        self.queue.append(songs[0])
        return True

    async def play_audio_queue(self, interaction: discord.Interaction, voice_client: discord.VoiceClient) -> None:
        if voice_client is None:
            await ui.ErrMsg.bot_not_in_voice_channel(interaction)
            return
        if voice_client.is_playing():
            return
        if self.queue:
            song = self.queue.pop(0)
            self.current_song = song
            await ui.SysMsg.now_playing(interaction, song)
            await self.stream_track(interaction, song, voice_client)
        else:
            prev_song_id = self.current_song.song_id if self.current_song else None
            self.current_song = None
            if await self.handle_autoplay(interaction, prev_song_id):
                await self.play_audio_queue(interaction, voice_client)
                return
            await ui.SysMsg.playback_ended(interaction)

    async def skip_track(self, interaction: discord.Interaction, voice_client: discord.VoiceClient) -> None:
        if voice_client is None:
            await ui.ErrMsg.bot_not_in_voice_channel(interaction)
            return
        if voice_client.is_playing():
            voice_client.stop()
            await ui.SysMsg.skipping(interaction)
        else:
            await ui.ErrMsg.not_playing(interaction)
