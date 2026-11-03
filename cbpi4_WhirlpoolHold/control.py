"""Pure helpers for the whirlpool hold step and the HLT PID.

CraftBeerPi imports the plugin package, which pulls in cbpi. These helpers stay
free of that dependency so the regulation rules can be tested on their own.
"""

from __future__ import annotations

import time

TIMER_IMMEDIATE = "Sofort"
TIMER_AT_SETPOINT = "Bei Soll"


def parse_number(value, default):
    """Return float(value), or default when the step property is empty or junk."""
    if value is None:
        return float(default)
    if isinstance(value, str) and value.strip() == "":
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def parse_sample_time(value, default=5):
    """PID sample time in seconds. Only positive values are accepted."""
    sample = parse_number(value, default)
    if sample <= 0:
        return int(default)
    return int(sample)


def selected_id(value):
    """Actor, sensor, and kettle dropdowns store an empty string when unset."""
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.lower() == "none":
        return None
    return text


def same_id(left, right):
    left_id = selected_id(left)
    right_id = selected_id(right)
    if left_id is None or right_id is None:
        return False
    return left_id == right_id


def read_temperature(state):
    """Sensor.get_state() is usually {'value': 65.2}. Missing reads stay None."""
    if state is None:
        return None
    if isinstance(state, dict):
        value = state.get("value")
    else:
        value = state
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def temperature_unit(config_value):
    if str(config_value).strip().upper().startswith("F"):
        return "°F"
    return "°C"


def timer_should_start(mode, current, target, tolerance):
    """Immediate starts the whirlpool clock at step start.

    At-setpoint waits until the measured temperature is inside the band. After
    the boil the wort is above the hop-stand target, so a MashStep-style
    'current >= target' check would start the clock while the wort is still hot.
    """
    if mode == TIMER_IMMEDIATE:
        return True
    if current is None:
        return False
    return abs(float(current) - float(target)) <= abs(float(tolerance))


def target_drifted(current, desired, epsilon=0.05):
    try:
        return abs(float(current) - float(desired)) > float(epsilon)
    except (TypeError, ValueError):
        return True


def logic_looks_like_pid(type_name):
    if type_name is None:
        return False
    return "pid" in str(type_name).lower()


def format_temperature(value, unit):
    if value is None:
        return "k.A."
    return "{:.1f}{}".format(float(value), unit)


def waiting_summary(current, target, unit):
    return "Warte auf {} (Ist {})".format(
        format_temperature(target, unit),
        format_temperature(current, unit),
    )


def hold_detail(current, target, unit, power):
    text = "Soll {} · Ist {}".format(
        format_temperature(target, unit),
        format_temperature(current, unit),
    )
    if power is None:
        return text
    try:
        return text + " · Heizung {:d}%".format(int(round(float(power))))
    except (TypeError, ValueError):
        return text


class PIDArduino(object):
    """Arduino PID (Brett Beauregard), same form as cbpi4-PIDBoil.

    P, I, and D use the same scaling as the CraftBeerPi PID kettle logics:
    the integral gain is multiplied by the sample time, the derivative gain is
    divided by it. Output is clamped. The integrator only moves while the
    output is inside the limits, so it does not wind up against the stops.

    The first sample sets the derivative history to the current temperature.
    Otherwise the first D term treats the last input as 0 and cancels the heat.
    """

    def __init__(
        self,
        sample_time_sec,
        kp,
        ki,
        kd,
        output_min=0.0,
        output_max=100.0,
        get_time_ms=None,
    ):
        if kp is None or ki is None or kd is None:
            raise ValueError("kp, ki and kd are required")
        sample_time_sec = float(sample_time_sec)
        if sample_time_sec <= 0:
            raise ValueError("sample_time_sec must be greater than 0")
        output_min = float(output_min)
        output_max = float(output_max)
        if output_min >= output_max:
            raise ValueError("output_min must be less than output_max")

        self._kp = float(kp)
        self._ki = float(ki) * sample_time_sec
        self._kd = float(kd) / sample_time_sec
        self._sample_time_ms = sample_time_sec * 1000.0
        self._output_min = output_min
        self._output_max = output_max
        self._i_term = 0.0
        self._last_input = None
        self._last_output = 0.0
        self._last_calc = None
        self._get_time_ms = get_time_ms or self._current_time_ms

    def calc(self, input_value, setpoint):
        now = self._get_time_ms()
        if (
            self._last_calc is not None
            and (now - self._last_calc) < self._sample_time_ms
        ):
            return self._last_output

        input_value = float(input_value)
        setpoint = float(setpoint)
        if self._last_input is None:
            self._last_input = input_value

        error = setpoint - input_value
        d_input = input_value - self._last_input

        # Only integrate while the last output was not pinned to a rail.
        if self._output_min < self._last_output < self._output_max:
            self._i_term += self._ki * error
            self._i_term = min(self._i_term, self._output_max)
            self._i_term = max(self._i_term, self._output_min)

        output = (self._kp * error) + self._i_term - (self._kd * d_input)
        output = min(output, self._output_max)
        output = max(output, self._output_min)

        self._last_output = output
        self._last_input = input_value
        self._last_calc = now
        return output

    @staticmethod
    def _current_time_ms():
        return time.time() * 1000.0
