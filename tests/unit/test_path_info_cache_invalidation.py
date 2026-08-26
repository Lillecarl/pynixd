"""An operation that changes a path's info must not leave the old info cached.

`Store.path_info_cache` holds a `ValidPathInfo` for 300 s and
`LocalDBStore.query_path_info` answers from it before it reads SQLite.
Nothing invalidated it, so an operation that changed what `QueryPathInfo`
returns left the store answering with the value from before its own write.

Measured on `tests/functional/test_admin_ops.py::test_add_signatures_via_store`:
`nix store sign` returned 0, and `nix path-info --json` on the next line
reported no signature. Three client sessions, in order:

    QueryValidPaths, QueryMissing, AddMultipleToStore, BuildPathsWithResults
    QueryPathInfo, AddSignatures, QueryMissing        <- `nix store sign`
    QueryPathInfo, QueryMissing                       <- `nix path-info`

`AddMultipleToStore` cached the unsigned info in the first session and the
third read it back. `nix-daemon` holds no such cache and cannot have the
fault, so every case of it is a divergence from Nix.

**These tests are here and not only in `tests/functional/`, because no gate
runs that suite.** `checks.pynixd` builds the unit suite, so an assertion here
is one CI enforces. Issue #289 holds the other half.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from pynixd.serde import (
    AddSignaturesRequest,
    NARHash,
    SignPathInfoRequest,
    StorePath as SerdeStorePath,
    UnkeyedValidPathInfo,
    ValidPathInfo,
)
from pynixd.serde.signature import Signature
from pynixd.store.daemon import DaemonStore

if TYPE_CHECKING:
    from pynixd.connection import ClientConn

PATH = "/nix/store/00000000000000000000000000000001-thing"


def _info(*signatures: str) -> ValidPathInfo:
    return ValidPathInfo(
        path=SerdeStorePath(path=PATH),
        info=UnkeyedValidPathInfo(
            deriver=SerdeStorePath(path=""),
            nar_hash=NARHash("0" * 64),
            references=set(),
            registration_time=0,
            nar_size=1,
            ultimate=0,
            sigs={Signature(name=n, signature="x") for n in signatures},
            ca="",
        ),
    )


class RecordingStore(DaemonStore):
    """A `DaemonStore` whose wire is a list, so no daemon has to exist."""

    def __init__(self) -> None:
        # `Store.__init__` wants a spec, and every field this test reads comes
        # from the base class rather than the spec, so a minimal one does.
        from pynixd.config import LocalSocketStoreSpec
        from pynixd.serde.ids import StoreId

        super().__init__(LocalSocketStoreSpec(store_id=StoreId("test")))
        self.sent: list[str] = []
        # `features` is a read-only property over `_features`, which the
        # handshake fills in. No handshake runs here, so this writes it.
        self._features: set[str] = set()

    async def create_conn(self) -> Any:
        raise AssertionError("no test here opens a connection")

    async def call(self, request: Any, client: ClientConn | None = None, suppress_last: bool = False) -> Any:
        del client, suppress_last
        self.sent.append(type(request).__name__)
        return None


@pytest.fixture
def store() -> RecordingStore:
    return RecordingStore()


def test_the_cache_answers_before_anything_changes_it(store: RecordingStore) -> None:
    """The premise: a cached entry is what `QueryPathInfo` would return."""
    store.add_path_info(_info("old"))

    cached = store.get_path_info(PATH)

    assert cached is not None
    assert {sig.name for sig in cached.info.sigs} == {"old"}


@pytest.mark.anyio
async def test_add_signatures_forgets_the_cached_info(store: RecordingStore) -> None:
    """`nix store sign` reached the daemon and pynixd kept answering with the past."""
    store.add_path_info(_info("old"))

    await store.add_signatures(AddSignaturesRequest(path=SerdeStorePath(path=PATH), sigs=set()))

    assert store.sent == ["AddSignaturesRequest"], "it must still reach the daemon"
    assert store.get_path_info(PATH) is None


@pytest.mark.anyio
async def test_sign_path_info_forgets_it_when_pynixd_signs(store: RecordingStore) -> None:
    """The decompose path calls `self.call` directly and skips `add_signatures`."""
    store.add_path_info(_info("old"))

    await store.sign_path_info(SignPathInfoRequest(info=_info()))

    assert store.sent == ["AddSignaturesRequest"]
    assert store.get_path_info(PATH) is None


@pytest.mark.anyio
async def test_sign_path_info_forgets_it_when_the_daemon_signs(store: RecordingStore) -> None:
    """And the branch that relays `SignPathInfo` upstream changes it just as much."""
    store._features = {"SignPathInfo"}  # noqa: SLF001 -- see RecordingStore.__init__
    store.add_path_info(_info("old"))

    await store.sign_path_info(SignPathInfoRequest(info=_info()))

    assert store.sent == ["SignPathInfoRequest"]
    assert store.get_path_info(PATH) is None


def test_forgetting_a_path_that_was_never_cached_is_not_an_error(store: RecordingStore) -> None:
    """A miss is the common case: most paths are never in the cache."""
    store.forget_path_info(PATH)

    assert store.get_path_info(PATH) is None
