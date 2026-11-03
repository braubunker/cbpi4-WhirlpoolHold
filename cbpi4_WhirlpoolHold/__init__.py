from .kettlelogic import HltHoldPID
from .step import WhirlpoolHold


def setup(cbpi):
    """Called by CraftBeerPi while it loads installed cbpi* packages."""
    cbpi.plugin.register("WhirlpoolHold", WhirlpoolHold)
    cbpi.plugin.register("HltHoldPID", HltHoldPID)
