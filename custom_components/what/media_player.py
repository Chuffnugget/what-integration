"""Control Windows output volume and the current Windows media session."""
from homeassistant.components.media_player import MediaPlayerEntity, MediaPlayerEntityFeature as Feature, MediaPlayerState
from .control_entity import WindowsControlEntity

# Serialize actions within this platform; commands are never replayed.
PARALLEL_UPDATES = 1


async def async_setup_entry(hass, entry, async_add_entities):
    from .announcements import AnnouncementPlayer
    async_add_entities([WindowsMediaPlayer(entry.runtime_data), AnnouncementPlayer(entry.runtime_data)])


class WindowsMediaPlayer(WindowsControlEntity, MediaPlayerEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator, "windows_media_control", "Windows media", "media")

    @property
    def values(self):
        return self.controls.get("values", {})
    @property
    def state(self):
        return {
            "Playing": MediaPlayerState.PLAYING,
            "Paused": MediaPlayerState.PAUSED,
            "Changing": MediaPlayerState.BUFFERING,
        }.get(
            self.values.get("win_media_state"),
            MediaPlayerState.IDLE,
        )
    @property
    def supported_features(self):
        features = Feature.VOLUME_SET | Feature.VOLUME_MUTE | Feature.VOLUME_STEP
        for name, feature in {
            "play": Feature.PLAY,
            "pause": Feature.PAUSE,
            "stop": Feature.STOP,
            "next": Feature.NEXT_TRACK,
            "previous": Feature.PREVIOUS_TRACK,
            "seek": Feature.SEEK,
            "shuffle": Feature.SHUFFLE_SET,
            "repeat": Feature.REPEAT_SET,
        }.items():
            if self.values.get("win_media_can_" + name) is True:
                features |= feature
        return features
    @property
    def volume_level(self):
        value = self.values.get("win_audio_volume")
        return max(0, min(1, value / 100)) if type(value) in (float, int) else None
    @property
    def is_volume_muted(self):
        return self.values.get("win_audio_muted")
    @property
    def media_title(self):
        return self.values.get("win_media_title")
    @property
    def media_artist(self):
        return self.values.get("win_media_artist")
    @property
    def app_id(self):
        return self.values.get("win_media_app")
    @property
    def media_position(self):
        return self.values.get("win_media_position")
    @property
    def media_duration(self):
        return self.values.get("win_media_duration")
    @property
    def media_position_updated_at(self):
        return getattr(self.coordinator, "media_updated_at", None)
    @property
    def shuffle(self):
        return self.values.get("win_media_shuffle")
    @property
    def repeat(self):
        return self.values.get("win_media_repeat")
    async def async_set_volume_level(self, volume):
        await self.command("volume", volume)
    async def async_mute_volume(self, mute):
        await self.command("mute", mute)
    async def async_volume_up(self):
        await self.command("volume_up")
    async def async_volume_down(self):
        await self.command("volume_down")
    async def async_media_play(self):
        await self.command("play")
    async def async_media_pause(self):
        await self.command("pause")
    async def async_media_stop(self):
        await self.command("stop")
    async def async_media_next_track(self):
        await self.command("next")
    async def async_media_previous_track(self):
        await self.command("previous")
    async def async_media_seek(self, position):
        await self.command("seek", position)
    async def async_set_shuffle(self, shuffle):
        await self.command("shuffle", shuffle)
    async def async_set_repeat(self, repeat):
        await self.command("repeat", repeat)
