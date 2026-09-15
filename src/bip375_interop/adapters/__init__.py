"""Device adapters exposed by the interoperability harness."""

from bip375_interop.adapters.base import (
    AdapterCapabilities,
    CommandPlan,
    CommandResult,
    SignerAdapter,
    SignerIdentity,
)
from bip375_interop.adapters.bitsaga import BitSagaAdapter
from bip375_interop.adapters.coldcard import ColdcardAdapter
from bip375_interop.adapters.jade import JadeAdapter
from bip375_interop.adapters.seedsigner import SeedSignerAdapter

__all__ = [
    "AdapterCapabilities",
    "BitSagaAdapter",
    "ColdcardAdapter",
    "CommandPlan",
    "CommandResult",
    "JadeAdapter",
    "SeedSignerAdapter",
    "SignerAdapter",
    "SignerIdentity",
]
