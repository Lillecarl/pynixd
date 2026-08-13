from __future__ import annotations

from pathlib import Path

from pynixd.nix_config import NixConfig, merge_builder_frontend


def test_merge_builder_frontend_defaults_max_jobs_and_builders() -> None:
    merged = merge_builder_frontend(None, Path("/tmp/pynixd.sock"))

    assert merged.max_jobs == 0
    assert merged.builders == [
        "unix:///tmp/pynixd.sock x86_64-linux,aarch64-linux,aarch64-darwin - 500 1 "
        "apple-virt,kvm,nixos-test,benchmark,big-parallel,ca-derivations,recursive-nix,uid-range",
    ]
    assert merged.to_env() == {"NIX_CONFIG": merged.to_nix_conf()}


def test_merge_builder_frontend_preserves_user_max_jobs_and_overwrites_builders() -> None:
    user = NixConfig(
        max_jobs=7,
        builders=["ssh://old-builder x86_64-linux - 1 1 kvm"],
        substituters=["https://cache.example"],
        require_sigs=False,
    )

    merged = merge_builder_frontend(user, Path("/tmp/pynixd.sock"))

    assert merged.max_jobs == 7
    assert merged.builders == [
        "unix:///tmp/pynixd.sock x86_64-linux,aarch64-linux,aarch64-darwin - 500 1 "
        "apple-virt,kvm,nixos-test,benchmark,big-parallel,ca-derivations,recursive-nix,uid-range",
    ]
    assert merged.substituters == ["https://cache.example"]
    assert "require-sigs = false" in merged.to_nix_conf()
    assert "max-jobs = 7" in merged.to_nix_conf()
