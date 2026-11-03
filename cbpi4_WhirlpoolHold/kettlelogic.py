"""PID kettle logic for an HLT that must hold a setpoint, including whirlpool temps.

PIDBoil and PID_HERMS leave PID mode near boiling and force a fixed power.
That is what you want for the boil and what you do not want while holding a
whirlpool or hop-stand temperature on the same heater. This logic stays in PID
for every setpoint. Put it on the kettle whose heater is the HLT element.
"""

import asyncio
import logging

from cbpi.api import CBPiKettleLogic, Property, parameters

from .control import PIDArduino, parse_number, parse_sample_time, read_temperature

logger = logging.getLogger("HltHoldPID")


@parameters(
    [
        Property.Number(
            label="P",
            configurable=True,
            default_value=40,
            description="Proportionalanteil. Werte aus cbpi4-PID_AutoTune übernehmen.",
        ),
        Property.Number(
            label="I",
            configurable=True,
            default_value=0.2,
            description="Integralanteil, gleiche Skalierung wie PIDBoil und PID AutoTune.",
        ),
        Property.Number(
            label="D",
            configurable=True,
            default_value=10,
            description="Differentialanteil, gleiche Skalierung wie PIDBoil.",
        ),
        Property.Select(
            label="SampleTime",
            options=[2, 5],
            description="PID-Abtastung in Sekunden. Standard: 5.",
        ),
        Property.Number(
            label="Max_Output",
            configurable=True,
            default_value=100,
            description="Maximale Heizleistung in Prozent.",
        ),
    ]
)
class HltHoldPID(CBPiKettleLogic):
    async def on_stop(self):
        heater = getattr(self, "heater", None)
        if heater is not None:
            await self.actor_off(heater)

    async def run(self):
        self.heater = None
        try:
            sample_time = parse_sample_time(self.props.get("SampleTime", 5), 5)
            max_output = int(parse_number(self.props.get("Max_Output", 100), 100))
            if max_output < 1:
                max_output = 1
            if max_output > 100:
                max_output = 100
            kp = parse_number(self.props.get("P", 40), 40)
            ki = parse_number(self.props.get("I", 0.2), 0.2)
            kd = parse_number(self.props.get("D", 10), 10)

            self.kettle = self.get_kettle(self.id)
            self.heater = self.kettle.heater
            self.max_output = max_output
            pid = PIDArduino(sample_time, kp, ki, kd, 0, max_output)
            logger.info(
                "HltHoldPID P:%s I:%s D:%s sample:%ss max:%s heater:%s",
                kp,
                ki,
                kd,
                sample_time,
                max_output,
                self.heater,
            )

            # On at 0% so the logic owns the actor before the first calculation.
            await self.actor_on(self.heater, 0)
            heat_percent_old = None

            while self.running is True:
                try:
                    await self._regulate(pid, max_output, heat_percent_old)
                    heat_percent_old = self._last_power
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("HltHoldPID sample failed")
                await asyncio.sleep(sample_time)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("HltHoldPID failed")
        finally:
            self.running = False
            if self.heater is not None:
                await self.actor_off(self.heater)

    async def _regulate(self, pid, max_output, heat_percent_old):
        current = read_temperature(self.get_sensor_value(self.kettle.sensor))
        target = self.get_kettle_target_temp(self.id)
        self._last_power = heat_percent_old
        if current is None or target is None:
            return

        raw = pid.calc(current, float(target))
        heat_percent = int(round(max(0, min(max_output, raw))))
        heater_actor = self.cbpi.actor.find_by_id(self.heater)
        actor_power = None if heater_actor is None else heater_actor.power
        if heat_percent != heat_percent_old or heat_percent != actor_power:
            await self.actor_set_power(self.heater, heat_percent)
            self._last_power = heat_percent
