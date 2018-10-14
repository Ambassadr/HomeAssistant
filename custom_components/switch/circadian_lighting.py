"""
Circadian Lighting Switch for Home-Assistant.
"""

DEPENDENCIES = ['circadian_lighting', 'light']

import logging

from custom_components.circadian_lighting import DOMAIN, CIRCADIAN_LIGHTING_UPDATE_TOPIC, DATA_CIRCADIAN_LIGHTING

import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.dispatcher import dispatcher_connect
from homeassistant.helpers.event import track_state_change
from homeassistant.helpers.restore_state import async_get_last_state
from homeassistant.components.light import (
    is_on, ATTR_BRIGHTNESS, ATTR_COLOR_TEMP, ATTR_RGB_COLOR, ATTR_TRANSITION,
    ATTR_WHITE_VALUE, ATTR_XY_COLOR, DOMAIN as LIGHT_DOMAIN, VALID_TRANSITION)
from homeassistant.components.switch import SwitchDevice
from homeassistant.const import ( ATTR_ENTITY_ID, CONF_NAME, CONF_PLATFORM, CONF_LIGHTS, CONF_MODE, SERVICE_TURN_ON)
from homeassistant.util import slugify
from homeassistant.util.color import (
    color_RGB_to_xy, color_temperature_kelvin_to_mired, color_temperature_to_rgb)

_LOGGER = logging.getLogger(__name__)

ICON = 'mdi:theme-light-dark'

CONF_LIGHTS_CT = 'lights_ct'
CONF_LIGHTS_RGB = 'lights_rgb'
CONF_LIGHTS_XY = 'lights_xy'
CONF_LIGHTS_BRIGHT = 'lights_brightness'
CONF_DISABLE_BRIGHTNESS_ADJUST = 'disable_brightness_adjust'
CONF_MIN_BRIGHT = 'min_brightness'
DEFAULT_MIN_BRIGHT = 1
CONF_MAX_BRIGHT = 'max_brightness'
DEFAULT_MAX_BRIGHT = 100
CONF_SLEEP_ENTITY = 'sleep_entity'
CONF_SLEEP_STATE = 'sleep_state'
CONF_SLEEP_CT = 'sleep_colortemp'
CONF_SLEEP_BRIGHT = 'sleep_brightness'
CONF_DISABLE_ENTITY = 'disable_entity'
CONF_DISABLE_STATE = 'disable_state'

PLATFORM_SCHEMA = vol.Schema({
    vol.Required(CONF_PLATFORM): 'circadian_lighting',
    vol.Optional(CONF_NAME, default="Circadian Lighting"): cv.string,
    vol.Optional(CONF_LIGHTS_CT): cv.entity_ids,
    vol.Optional(CONF_LIGHTS_RGB): cv.entity_ids,
    vol.Optional(CONF_LIGHTS_XY): cv.entity_ids,
    vol.Optional(CONF_LIGHTS_BRIGHT): cv.entity_ids,
    vol.Optional(CONF_DISABLE_BRIGHTNESS_ADJUST, default=False): cv.boolean,
    vol.Optional(CONF_MIN_BRIGHT, default=DEFAULT_MIN_BRIGHT):
        vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
    vol.Optional(CONF_MAX_BRIGHT, default=DEFAULT_MAX_BRIGHT):
        vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
    vol.Optional(CONF_SLEEP_ENTITY): cv.entity_id,
    vol.Optional(CONF_SLEEP_STATE): cv.string,
    vol.Optional(CONF_SLEEP_CT):
        vol.All(vol.Coerce(int), vol.Range(min=1000, max=10000)),
    vol.Optional(CONF_SLEEP_BRIGHT):
        vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
    vol.Optional(CONF_DISABLE_ENTITY): cv.entity_id,
    vol.Optional(CONF_DISABLE_STATE): cv.string
})

def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up the Circadian Lighting switches."""
    cl = hass.data.get(DATA_CIRCADIAN_LIGHTING)
    if cl:
        lights_ct = config.get(CONF_LIGHTS_CT)
        lights_rgb = config.get(CONF_LIGHTS_RGB)
        lights_xy = config.get(CONF_LIGHTS_XY)
        lights_brightness = config.get(CONF_LIGHTS_BRIGHT)
        disable_brightness_adjust = config.get(CONF_DISABLE_BRIGHTNESS_ADJUST)
        name = config.get(CONF_NAME)
        min_brightness = config.get(CONF_MIN_BRIGHT)
        max_brightness = config.get(CONF_MAX_BRIGHT)
        sleep_entity = config.get(CONF_SLEEP_ENTITY)
        sleep_state = config.get(CONF_SLEEP_STATE)
        sleep_colortemp = config.get(CONF_SLEEP_CT)
        sleep_brightness = config.get(CONF_SLEEP_BRIGHT)
        disable_entity = config.get(CONF_DISABLE_ENTITY)
        disable_state = config.get(CONF_DISABLE_STATE)
        cs = CircadianSwitch(hass, cl, name, lights_ct, lights_rgb, lights_xy, lights_brightness,
                                disable_brightness_adjust, min_brightness, max_brightness,
                                sleep_entity, sleep_state, sleep_colortemp, sleep_brightness,
                                disable_entity, disable_state)
        add_devices([cs])

        def update(call=None):
            """Update lights."""
            cs.update_switch()
        return True
    else:
        return False


class CircadianSwitch(SwitchDevice):
    """Representation of a Circadian Lighting switch."""

    def __init__(self, hass, cl, name, lights_ct, lights_rgb, lights_xy, lights_brightness,
                    disable_brightness_adjust, min_brightness, max_brightness,
                    sleep_entity, sleep_state, sleep_colortemp, sleep_brightness,
                    disable_entity, disable_state):
        """Initialize the Circadian Lighting switch."""
        self.hass = hass
        self._cl = cl
        self._name = name
        self._entity_id = "switch." + slugify("{} {}".format('circadian_lighting', name))
        self._state = None
        self._icon = ICON
        self._hs_color = self._cl.data['hs_color']
        self._attributes = {}
        self._attributes['lights_ct'] = lights_ct
        self._attributes['lights_rgb'] = lights_rgb
        self._attributes['lights_xy'] = lights_xy
        self._attributes['lights_brightness'] = lights_brightness
        self._attributes['disable_brightness_adjust'] = disable_brightness_adjust
        self._attributes['min_brightness'] = min_brightness
        self._attributes['max_brightness'] = max_brightness
        self._attributes['sleep_entity'] = sleep_entity
        self._attributes['sleep_state'] = sleep_state
        self._attributes['sleep_colortemp'] = sleep_colortemp
        self._attributes['sleep_brightness'] = sleep_brightness
        self._attributes['disable_entity'] = disable_entity
        self._attributes['disable_state'] = disable_state
        self._attributes['hs_color'] = self._hs_color
        self._attributes['brightness'] = self.calc_brightness()

        self._lights = []
        if lights_ct != None:
            self._lights += lights_ct
        if lights_rgb != None:
            self._lights += lights_rgb
        if lights_xy != None:
            self._lights += lights_xy
        if lights_brightness != None:
            self._lights += lights_brightness

        """Register callbacks."""
        dispatcher_connect(hass, CIRCADIAN_LIGHTING_UPDATE_TOPIC, self.update_switch)
        track_state_change(hass, self._lights, self.light_state_changed)
        if self._attributes['sleep_entity'] is not None:
            track_state_change(hass, self._attributes['sleep_entity'], self.sleep_state_changed)

    @property
    def entity_id(self):
        """Return the entity ID of the switch."""
        return self._entity_id

    @property
    def name(self):
        """Return the name of the device if any."""
        return self._name

    @property
    def is_on(self):
        """Return true if circadian lighting is on."""
        return self._state

    async def async_added_to_hass(self):
        """Call when entity about to be added to hass."""
        # If not None, we got an initial value.
        if self._state is not None:
            return

        state = await async_get_last_state(self.hass, self._entity_id)
        self._state = state and state.state == STATE_ON

    @property
    def icon(self):
        """Icon to use in the frontend, if any."""
        return self._icon

    @property
    def hs_color(self):
        return self._hs_color

    @property
    def device_state_attributes(self):
        """Return the attributes of the switch."""
        return self._attributes

    def turn_on(self, **kwargs):
        """Turn on circadian lighting."""
        self._state = True

        # Make initial update
        self.update_switch()

        self.schedule_update_ha_state()

    def turn_off(self, **kwargs):
        """Turn off circadian lighting."""
        self._state = False
        self.schedule_update_ha_state()
        self._hs_color = None
        self._attributes['hs_color'] = self._hs_color

    def calc_ct(self):
        if self._attributes['sleep_entity'] is not None and self.hass.states.get(self._attributes['sleep_entity']).state == self._attributes['sleep_state']:
            _LOGGER.debug(self._name + " in Sleep mode")
            return color_temperature_kelvin_to_mired(self._attributes['sleep_colortemp'])
        else:
            return color_temperature_kelvin_to_mired(self._cl.data['colortemp'])

    def calc_rgb(self):
        if self._attributes['sleep_entity'] is not None and self.hass.states.get(self._attributes['sleep_entity']).state == self._attributes['sleep_state']:
            _LOGGER.debug(self._name + " in Sleep mode")
            return color_temperature_to_rgb(self._attributes['sleep_colortemp'])
        else:
            return color_temperature_to_rgb(self._cl.data['colortemp'])

    def calc_xy(self):
        return color_RGB_to_xy(*self.calc_rgb())

    def calc_brightness(self):
        if self._attributes['disable_brightness_adjust'] is True:
            return None
        else:
            if self._attributes['sleep_entity'] is not None and self.hass.states.get(self._attributes['sleep_entity']).state == self._attributes['sleep_state']:
                _LOGGER.debug(self._name + " in Sleep mode")
                return self._attributes['sleep_brightness']
            else:
                if self._cl.data['percent'] > 0:
                    return self._attributes['max_brightness']
                else:
                    return ((self._attributes['max_brightness'] - self._attributes['min_brightness']) * ((100+self._cl.data['percent']) / 100)) + self._attributes['min_brightness']

    def update_switch(self):
        if self._cl.data is not None:
            self._hs_color = self._cl.data['hs_color']
            self._attributes['hs_color'] = self._hs_color
            self._attributes['brightness'] = self.calc_brightness()
            _LOGGER.debug(self._name + " Switch Updated")

        self.adjust_lights(self._lights)

    def should_adjust(self):
        if self._state is not True:
            _LOGGER.debug(self._name + " off - not adjusting")
            return False
        elif self._cl.data is None:
            _LOGGER.debug(self._name + " could not retrieve Circadian Lighting data")
            return False
        elif self._attributes['disable_entity'] is not None and self.hass.states.get(self._attributes['disable_entity']).state == self._attributes['disable_state']:
            _LOGGER.debug(self._name + " disabled by " + str(self._attributes['disable_entity']))
            return False
        else:
            return True

    def adjust_lights(self, lights, transition=None):
        if self.should_adjust():
            if transition == None:
                transition = self._cl.data['transition']

            brightness = (self._attributes['brightness'] / 100) * 255 if self._attributes['brightness'] is not None else None

            for light in lights:
                """Set color of array of ct light."""
                if self._attributes['lights_ct'] is not None and light in self._attributes['lights_ct']:
                    mired = int(self.calc_ct())
                    if is_on(self.hass, light):
                        service_data = {ATTR_ENTITY_ID: light}
                        service_data[ATTR_COLOR_TEMP] = int(mired)
                        service_data[ATTR_BRIGHTNESS] = brightness
                        service_data[ATTR_TRANSITION] = transition
                        self.hass.services.call(
                            LIGHT_DOMAIN, SERVICE_TURN_ON, service_data)
                        _LOGGER.debug(light + " CT Adjusted - color_temp: " + str(mired) + ", brightness: " + str(brightness) + ", transition: " + str(transition))

                """Set color of array of rgb light."""
                if self._attributes['lights_rgb'] is not None and light in self._attributes['lights_rgb']:
                    rgb = self.calc_rgb()
                    if is_on(self.hass, light):
                        service_data = {ATTR_ENTITY_ID: light}
                        service_data[ATTR_RGB_COLOR] = rgb
                        service_data[ATTR_BRIGHTNESS] = brightness
                        service_data[ATTR_TRANSITION] = transition
                        self.hass.services.call(
                            LIGHT_DOMAIN, SERVICE_TURN_ON, service_data)
                        _LOGGER.debug(light + " RGB Adjusted - rgb_color: " + str(rgb) + ", brightness: " + str(brightness) + ", transition: " + str(transition))

                """Set color of array of xy light."""
                if self._attributes['lights_xy'] is not None and light in self._attributes['lights_xy']:
                    x_val, y_val = self.calc_xy()
                    if is_on(self.hass, light):
                        service_data = {ATTR_ENTITY_ID: light}
                        service_data[ATTR_XY_COLOR] = [x_val, y_val]
                        service_data[ATTR_BRIGHTNESS] = brightness
                        service_data[ATTR_WHITE_VALUE] = brightness
                        service_data[ATTR_TRANSITION] = transition
                        self.hass.services.call(
                            LIGHT_DOMAIN, SERVICE_TURN_ON, service_data)
                        _LOGGER.debug(light + " XY Adjusted - xy_color: [" + str(x_val) + ", " + str(y_val) + "], brightness: " + str(brightness) + ", transition: " + str(transition) + ", white_value: " + str(brightness))

                """Set color of array of brightness light."""
                if self._attributes['lights_brightness'] is not None and light in self._attributes['lights_brightness']:
                    if is_on(self.hass, light):
                        service_data = {ATTR_ENTITY_ID: light}
                        service_data[ATTR_BRIGHTNESS] = brightness
                        service_data[ATTR_TRANSITION] = transition
                        self.hass.services.call(
                            LIGHT_DOMAIN, SERVICE_TURN_ON, service_data)
                        _LOGGER.debug(light + " Brightness Adjusted - brightness: " + str(brightness) + ", transition: " + str(transition))

    def light_state_changed(self, entity_id, from_state, to_state):
        self.adjust_lights([entity_id], 1)

    def sleep_state_changed(self, entity_id, from_state, to_state):
        if to_state.state == self._attributes['sleep_state']:
            self.adjust_lights(self._lights, 1)