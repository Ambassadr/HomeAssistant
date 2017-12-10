# @Author: Will Scott <willscott>
# @Date:   09/12/2017 09:01
# @Project: Ambassadr Home Automation
# @Last modified by:   willscott
# @Last modified time: 09/12/2017 09:02



"""
Support for interface with an LG webOS Smart TV.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/media_player.webostv/
"""
import logging
import asyncio
import datetime as dt
from urllib.parse import urlparse

import pytz
import voluptuous as vol

import homeassistant.util as util
from homeassistant.components.media_player import (
    SUPPORT_TURN_ON, SUPPORT_TURN_OFF, SUPPORT_PLAY,
    SUPPORT_NEXT_TRACK, SUPPORT_PAUSE, SUPPORT_PREVIOUS_TRACK,
    SUPPORT_VOLUME_MUTE, SUPPORT_VOLUME_STEP,
    SUPPORT_SELECT_SOURCE, SUPPORT_PLAY_MEDIA, MEDIA_TYPE_CHANNEL,
    MediaPlayerDevice, PLATFORM_SCHEMA)
from homeassistant.const import (
    CONF_HOST, CONF_CUSTOMIZE, CONF_TIMEOUT, STATE_OFF,
    STATE_PLAYING, STATE_PAUSED,
    STATE_UNKNOWN, CONF_NAME, CONF_FILENAME)
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.script import Script

REQUIREMENTS = ['pylgtv==0.1.7',
                'websockets==3.2',
                'wakeonlan==0.2.2']

_CONFIGURING = {}  # type: Dict[str, str]
_LOGGER = logging.getLogger(__name__)

CONF_SOURCES = 'sources'
CONF_ON_ACTION = 'turn_on_action'

DEFAULT_NAME = 'LG webOS Smart TV'

WEBOSTV_CONFIG_FILE = 'webostv.conf'

SUPPORT_WEBOSTV = SUPPORT_TURN_OFF | \
    SUPPORT_NEXT_TRACK | SUPPORT_PAUSE | SUPPORT_PREVIOUS_TRACK | \
    SUPPORT_VOLUME_MUTE | SUPPORT_VOLUME_STEP | \
    SUPPORT_SELECT_SOURCE | SUPPORT_PLAY_MEDIA | SUPPORT_PLAY

MIN_TIME_BETWEEN_SCANS = dt.timedelta(seconds=10)
MIN_TIME_BETWEEN_FORCED_SCANS = dt.timedelta(seconds=1)

CUSTOMIZE_SCHEMA = vol.Schema({
    vol.Optional(CONF_SOURCES):
        vol.All(cv.ensure_list, [cv.string]),
})

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Optional(CONF_HOST): cv.string,
    vol.Optional(CONF_CUSTOMIZE, default={}): CUSTOMIZE_SCHEMA,
    vol.Optional(CONF_FILENAME, default=WEBOSTV_CONFIG_FILE): cv.string,
    vol.Optional(CONF_TIMEOUT, default=8): cv.positive_int,
    vol.Optional(CONF_ON_ACTION): cv.SCRIPT_SCHEMA,
})


# pylint: disable=unused-argument
def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up the LG WebOS TV platform."""
    if discovery_info is not None:
        host = urlparse(discovery_info[1]).hostname
    else:
        host = config.get(CONF_HOST)

    if host is None:
        _LOGGER.error("No TV found in configuration file or with discovery")
        return False

    # Only act if we are not already configuring this host
    if host in _CONFIGURING:
        return

    name = config.get(CONF_NAME)
    customize = config.get(CONF_CUSTOMIZE)
    timeout = config.get(CONF_TIMEOUT)
    turn_on_action = config.get(CONF_ON_ACTION)

    config = hass.config.path(config.get(CONF_FILENAME))

    setup_tv(host, name, customize, config, timeout, hass,
             add_devices, turn_on_action)


def setup_tv(host, name, customize, config, timeout, hass,
             add_devices, turn_on_action):
    """Set up a LG WebOS TV based on host parameter."""
    from pylgtv import WebOsClient
    from pylgtv import PyLGTVPairException
    from websockets.exceptions import ConnectionClosed

    client = WebOsClient(host, config, timeout)

    if not client.is_registered():
        if host in _CONFIGURING:
            # Try to pair.
            try:
                client.register()
            except PyLGTVPairException:
                _LOGGER.warning(
                    "Connected to LG webOS TV %s but not paired", host)
                return
            except (OSError, ConnectionClosed, asyncio.TimeoutError):
                _LOGGER.error("Unable to connect to host %s", host)
                return
        else:
            # Not registered, request configuration.
            _LOGGER.warning("LG webOS TV %s needs to be paired", host)
            request_configuration(
                host, name, customize, config, timeout, hass,
                add_devices, turn_on_action)
            return

    # If we came here and configuring this host, mark as done.
    if client.is_registered() and host in _CONFIGURING:
        request_id = _CONFIGURING.pop(host)
        configurator = hass.components.configurator
        configurator.request_done(request_id)

    add_devices([LgWebOSDevice(host, name, customize, config, timeout,
                               hass, turn_on_action)], True)


def request_configuration(
        host, name, customize, config, timeout, hass,
        add_devices, turn_on_action):
    """Request configuration steps from the user."""
    configurator = hass.components.configurator

    # We got an error if this method is called while we are configuring
    if host in _CONFIGURING:
        configurator.notify_errors(
            _CONFIGURING[host], 'Failed to pair, please try again.')
        return

    # pylint: disable=unused-argument
    def lgtv_configuration_callback(data):
        """The actions to do when our configuration callback is called."""
        setup_tv(host, name, customize, config, timeout, hass,
                 add_devices, turn_on_action)

    _CONFIGURING[host] = configurator.request_config(
        name, lgtv_configuration_callback,
        description='Click start and accept the pairing request on your TV.',
        description_image='/static/images/config_webos.png',
        submit_caption='Start pairing request'
    )


class LgWebOSDevice(MediaPlayerDevice):
    """Representation of a LG WebOS TV."""

    def __init__(self, host, name, customize, config, timeout,
                 hass, on_action):
        """Initialize the webos device."""
        from pylgtv import WebOsClient
        self._client = WebOsClient(host, config, timeout)
        self._on_script = Script(hass, on_action) if on_action else None
        self._customize = customize

        self._name = name
        # Assume that the TV is not muted
        self._muted = False
        # Assume that the TV is in Play mode
        self._playing = True
        self._volume = 0
        self._current_source = None
        self._current_source_id = None
        self._current_channel_name = None
        self._current_channel_id = None
        self._state = STATE_UNKNOWN
        self._source_list = {}
        self._app_list = {}
        self._now_playing = {}

    @util.Throttle(MIN_TIME_BETWEEN_SCANS, MIN_TIME_BETWEEN_FORCED_SCANS)
    def update(self):
        """Retrieve the latest data."""
        from websockets.exceptions import ConnectionClosed
        try:
            current_input = self._client.get_input()
            if current_input is not None:
                self._current_source_id = current_input
                if self._state in (STATE_UNKNOWN, STATE_OFF):
                    self._state = STATE_PLAYING
            else:
                self._state = STATE_OFF
                self._current_source = None
                self._current_source_id = None

            if self._state is not STATE_OFF:
                self._muted = self._client.get_muted()
                self._volume = self._client.get_volume()

                self._source_list = {}
                self._app_list = {}
                self._current_channel_name = None
                self._current_channel_id = None
                self._now_playing = {}
                conf_sources = self._customize.get(CONF_SOURCES, [])

                info = self._client.get_channel_info()
                if info.get('returnValue', False):
                    self.update_channels(info)
                for app in self._client.get_apps():
                    self._app_list[app['id']] = app
                    if app['id'] == self._current_source_id:
                        self._current_source = app['title']
                        self._source_list[app['title']] = app
                    elif (not conf_sources or
                          app['id'] in conf_sources or
                          any(word in app['title']
                              for word in conf_sources) or
                          any(word in app['id']
                              for word in conf_sources)):
                        self._source_list[app['title']] = app

                for source in self._client.get_inputs():
                    if source['id'] == self._current_source_id:
                        self._current_source = source['label']
                        self._source_list[source['label']] = source
                    elif (not conf_sources or
                          source['label'] in conf_sources or
                          any(source['label'].find(word) != -1
                              for word in conf_sources)):
                        self._source_list[source['label']] = source
        except (OSError, ConnectionClosed, TypeError,
                asyncio.TimeoutError):
            self._state = STATE_OFF
            self._current_source = None
            self._current_source_id = None

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def state(self):
        """Return the state of the device."""
        return self._state

    @property
    def is_volume_muted(self):
        """Boolean if volume is currently muted."""
        return self._muted

    @property
    def volume_level(self):
        """Volume level of the media player (0..1)."""
        return self._volume / 100.0

    @property
    def source(self):
        """Return the current input source."""
        return self._current_source

    @property
    def source_list(self):
        """List of available input sources."""
        return sorted(self._source_list.keys())

    @property
    def media_content_type(self):
        """Content type of current playing media."""
        return MEDIA_TYPE_CHANNEL

    @property
    def media_content_id(self):
        """Content ID of current playing media."""
        return self._current_channel_id

    @property
    def media_image_url(self):
        """Image url of current playing media."""
        if self._current_source_id in self._app_list:
            icon = self._app_list[self._current_source_id]['largeIcon']
            if not icon.startswith('http'):
                icon = self._app_list[self._current_source_id]['icon']
            return icon
        return None

    @property
    def supported_features(self):
        """Flag media player features that are supported."""
        if self._on_script:
            return SUPPORT_WEBOSTV | SUPPORT_TURN_ON
        return SUPPORT_WEBOSTV

    @property
    def media_channel(self):
        """Channel currently playing."""
        return self._current_channel_name

    @property
    def app_id(self):
        """ID of the current running app."""
        return self._current_source_id

    @property
    def app_name(self):
        """Name of the current running app."""
        return self._current_source

    @property
    def media_title(self):
        """Title of current playing media."""
        if self.media_channel:
            return "{}: {}".format(self.media_channel,
                                   self._now_playing.get('programName'))

    @property
    def media_position(self):
        """Position of current playing media in seconds."""
        if not self.media_title:
            return None
        start_time = pytz.utc.localize(dt.datetime.strptime(
            self._now_playing.get('startTime'), '%Y,%m,%d,%H,%M,%S'))
        return (dt.datetime.now(pytz.UTC) - start_time).total_seconds()

    @property
    def media_position_updated_at(self):
        """When was the position of the current playing media valid."""
        return util.dt.now()

    @property
    def media_duration(self):
        """Duration of current playing media in seconds."""
        return self._now_playing.get('duration')

    def turn_off(self):
        """Turn off media player."""
        from websockets.exceptions import ConnectionClosed
        self._state = STATE_OFF
        try:
            self._client.power_off()
        except (OSError, ConnectionClosed, TypeError,
                asyncio.TimeoutError):
            pass

    def turn_on(self):
        """Turn on the media player."""
        if self._on_script:
            self._on_script.run()

    def volume_up(self):
        """Volume up the media player."""
        self._client.volume_up()

    def volume_down(self):
        """Volume down media player."""
        self._client.volume_down()

    def set_volume_level(self, volume):
        """Set volume level, range 0..1."""
        tv_volume = volume * 100
        self._client.set_volume(tv_volume)

    def mute_volume(self, mute):
        """Send mute command."""
        self._muted = mute
        self._client.set_mute(mute)

    def media_play_pause(self):
        """Simulate play pause media player."""
        if self._playing:
            self.media_pause()
        else:
            self.media_play()

    def select_source(self, source):
        """Select input source."""
        if self._source_list.get(source).get('title'):
            self._current_source_id = self._source_list[source]['id']
            self._current_source = self._source_list[source]['title']
            self._client.launch_app(self._source_list[source]['id'])
        elif self._source_list.get(source).get('label'):
            self._current_source_id = self._source_list[source]['id']
            self._current_source = self._source_list[source]['label']
            self._client.set_input(self._source_list[source]['id'])

    def media_play(self):
        """Send play command."""
        self._playing = True
        self._state = STATE_PLAYING
        self._client.play()

    def media_pause(self):
        """Send media pause command to media player."""
        self._playing = False
        self._state = STATE_PAUSED
        self._client.pause()

    def media_next_track(self):
        """Send next track command."""
        self._client.fast_forward()

    def media_previous_track(self):
        """Send the previous track command."""
        self._client.rewind()

    def play_media(self, media_type, media_id, **kwargs):
        """Play a piece of media."""
        self._playing = True
        self._state = STATE_PLAYING
        if media_type != MEDIA_TYPE_CHANNEL:
            self._client.play()
        else:
            self._client.set_channel(media_id)
            self.update_channels()

    def update_channels(self, info=None):
        """Update channels info."""
        info = info or self._client.get_channel_info()
        self._current_channel_name = info.get('channel', {}) \
            .get('channelName')
        self._current_channel_id = info.get('channel', {}) \
            .get('channelId')
        for program in info.get('programList', []):
            start_time = pytz.utc.localize(dt.datetime.strptime(
                program.get('startTime'), '%Y,%m,%d,%H,%M,%S'))
            end_time = pytz.utc.localize(dt.datetime.strptime(
                program.get('endTime'), '%Y,%m,%d,%H,%M,%S'))
            if start_time <= dt.datetime.now(pytz.UTC) < end_time:
                self._now_playing = program