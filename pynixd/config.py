"""Pydantic-settings model for the PYNIXD_CONFIG JSON file and env vars."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from .nix_config import NixConfig
from .serde.ids import LOCAL_STORE_ID, StoreId
from .store_layout import StoreLayout


class ScheduleMode(StrEnum):
    """How pynixd assigns builds: auto-detect, proxy-only, or scheduler-only."""

    auto = "auto"
    proxy = "proxy"
    scheduler = "scheduler"


if TYPE_CHECKING:
    from .store import (
        DaemonStore,
        ExternalUnixStore,
        HTTPBinaryCacheStore,
        LocalDBStore,
        LocalStore,
        SSHSocketStore,
        SSHSubprocessStore,
        Store,
    )


def _feature_matrix_from_config(
    systems: set[str] | None,
    system_features: set[str],
) -> dict[str, set[str]] | None:
    """Convert config-level systems + system_features into a feature_matrix.

    Returns None if both are empty/default (meaning: probe at startup).
    """
    if systems and system_features:
        return {s: set(system_features) for s in systems}
    if systems:
        return {s: set() for s in systems}
    if system_features:
        raise ValueError(
            "system_features specified without systems; specify systems to create a feature_matrix",
        )
    return None


class StoreRankingSettings(BaseModel):
    """Configurable weights for the telemetry-driven store ranking algorithm."""

    locality_weight: float = 500.0
    """Points for data locality: (common_paths / total_paths) * locality_weight."""

    cpu_idle_weight: float = 100.0
    """Points for CPU availability: (1.0 - cpu_utilization) * cpu_idle_weight."""

    cpu_pressure_penalty: float = 50.0
    """Penalty scaled by CPU PSI average: -(cpu_psi * cpu_pressure_penalty)."""

    io_pressure_penalty: float = 50.0
    """Penalty scaled by IO PSI average: -(io_psi * io_pressure_penalty)."""

    concurrency_penalty: float = 50.0
    """Penalty per active connection: -(active_conns * concurrency_penalty)."""

    predicted_load_penalty_per_min: float = 10.0
    """Penalty per expected minute of in-flight work: -(predicted_mins * load_penalty)."""

    thundering_herd_penalty: float = 100.0
    """Penalty per build assigned in current scheduling pass: -(assigned * penalty)."""

    min_schedule_score: float = 0.0
    """Minimum score required for a store to be considered. If lower, builds stay queued."""


class StoreSpecBase(BaseModel):
    """Common settings shared by all store specs."""

    store_id: StoreId | None = None
    systems: set[str] | None = None
    system_features: set[str] = Field(default_factory=set)
    feature_matrix: dict[str, set[str]] | None = None
    nix_bin: str = "nix"
    idle_ttl: float = 10.0
    max_lifetime: float = 300.0
    """How long a pooled connection may serve before the pool retires it.

    A worker of the daemon holds a temporary root for each path that it
    builds or substitutes, and it releases those roots when it exits. A
    pooled connection keeps that worker alive, so a connection in steady
    use holds every root it ever made. Zero turns the rule off. Issue #174.
    """
    scheduleable: bool = True
    priority: float = 1.0
    score_penalty: int = 0
    gc_enabled: bool = True
    gc_max_age: int | None = None
    no_schedule: bool = False
    probe: bool | None = None
    settings: PynixdSettings | None = None
    reconnect: bool = True
    reconnect_min_delay: float = 1.0
    reconnect_max_delay: float = 300.0

    def _effective_feature_matrix(self) -> dict[str, set[str]] | None:
        if self.feature_matrix is not None:
            return self.feature_matrix
        return _feature_matrix_from_config(self.systems, self.system_features)

    def to_store(self, store_id: str) -> Store:
        raise NotImplementedError(f"{type(self).__name__} must implement to_store()")


class LocalSocketStoreSpec(StoreSpecBase):
    """A local Nix daemon accessed via Unix domain socket.

    Optionally wraps the daemon with a local SQLite database (``use_db``)
    for fast-path resolution of queries like ``IsValidPath``, ``QueryPathInfo``,
    and ``QueryReferrers``.
    """

    type: Literal["local-socket"] = "local-socket"
    store_path: Path = Path("/")
    """The root of a chroot store, which `nix daemon --store` takes.

    Leave `store_dir` unset to use this. `/` is the store of the machine.
    """

    store_dir: Path | None = None
    """The directory in a store path, for a relocated store.

    Set this and `state_dir` together to serve a store that `NIX_STORE_DIR`
    moved, rather than one that `--store <root>` moved. The two shapes differ:
    a chroot store keeps `builtins.storeDir` at `/nix/store` and puts the
    files under the root, and a relocated store moves the store path itself.
    `pynixd/store_layout.py` states both. Issue #176.
    """

    state_dir: Path | None = None
    """The directory that holds `db/` and `temproots/`, for a relocated store.

    Required with `store_dir`, and refused without it. Nix keeps the two
    independent, so neither one gives the other.
    """

    socket_path: Path = Path("pynixd-nix")
    """Where the managed daemon listens.

    An absolute path is used as it is. A relative one is a name under
    `<state_dir>/daemon-socket/`, which is where Nix puts its own socket. The
    value was `nix/var/nix/daemon-socket/pynixd-nix` and it was joined to the
    store root, which gives the same path for a chroot store and no path at
    all for a relocated one.
    """

    nix_config: NixConfig | None = None
    extra_env: dict[str, str] | None = None
    extra_args: list[str] | None = None
    use_db: bool = True
    monitor: bool = True

    @model_validator(mode="after")
    def _check_the_two_shapes(self) -> LocalSocketStoreSpec:
        """A relocated store names both of its directories, or neither.

        A default of `/nix/var/nix` for the state would put the temporary
        roots and the database of a relocated store in the store of the
        machine, and nothing would report it.
        """
        if self.store_dir is None and self.state_dir is not None:
            raise ValueError("state_dir needs store_dir: it describes a relocated store")
        if self.store_dir is not None and self.state_dir is None:
            raise ValueError("store_dir needs state_dir: Nix keeps the two independent")
        return self

    def layout(self) -> StoreLayout:
        """The three directories of the store that this spec names."""
        if self.store_dir is not None and self.state_dir is not None:
            return StoreLayout.relocated_store(self.store_dir, self.state_dir)
        return StoreLayout.chroot(self.store_path)

    def to_store(self, store_id: str) -> LocalStore | LocalDBStore:
        """Build a ``LocalStore`` or ``LocalDBStore`` from this spec."""
        from .store.local_daemon import LocalStore
        from .store.local_db import LocalDBStore

        spec = self.model_copy(update={"store_id": StoreId(store_id)})
        if spec.use_db:
            return LocalDBStore(spec)
        return LocalStore(spec)


class ExternalUnixStoreSpec(StoreSpecBase):
    """A remote Nix daemon connected via an external Unix socket.

    Used as a substitution source only — builds and GC are disabled.
    """

    type: Literal["external-unix"] = "external-unix"
    store_path: Path = Path("/")
    socket_path: Path = Path("/nix/var/nix/daemon-socket/socket")
    monitor: bool = False
    scheduleable: bool = False
    no_schedule: bool = True
    gc_enabled: bool = False

    def to_store(self, store_id: str) -> ExternalUnixStore:
        """Build an ``ExternalUnixStore`` from this spec."""
        from .store import ExternalUnixStore

        return ExternalUnixStore(
            self.model_copy(update={"store_id": StoreId(store_id)}),
        )


class ReverseStoreSpec(StoreSpecBase):
    """Configuration for a reverse store (builder connects to controller).

    Reverse stores are created dynamically when a builder registers with
    the controller via the reverse server. This spec exists to provide a
    type in the StoreSpec union and document the expected fields.
    """

    type: Literal["reverse"] = "reverse"

    def to_store(self, store_id: str) -> DaemonStore:
        raise NotImplementedError("Reverse stores are created dynamically, not from config")


class ReverseAcceptorSettings(BaseModel):
    """Settings for the reverse acceptor (controller side).

    The reverse acceptor listens for builder-initiated SSH connections.
    Builders (reverse initiators) register themselves as build stores,
    enabling NAT traversal.
    """

    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 2235
    host_key_path: Path | None = None


class ReverseInitiatorSettings(BaseModel):
    """Settings for the reverse initiator (builder side).

    The reverse initiator connects to a controller's reverse acceptor and
    registers the local pynixd instance as a build store.  After registration,
    the controller opens pooled ``nix-daemon --stdio`` sessions to execute
    builds and queries.
    """

    enabled: bool = False
    acceptor_host: str = "127.0.0.1"
    acceptor_port: int = 2235
    store_id: str | None = None
    systems: list[str] | None = None
    system_features: list[str] = Field(default_factory=list)
    nix_bin: str = "nix"
    server_host_key_paths: list[Path] = Field(default_factory=list)
    reconnect_min_delay: float = 1.0
    reconnect_max_delay: float = 60.0
    shutdown_on_connect_failure_seconds: float | None = None


class SSHSubprocessStoreSpec(StoreSpecBase):
    """A remote Nix daemon accessed via ``ssh <host> nix-daemon --stdio``.

    Spawns a new ``nix-daemon`` process per connection over SSH.
    """

    type: Literal["ssh-subprocess"] = "ssh-subprocess"
    host: str
    port: int = 22
    username: str | None = None
    store_path: Path = Path("/")
    monitor: bool = True
    client_keys: list[Any] = Field(default_factory=list)
    persistent_connection: bool = True
    """Whether to hold one SSH connection open for the life of the store.

    True, the default, is what a normal remote builder wants. The connection
    is opened at startup and kept, so its state *is* the health of the store:
    the reconnect loop, the backoff and the circuit breaker all read it, and
    the resource monitor polls over it. A builder that is up looks up because
    the socket is there.

    False suits a builder that starts on demand -- a local VM behind a
    socket-activated unit, with a watchdog that stops it once the last
    connection closes. pynixd then connects on first use and drops the
    transport once its pool holds nothing, so the builder is free to go away.
    The cost is the measurement above: with no connection there is nothing to
    read the store's health from, and pynixd learns a backend is down by
    failing to reach it.

    Set `monitor = false` alongside it. The monitor polls over the same
    connection, so it would hold the builder awake by itself. Issue #164.
    """

    def to_store(self, store_id: str) -> SSHSubprocessStore:
        """Build an ``SSHSubprocessStore`` from this spec."""
        from .store import SSHSubprocessStore

        return SSHSubprocessStore(
            self.model_copy(update={"store_id": StoreId(store_id)}),
        )


class SSHSocketStoreSpec(StoreSpecBase):
    """A remote Nix daemon accessed via SSH with Unix socket forwarding.

    Reuses an existing daemon socket on the remote host through an SSH
    tunnel, avoiding per-connection daemon startup overhead.
    """

    type: Literal["ssh-socket"] = "ssh-socket"
    host: str
    port: int = 22
    username: str | None = None
    socket_path: Path = Path("/nix/var/nix/daemon-socket/socket")
    monitor: bool = True
    client_keys: list[Any] = Field(default_factory=list)
    persistent_connection: bool = True
    """Whether to hold one SSH connection open for the life of the store.

    True, the default, is what a normal remote builder wants. The connection
    is opened at startup and kept, so its state *is* the health of the store:
    the reconnect loop, the backoff and the circuit breaker all read it, and
    the resource monitor polls over it. A builder that is up looks up because
    the socket is there.

    False suits a builder that starts on demand -- a local VM behind a
    socket-activated unit, with a watchdog that stops it once the last
    connection closes. pynixd then connects on first use and drops the
    transport once its pool holds nothing, so the builder is free to go away.
    The cost is the measurement above: with no connection there is nothing to
    read the store's health from, and pynixd learns a backend is down by
    failing to reach it.

    Set `monitor = false` alongside it. The monitor polls over the same
    connection, so it would hold the builder awake by itself. Issue #164.
    """

    def to_store(self, store_id: str) -> SSHSocketStore:
        """Build an ``SSHSocketStore`` from this spec."""
        from .store import SSHSocketStore

        return SSHSocketStore(
            self.model_copy(update={"store_id": StoreId(store_id)}),
        )


class HTTPBinaryCacheSpec(StoreSpecBase):
    """A remote binary cache accessed via HTTP (Nix binary cache protocol).

    Read-only substitution source. Supports health tracking via configurable
    concurrency limits and failure ratio thresholds.
    """

    type: Literal["http-binary-cache"] = "http-binary-cache"
    url: str
    max_concurrent: int | None = None
    max_fail_ratio: float = 0.5
    health_window: int = 10
    scheduleable: bool = False
    no_schedule: bool = True
    gc_enabled: bool = False

    def to_store(self, store_id: str) -> HTTPBinaryCacheStore:
        """Build an ``HTTPBinaryCacheStore`` from this spec."""
        from .store import HTTPBinaryCacheStore

        return HTTPBinaryCacheStore(
            self.model_copy(update={"store_id": StoreId(store_id)}),
        )


StoreSpec = Annotated[
    LocalSocketStoreSpec
    | ExternalUnixStoreSpec
    | SSHSubprocessStoreSpec
    | SSHSocketStoreSpec
    | ReverseStoreSpec
    | HTTPBinaryCacheSpec,
    Field(discriminator="type"),
]


class _ConfigFileSource(PydanticBaseSettingsSource):
    """Read settings fields from a JSON config file (PYNIXD_CONFIG)."""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        """Return no value — all fields are loaded in ``__call__``."""
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        """Load settings from the JSON file at ``PYNIXD_CONFIG``.

        Returns only fields that exist in the settings model. Returns empty
        dict if the path is unset or does not exist.
        """
        config_path = self.settings_cls.model_fields.get("config")
        if config_path is None:
            return {}
        env_val = self.current_state.get("config")
        if env_val is None:
            return {}
        path = Path(env_val)
        if not path.exists():
            return {}
        with path.open() as f:
            data = json.load(f)
        return {k: v for k, v in data.items() if k in self.settings_cls.model_fields}


class PynixdSettings(BaseSettings):
    """Unified configuration from env vars (priority) and config file.

    Env var mapping: PYNIXD_<FIELD_NAME> (e.g. PYNIXD_SSH_PORT → ssh_port).
    Config file fields (lower priority) are loaded from the JSON file at
    PYNIXD_CONFIG (if it points to an existing path).
    """

    model_config = SettingsConfigDict(env_prefix="PYNIXD_")

    config: Path | None = None
    stores: dict[str, StoreSpec] = Field(default_factory=dict)

    ssh_host: str = "127.0.0.1"
    ssh_port: int | None = 2234
    ssh_host_key: Path | None = None

    unix_path: Path | None = Path("/run/pynixd/pynixd.sock")

    http_host: str = "0.0.0.0"
    http_port: int | None = None
    http_enable_cache: bool = True
    http_enable_metrics: bool = True
    http_metrics_no_auth: bool = True
    http_user: str | None = None
    http_pass: str | None = None
    http_htpasswd: Path | None = None
    http_priority: int = 30
    http_upload_dir: Path | None = None

    https_port: int | None = None
    https_cert: Path | None = None
    https_key: Path | None = None

    reverse_acceptor: ReverseAcceptorSettings = Field(default_factory=ReverseAcceptorSettings)
    reverse_initiator: ReverseInitiatorSettings = Field(default_factory=ReverseInitiatorSettings)

    admin_users: set[str] = Field(default_factory=set)

    gc_enabled: bool = True
    gc_interval: float = 3600.0

    # Scheduling & Telemetry
    schedule_mode: ScheduleMode = ScheduleMode.auto
    ranking: StoreRankingSettings = Field(default_factory=StoreRankingSettings)

    # Substitution scheduling
    substitution_cache_maxsize: int = 100_000
    substitution_positive_ttl: float = 300.0
    substitution_negative_ttl: float = 15.0
    substitution_health_window: int = 64
    substitution_health_min_fill_ratio: float = 0.10
    substitution_health_min_success_ratio: float = 0.50
    substitution_query_timeout: float = 2.0

    # Resource Monitoring
    psi_cpu_threshold: float = 15.0  # % pressure (some)
    psi_mem_threshold: float = 10.0  # % pressure (some)
    psi_io_threshold: float = 10.0  # % pressure (some)
    min_available_memory_mb: int = 512  # Hard gate: block if < 512MB available
    max_cpu_util: float = 90.0  # Fallback: max 90% utilization
    gate_timeout: float = 5.0  # seconds to wait for pressure to subside

    # Plugins
    plugins: list[Path] = Field(default_factory=list)

    # Logging (the log-level threshold; filtering is handled by plugins)
    log_level: str = "WARNING"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Configure settings source priority: init > env > JSON config file."""
        return (init_settings, env_settings, _ConfigFileSource(settings_cls))

    def to_stores(self) -> dict[StoreId, Store]:
        """Convert all store specs to live Store instances."""
        stores: dict[StoreId, Store] = {}
        for key, spec in self.stores.items():
            spec.settings = self
            store = spec.to_store(store_id=key)
            stores[store.store_id] = store

        if LOCAL_STORE_ID not in stores:
            spec = LocalSocketStoreSpec(
                store_id=LOCAL_STORE_ID,
                monitor=False,
                settings=self,
            )
            # `spec.to_store`, so the implicit local store honours `use_db`
            # like a configured one. This line read `LocalStore(spec)`, which
            # ignored the option and hardcoded the answer. `use_db` defaults to
            # true, and a configured `stores.local` already reached the loop
            # above and got a `LocalDBStore` -- so the SQLite fast paths were
            # on for anyone who wrote the store out and off for everyone who
            # did not, including every deployment of `pynixd daemon`.
            #
            # `LocalStoreDB.open` degrades on its own when it cannot open the
            # database: it returns an inactive instance, every fast path of
            # `LocalDBStore` returns `None` for that, and `DaemonStore.execute`
            # falls through to the wire. So "on by default" costs a store that
            # cannot read the database nothing but one warning.
            stores[LOCAL_STORE_ID] = spec.to_store(store_id=str(LOCAL_STORE_ID))

        return stores


StoreSpecBase.model_rebuild()
LocalSocketStoreSpec.model_rebuild()
PynixdSettings.model_rebuild()
