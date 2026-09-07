"""A field with an alias takes a value under its name as well.

`Realisation` is the one model with aliases: the JSON on the wire says
`outPath`, and Python code says `out_path`. Pydantic takes the alias alone by
default, so `Realisation(out_path=...)` put nothing in the field and raised
nothing. The answer then carried `null` where a store path belongs, and the
client read no path.

`WireModel` sets both directions now, so the next field that gets an alias
cannot repeat this.
"""

from __future__ import annotations

from nix_daemon_protocol.realisation import Realisation
from nix_daemon_protocol.store_path import StorePath
from nix_daemon_protocol.wire_message import WireModel

PATH = "abcdefghijklmnopqrstuvwxyz012345-thing"


def test_the_base_takes_a_value_both_ways() -> None:
    assert WireModel.model_config["validate_by_name"] is True
    assert WireModel.model_config["validate_by_alias"] is True


def test_the_name_of_the_field_fills_the_field() -> None:
    realisation = Realisation(id="sha256:00!out", out_path=StorePath(path=PATH))

    assert realisation.out_path is not None
    assert str(realisation.out_path) == PATH


def test_the_alias_fills_the_field() -> None:
    realisation = Realisation.model_validate({"id": "sha256:00!out", "outPath": PATH})

    assert realisation.out_path is not None
    assert str(realisation.out_path) == PATH


def test_the_wire_reads_the_alias_back() -> None:
    """The JSON keeps the alias, whichever way the value went in."""
    realisation = Realisation(id="sha256:00!out", out_path=StorePath(path=PATH))

    assert Realisation.from_json(realisation.to_json()) == realisation
    assert '"outPath"' in realisation.to_json()
