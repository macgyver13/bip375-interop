from __future__ import annotations

import base64
import json
from pathlib import Path

from bip375_interop.smoke import _coldcard_scenario, jade_fixture


def test_jade_fixture_loads_the_vendor_input(tmp_path: Path) -> None:
    fixture = tmp_path / "test_data" / "psbt_sp_resolve_sign.json"
    fixture.parent.mkdir()
    fixture.write_text(json.dumps({
        "input": {
            "network": "localtest",
            "psbt": base64.b64encode(b"psbt\xfffixture").decode(),
        },
    }))

    network, psbt = jade_fixture(tmp_path)

    assert network == "localtest"
    assert psbt == b"psbt\xfffixture"


def test_coldcard_smoke_uses_one_external_p2wpkh_fixture() -> None:
    scenario = _coldcard_scenario()

    assert scenario.signers[0].backend == "coldcard"
    assert scenario.inputs[0]["type"] == "p2wpkh"
    assert scenario.outputs[0]["amount_sat"] == 95000
