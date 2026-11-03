"""Whirlpool step that holds a setpoint with the HLT kettle PID.

The step does not run a second PID. CraftBeerPi already runs the kettle logic
in its own task and that logic reads kettle.target_temp. A PID inside the step
would fight the kettle logic for the same heater. This step only:

1. writes the whirlpool setpoint onto the HLT kettle
2. switches that kettle logic on
3. optionally runs the whirlpool pump
4. keeps the setpoint until the timer ends, then releases the heater
"""

import asyncio
import logging
from datetime import datetime

from cbpi.api import CBPiStep, Property, action, parameters
from cbpi.api.dataclasses import NotificationType
from cbpi.api.timer import Timer

from .control import (
    TIMER_AT_SETPOINT,
    TIMER_IMMEDIATE,
    format_temperature,
    hold_detail,
    logic_looks_like_pid,
    parse_number,
    read_temperature,
    same_id,
    selected_id,
    target_drifted,
    temperature_unit,
    timer_should_start,
    waiting_summary,
)

logger = logging.getLogger("WhirlpoolHold")


@parameters(
    [
        Property.Number(
            label="Timer",
            configurable=True,
            description="Whirlpool-Dauer in Minuten. 0 hält die Temperatur, bis du Next drückst.",
        ),
        Property.Number(
            label="Temp",
            configurable=True,
            description="Solltemperatur, die der PID des HLT-Kessels halten soll. Einheit wie in den CBPi-Einstellungen.",
        ),
        Property.Sensor(
            label="Sensor",
            description="Temperatur für Timer und Anzeige. Leer = Sensor des HLT-Kessels. Der PID regelt immer den Sensor, der am Kessel hinterlegt ist.",
        ),
        Property.Kettle(
            label="Kettle",
            description="Kessel mit dem HLT-Heizstab und einer PID-Logik (HltHoldPID, PIDBoil oder PID_HERMS).",
        ),
        Property.Kettle(
            label="StopKettle",
            description="Optional. Auto-Modus dieses Kessels wird beim Start ausgeschaltet, damit ein Kochkessel nicht gegen den HLT heizt. Leer lassen oder denselben Kessel wählen, wenn das nicht nötig ist.",
        ),
        Property.Actor(
            label="Pump",
            description="Optionale Whirlpool-Pumpe. An beim Start, aus am Ende. Leer lassen, wenn die Kessel-Logik die Pumpe schon schaltet.",
        ),
        Property.Select(
            label="AutoMode",
            options=["Yes", "No"],
            description="Yes: PID-Logik des HLT beim Start einschalten und am Ende wieder aus.",
        ),
        Property.Select(
            label="TimerStart",
            options=[TIMER_IMMEDIATE, TIMER_AT_SETPOINT],
            description="Sofort: Zeit läuft ab Schrittstart. Bei Soll: Zeit startet erst im Toleranzband (nach dem Kochen, wenn die Würze noch heisser ist).",
        ),
        Property.Number(
            label="Tolerance",
            configurable=True,
            default_value=0.5,
            description="Band in Grad für 'Bei Soll'. Beispiel 0.5 startet den Timer zwischen Soll-0.5 und Soll+0.5.",
        ),
        Property.Select(
            label="HeaterOff",
            options=["Yes", "No"],
            description="Yes: am Ende Sollwert auf 0 und PID aus. No: PID läuft mit dem Sollwert weiter.",
        ),
    ]
)
class WhirlpoolHold(CBPiStep):
    @action("Timer starten", [])
    async def start_timer_now(self):
        if self._timer_running:
            self.cbpi.notify(
                self.name, "Timer läuft bereits", NotificationType.WARNING
            )
            return
        await self._start_timer("Timer von Hand gestartet")

    @action("5 Minuten dazugeben", [])
    async def add_five_minutes(self):
        if not self._timer_running or self.timer is None:
            self.cbpi.notify(
                self.name,
                "Timer muss laufen, um Zeit dazuzugeben",
                NotificationType.WARNING,
            )
            return
        self.cbpi.notify(self.name, "5 Minuten dazugegeben", NotificationType.INFO)
        await self.timer.add(300)

    async def on_timer_done(self, timer):
        # Timer.stop() also schedules this callback. Ignore that, and ignore a
        # timer that reset() already replaced.
        if timer is not self.timer or not self.running or not self._timer_running:
            return
        self._timer_running = False
        self.cbpi.notify(self.name, "Whirlpool beendet", NotificationType.SUCCESS)
        await self.next()

    async def on_timer_update(self, timer, seconds):
        self.summary = Timer.format_time(seconds)
        self.summary2 = hold_detail(
            self._read_temp(), self.target, self.unit, self._heater_power()
        )
        await self.push_update()

    async def on_props_update(self, props):
        await super().on_props_update(props)
        self._read_settings()
        if self.running:
            await self._apply_target()

    async def on_start(self):
        self._timer_running = False
        self._pid_warning_sent = False
        self._zero_timer_announced = False
        self._pump_on = False
        self.kettle = None
        self.summary = "Starte Whirlpool"
        self.summary2 = ""
        self._read_settings()

        await self._stop_other_kettle()
        self.kettle = self.get_kettle(self.kettle_id) if self.kettle_id else None
        if self.kettle is None:
            self.cbpi.notify(
                self.name,
                "Kein HLT-Kessel gewählt. Die Temperatur kann nicht gehalten werden.",
                NotificationType.ERROR,
            )
        else:
            await self._apply_target()
            self._warn_if_not_pid()
            if self.auto_mode:
                await self._set_auto_mode(True)

        await self._set_pump(True)

        seconds = self._timer_seconds if self._timer_seconds > 0 else 1
        self.timer = Timer(
            seconds,
            on_update=self.on_timer_update,
            on_done=self.on_timer_done,
        )
        current = self._read_temp()
        if self.timer_mode == TIMER_IMMEDIATE:
            await self._start_timer("Whirlpool gestartet, PID hält die Solltemperatur.")
        elif timer_should_start(self.timer_mode, current, self.target, self.tolerance):
            await self._start_timer("Solltemperatur erreicht, Timer gestartet.")
        else:
            self.summary = waiting_summary(current, self.target, self.unit)
        await self.push_update()

    async def on_stop(self):
        if self.timer is not None:
            await self.timer.stop()
        self.summary = ""
        self.summary2 = ""
        if getattr(self, "_pump_on", False):
            await self._set_pump(False)
        if getattr(self, "auto_mode", False) and getattr(self, "heater_off", True):
            await self._set_auto_mode(False)
        kettle = getattr(self, "kettle", None)
        if getattr(self, "heater_off", True) and kettle is not None:
            try:
                await self.set_target_temp(kettle.id, 0)
            except Exception:
                logger.exception("Sollwert konnte nicht zurückgesetzt werden")
        await self.push_update()

    async def reset(self):
        self._timer_running = False
        if self.timer is not None:
            await self.timer.stop()
        seconds = self._timer_seconds if self._timer_seconds > 0 else 1
        self.timer = Timer(
            seconds,
            on_update=self.on_timer_update,
            on_done=self.on_timer_done,
        )
        self.summary = waiting_summary(self._read_temp(), self.target, self.unit)
        await self._apply_target()
        await self.push_update()

    async def run(self):
        while self.running is True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("WhirlpoolHold tick failed")
            await asyncio.sleep(1)
        return None

    def _read_settings(self):
        self.unit = temperature_unit(self.get_config_value("TEMP_UNIT", "C"))
        self.target = parse_number(self.props.get("Temp", 0), 0)
        minutes = parse_number(self.props.get("Timer", 0), 0)
        self._timer_seconds = 0 if minutes <= 0 else int(round(minutes * 60))
        self.tolerance = abs(parse_number(self.props.get("Tolerance", 0.5), 0.5))
        self.auto_mode = self.props.get("AutoMode", "Yes") == "Yes"
        self.heater_off = self.props.get("HeaterOff", "Yes") == "Yes"
        mode = self.props.get("TimerStart", TIMER_IMMEDIATE)
        self.timer_mode = mode if mode in (TIMER_IMMEDIATE, TIMER_AT_SETPOINT) else TIMER_IMMEDIATE
        self.kettle_id = selected_id(self.props.get("Kettle", None))
        self.stop_kettle_id = selected_id(self.props.get("StopKettle", None))
        self.pump_id = selected_id(self.props.get("Pump", None))
        self.sensor_id = selected_id(self.props.get("Sensor", None))

    def _read_temp(self):
        sensor_id = self.sensor_id
        if sensor_id is None and self.kettle is not None:
            sensor_id = self.kettle.sensor
        if not sensor_id:
            return None
        try:
            return read_temperature(self.get_sensor_value(sensor_id))
        except Exception:
            logger.exception("Sensorwert nicht lesbar")
            return None

    def _heater_power(self):
        if self.kettle is None or not self.kettle.heater:
            return None
        try:
            if self.get_actor_state(self.kettle.heater) is not True:
                return 0
            actor = self.get_actor(self.kettle.heater)
            if actor is None:
                return None
            return actor.power
        except Exception:
            logger.exception("Heizleistung nicht lesbar")
            return None

    async def _tick(self):
        if self.kettle is not None and target_drifted(self.kettle.target_temp, self.target):
            await self._apply_target()

        current = self._read_temp()
        if self._timer_running:
            self.summary2 = hold_detail(current, self.target, self.unit, self._heater_power())
            await self.push_update()
            return

        if self._timer_seconds <= 0:
            self.summary = "Hält {} ohne Timer".format(
                format_temperature(self.target, self.unit)
            )
            self.summary2 = hold_detail(current, self.target, self.unit, self._heater_power())
            if not self._zero_timer_announced:
                self._zero_timer_announced = True
                self.cbpi.notify(
                    self.name,
                    "Timer ist 0. Temperatur bleibt gehalten, bis du Next drückst.",
                    NotificationType.INFO,
                )
            await self.push_update()
            return

        if timer_should_start(self.timer_mode, current, self.target, self.tolerance):
            if self.timer_mode == TIMER_IMMEDIATE:
                message = "Whirlpool gestartet, PID hält die Solltemperatur."
            else:
                message = "Solltemperatur erreicht, Timer gestartet."
            await self._start_timer(message)
            return

        self.summary = waiting_summary(current, self.target, self.unit)
        self.summary2 = hold_detail(current, self.target, self.unit, self._heater_power())
        await self.push_update()

    async def _start_timer(self, message):
        if self._timer_running or self.timer is None:
            return
        if self._timer_seconds <= 0:
            self._zero_timer_announced = True
            self.summary = "Hält {} ohne Timer".format(
                format_temperature(self.target, self.unit)
            )
            self.cbpi.notify(
                self.name,
                "Kein Timer gesetzt. Temperatur bleibt gehalten, bis du Next drückst.",
                NotificationType.INFO,
            )
            await self.push_update()
            return
        # The timer object was created with the duration at step start. If the
        # property changed before the clock started, rebuild it.
        if getattr(self.timer, "timeout", None) != self._timer_seconds:
            self.timer = Timer(
                self._timer_seconds,
                on_update=self.on_timer_update,
                on_done=self.on_timer_done,
            )
        self.timer.start()
        self._timer_running = True
        self.summary = Timer.format_time(self._timer_seconds)
        self.summary2 = hold_detail(
            self._read_temp(), self.target, self.unit, self._heater_power()
        )
        done_at = datetime.fromtimestamp(
            datetime.now().timestamp() + self._timer_seconds
        )
        self.cbpi.notify(
            self.name,
            "{} Voraussichtliches Ende {}.".format(message, done_at.strftime("%H:%M")),
            NotificationType.INFO,
        )
        await self.push_update()

    async def _apply_target(self):
        if self.kettle is None:
            return
        try:
            await self.set_target_temp(self.kettle.id, self.target)
        except Exception:
            logger.exception("Sollwert konnte nicht gesetzt werden")

    def _warn_if_not_pid(self):
        if self._pid_warning_sent or self.kettle is None:
            return
        if logic_looks_like_pid(self.kettle.type):
            return
        self._pid_warning_sent = True
        logic_name = self.kettle.type or "keine"
        self.cbpi.notify(
            self.name,
            "Kessel '{}' nutzt die Logik '{}'. Gehalten wird die Temperatur nur mit einer PID-Logik auf diesem Kessel, zum Beispiel HltHoldPID.".format(
                self.kettle.name, logic_name
            ),
            NotificationType.WARNING,
        )

    async def _stop_other_kettle(self):
        if self.stop_kettle_id is None or same_id(self.stop_kettle_id, self.kettle_id):
            return
        other = self.get_kettle(self.stop_kettle_id)
        if other is None:
            return
        instance = other.instance
        running = instance is not None and getattr(instance, "state", False) is True
        if not running:
            return
        try:
            await self.cbpi.kettle.stop(other.id)
            self.cbpi.notify(
                self.name,
                "Auto-Modus von '{}' ausgeschaltet".format(other.name),
                NotificationType.INFO,
            )
        except Exception:
            logger.exception("Anderer Kessel konnte nicht gestoppt werden")

    async def _set_auto_mode(self, enabled):
        if self.kettle is None:
            return
        instance = self.kettle.instance
        running = instance is not None and getattr(instance, "state", False) is True
        try:
            if enabled and not running:
                await self.cbpi.kettle.toggle(self.kettle.id)
            elif not enabled and running:
                await self.cbpi.kettle.stop(self.kettle.id)
        except Exception:
            logger.exception("HLT-PID konnte nicht umgeschaltet werden")

    async def _set_pump(self, enabled):
        if self.pump_id is None:
            return
        try:
            if enabled and not self._pump_on:
                await self.actor_on(self.pump_id)
                self._pump_on = True
            elif not enabled and self._pump_on:
                await self.actor_off(self.pump_id)
                self._pump_on = False
        except Exception:
            logger.exception("Whirlpool-Pumpe konnte nicht geschaltet werden")
