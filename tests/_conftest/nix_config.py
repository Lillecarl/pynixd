from __future__ import annotations

from pynixd.nix_config import NixConfig

DEFAULT_SUBSTITUTERS = (
    "https://nixkube.cachix.org/",
    "unix:///nix/var/nix/daemon-socket/socket?root=/",
)
DEFAULT_TRUSTED_PUBLIC_KEYS = ("nixkube.cachix.org-1:H8UE0jlI9pxHexK/NhDmEoLDarJXp1WTymQrsajlh7M=",)


def for_test_store(
    *,
    substituters: tuple[str, ...] = DEFAULT_SUBSTITUTERS,
    trusted_public_keys: tuple[str, ...] = DEFAULT_TRUSTED_PUBLIC_KEYS,
    require_sigs: bool = False,
    experimental_features: tuple[str, ...] = (),
) -> NixConfig:
    return NixConfig(
        substituters=list(substituters),
        trusted_public_keys=list(trusted_public_keys),
        require_sigs=require_sigs,
        experimental_features=list(experimental_features) or None,
    )


def for_dynamic_derivations(
    *,
    substituters: tuple[str, ...] = (),
    trusted_public_keys: tuple[str, ...] = (),
    require_sigs: bool = False,
) -> NixConfig:
    return NixConfig(
        substituters=list(substituters) or None,
        trusted_public_keys=list(trusted_public_keys) or None,
        require_sigs=require_sigs,
        experimental_features=["ca-derivations", "dynamic-derivations"],
    )


def for_ca_derivations(
    *,
    substituters: tuple[str, ...] = (),
    trusted_public_keys: tuple[str, ...] = (),
    require_sigs: bool = False,
) -> NixConfig:
    return NixConfig(
        substituters=list(substituters) or None,
        trusted_public_keys=list(trusted_public_keys) or None,
        require_sigs=require_sigs,
        experimental_features=["ca-derivations"],
    )
