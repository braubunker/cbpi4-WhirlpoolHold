"""Regulation rules for the whirlpool hold. No CraftBeerPi install required."""

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "cbpi4_WhirlpoolHold" / "control.py"
_SPEC = importlib.util.spec_from_file_location("whirlpool_control", _PATH)
control = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(control)


def test_read_temperature_accepts_sensor_state_and_bare_numbers():
    assert control.read_temperature({"value": "78.5"}) == 78.5
    assert control.read_temperature(80) == 80.0
    assert control.read_temperature(None) is None
    assert control.read_temperature({"value": None}) is None
    assert control.read_temperature({"value": "hot"}) is None


def test_selected_id_treats_empty_dropdown_as_missing():
    assert control.selected_id(None) is None
    assert control.selected_id("") is None
    assert control.selected_id("  ") is None
    assert control.selected_id("None") is None
    assert control.selected_id("kettle-1") == "kettle-1"


def test_same_kettle_is_not_stopped():
    assert control.same_id("abc", "abc") is True
    assert control.same_id("abc", "def") is False
    assert control.same_id("", "abc") is False


def test_timer_starts_immediately_or_only_inside_the_band():
    assert control.timer_should_start(control.TIMER_IMMEDIATE, 99, 80, 0.5) is True
    assert control.timer_should_start(control.TIMER_IMMEDIATE, None, 80, 0.5) is True
    # Still at boil. A MashStep check (temp >= target) would already be true.
    assert control.timer_should_start(control.TIMER_AT_SETPOINT, 99, 80, 0.5) is False
    assert control.timer_should_start(control.TIMER_AT_SETPOINT, 80.5, 80, 0.5) is True
    assert control.timer_should_start(control.TIMER_AT_SETPOINT, 80.6, 80, 0.5) is False
    assert control.timer_should_start(control.TIMER_AT_SETPOINT, None, 80, 0.5) is False


def test_target_drift_ignores_int_float_noise():
    assert control.target_drifted(80, 80.0) is False
    assert control.target_drifted(0, 80) is True
    assert control.target_drifted("n/a", 80) is True


def test_pid_warning_only_for_logics_that_are_not_pid():
    assert control.logic_looks_like_pid("HltHoldPID") is True
    assert control.logic_looks_like_pid("PIDBoil") is True
    assert control.logic_looks_like_pid("PID_HERMS") is True
    assert control.logic_looks_like_pid("Hysteresis") is False
    assert control.logic_looks_like_pid(None) is False


def test_temperature_unit_follows_cbpi_setting():
    assert control.temperature_unit("C") == "°C"
    assert control.temperature_unit("F") == "°F"
    assert control.temperature_unit(None) == "°C"


def test_first_pid_sample_does_not_kick_the_derivative():
    clock = [0]

    def now():
        return clock[0]

    pid = control.PIDArduino(5, 10, 0, 100, 0, 100, get_time_ms=now)
    # error 10 * P 10 = 100. A derivative against a fake last input of 0
    # would clamp this to 0.
    assert pid.calc(70, 80) == 100

    clock[0] += 5000
    # error 9 * 10 = 90, temperature rose by 1, D = (100/5) * 1 = 20
    assert pid.calc(71, 80) == 70


def test_pid_holds_last_output_until_the_sample_time_elapses():
    clock = [0]

    def now():
        return clock[0]

    pid = control.PIDArduino(5, 10, 0, 0, 0, 100, get_time_ms=now)
    first = pid.calc(70, 80)
    clock[0] += 1000
    assert pid.calc(10, 80) == first


def test_pid_does_not_heat_when_the_wort_is_above_the_setpoint():
    clock = [0]

    def now():
        return clock[0]

    pid = control.PIDArduino(5, 40, 0.2, 10, 0, 100, get_time_ms=now)
    assert pid.calc(70, 80) > 0
    clock[0] += 5000
    assert pid.calc(99, 80) == 0


def test_integrator_does_not_wind_up_while_pinned_at_zero():
    clock = [0]

    def now():
        return clock[0]

    pid = control.PIDArduino(5, 40, 2, 0, 0, 100, get_time_ms=now)
    pid.calc(99, 80)
    for _ in range(50):
        clock[0] += 5000
        assert pid.calc(99, 80) == 0
    assert pid._i_term == 0


def _settle(start, setpoint, steps=900):
    clock = [0]

    def now():
        return clock[0]

    # Gains sized for the toy plant below, not for a real kettle.
    pid = control.PIDArduino(5, 22, 0.35, 12, 0, 100, get_time_ms=now)
    temp = float(start)
    inside = 0
    power = 0
    for _ in range(steps):
        power = pid.calc(temp, setpoint)
        # 100% heat adds 0.35°C per sample. Loss is 0.004°C per degree above 22°C.
        temp += 0.35 * (power / 100.0) - 0.004 * (temp - 22.0)
        clock[0] += 5000
        if abs(temp - setpoint) <= 0.8:
            inside += 1
            if inside >= 24:
                return temp, power
        else:
            inside = 0
    raise AssertionError(
        "did not settle: temp={:.2f} power={:.1f} setpoint={}".format(
            temp, power, setpoint
        )
    )


def test_pid_holds_after_heating_up_and_after_the_boil():
    heated, heat_power = _settle(60, 78)
    assert abs(heated - 78) <= 0.8
    assert 0 < heat_power < 100

    cooled, cool_power = _settle(98, 80)
    assert abs(cooled - 80) <= 0.8
    assert cool_power < 100
