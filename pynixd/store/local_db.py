"""LocalDBStore — LocalStore with SQLite database for fast-path queries."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel

from nix_daemon_protocol.store_dir import store_prefix

from ..local_store_db import LocalStoreDB
from ..serde import StorePath as SerdeStorePath
from .local_daemon import LocalStore

log = structlog.get_logger(__name__)


def referenced_paths(request: object) -> set[str]:
    """Every store path that a request names in its own fields.

    The fields are the declaration, so this reads the model rather than a
    list of operations that somebody has to keep current. A new operation
    that carries a `StorePath` is counted on the day it is added.

    Only the fields of the request itself. A nested model, such as the
    `BasicDerivation` of a build, names the paths that the build will
    *produce*, and a path that does not exist yet was referenced by nothing.
    """
    if not isinstance(request, BaseModel):
        return set()
    found: set[str] = set()
    for name in type(request).model_fields:
        value = getattr(request, name, None)
        if isinstance(value, SerdeStorePath):
            found.add(str(value))
        elif isinstance(value, (set, frozenset, list, tuple)):
            found.update(str(item) for item in value if isinstance(item, SerdeStorePath))
    found.discard("")
    return found


class LocalDBStore(LocalStore):
    """LocalStore with SQLite database for fast-path query optimizations.

    Each executor method answers from SQLite when the database is open, and
    returns `None` when it is not. `DaemonStore.execute` treats a falsy result
    as "no fast path" and calls the wire, so a database pynixd cannot open
    costs correctness nothing.

    **The fast paths are valid for a plain local store only. A
    `local-overlay-store` must not use this class.** They read one database,
    and an overlay store keeps its lower store's paths in a second one:
    `LocalOverlayStore::isValidPathUncached` asks `LocalStore` first, then
    `lowerStore`, and only then copies the lower path's info up with
    `LocalStore::registerValidPath`. Reading the upper database alone would
    report a valid lower path as invalid *and* skip the sync that would have
    made it valid. The same applies to `queryPathInfoUncached`,
    `queryReferrers`, `queryValidPaths` and `queryPathFromHashPart`, each of
    which overlay overrides for the same reason.

    `_refuses_a_database` is what keeps that from happening quietly.
    """

    db: LocalStoreDB

    def _refuses_a_database(self) -> str | None:
        """Why this store must not use SQLite, or `None` when it may.

        Only the store URI can answer this, and pynixd builds the managed
        daemon's URI itself -- `LocalStore.ensure_daemon` passes `--store
        <store_path>`, which is always a plain local store. `extra_args` is
        the one way a different store reaches the daemon, because it is
        appended after that flag and a later `--store` wins.
        """
        overlay = next((arg for arg in self.extra_args if "local-overlay" in arg), None)
        if overlay is not None:
            return (
                f"the daemon is started with {overlay!r}, and the SQLite fast paths read one "
                f"database. An overlay store keeps its lower paths in another one, so a fast "
                f"path would call a valid path invalid."
            )
        return None

    async def start(self, sync_paths: bool = True) -> None:
        """Initialise the SQLite database and start the daemon store."""
        await self.ensure_daemon()
        refusal = self._refuses_a_database()
        if refusal is not None:
            log.warning("local_store_db_refused", store_id=str(self.store_id), reason=refusal)
            self.db = LocalStoreDB.inactive(self.store_path or Path("/"))
        else:
            self.db = await LocalStoreDB.open(self.store_path or Path("/"))
        await super().start(sync_paths=sync_paths)

    async def close(self) -> None:
        """Close the SQLite database and the daemon store."""
        await self.db.close()
        await super().close()

    async def execute(self, request, client=None, suppress_last=False, skip_probe=False):  # type: ignore[no-untyped-def] -- the parent is untyped
        """Note the paths of the request, then run it.

        This is the one place that sees every operation, whichever route
        answers it: a fast path over SQLite, or the wire. `LocalStoreDB`
        collects the paths and writes them a few seconds later.

        `mark_path` and `mark_paths` had no caller anywhere, in any project of
        this repository. The set they fill was therefore always empty,
        `flush_references` returned at its first line every time, and the
        background task woke every five seconds to do nothing. So
        `registrationTime` was never refreshed, and the LRU garbage collection
        that the refresh exists for never had an input. Issue #166.
        """
        self.db.mark_paths(referenced_paths(request))
        return await super().execute(request, client=client, suppress_last=suppress_last, skip_probe=skip_probe)

    # ── Fast-path overrides ────────────────────────────────────────

    async def is_valid_path(self, request: Any, client: Any = None, suppress_last: bool = False) -> Any:
        """IsValidPath — fast-path via SQLite lookup."""
        if not self.db.active:
            return None

        path_str = str(request.path)

        from pynixd.serde import IsValidPathResponse

        from .queries import IS_VALID_PATH

        async with self.db.execute(IS_VALID_PATH, (path_str,)) as cursor:
            row = await cursor.fetchone()
        if row is not None:
            return IsValidPathResponse(valid=True)

        return IsValidPathResponse(valid=False)

    async def query_path_info(self, request: Any, client: Any = None, suppress_last: bool = False) -> Any:
        """QueryPathInfo — fast-path via SQLite, with in-memory cache check."""
        cached = self.get_path_info(request.path)
        if cached is not None:
            from pynixd.serde import QueryPathInfoResponse

            return QueryPathInfoResponse(valid=True, info=cached.info)

        from pynixd.serde import QueryPathInfoResponse, StorePath as SerdeStorePath
        from pynixd.serde.content_address import ContentAddress
        from pynixd.serde.nar_hash import NARHash
        from pynixd.serde.path_info import UnkeyedValidPathInfo as SerdeUnkeyedValidPathInfo
        from pynixd.serde.signature import Signature
        from pynixd.serde.wire_time import Time

        from .queries import QUERY_PATH_INFO, QUERY_REFERENCES

        async with self.db.execute(QUERY_PATH_INFO, (str(request.path),)) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return QueryPathInfoResponse(valid=False)

        _path, deriver, nar_hash, reg_time, nar_size, ultimate, sigs, ca = row

        async with self.db.execute(QUERY_REFERENCES, (str(request.path),)) as cursor:
            ref_rows = await cursor.fetchall()
        refs = {r[0] for r in ref_rows}

        sig_set: set = set()
        if sigs:
            for s in sigs.split():
                sig_set.add(Signature(**Signature.from_str(s)))

        info = SerdeUnkeyedValidPathInfo(
            deriver=SerdeStorePath(path=deriver or ""),
            nar_hash=NARHash(hash=nar_hash),
            references={SerdeStorePath(path=r) for r in refs},  # type: ignore[arg-type]
            registration_time=Time(ts=reg_time),
            nar_size=nar_size or 0,
            ultimate=bool(ultimate),
            sigs=sig_set,
            ca=ContentAddress(value=ca or ""),
        )
        return QueryPathInfoResponse(valid=True, info=info)

    async def query_all_valid_paths(self, request: Any, client: Any = None, suppress_last: bool = False) -> Any:
        """QueryAllValidPaths — fast-path via SQLite."""

        from pynixd.serde import QueryAllValidPathsResponse, StorePath as SerdeStorePath

        from .queries import QUERY_ALL_VALID_PATHS

        async with self.db.execute(QUERY_ALL_VALID_PATHS) as cursor:
            rows = await cursor.fetchall()
        paths: set = {SerdeStorePath(path=r[0]) for r in rows}  # type: ignore[arg-type]
        return QueryAllValidPathsResponse(paths=paths)

    async def query_valid_paths(self, request: Any, client: Any = None, suppress_last: bool = False) -> Any:
        """QueryValidPaths — fast-path via SQLite."""

        import json

        from pynixd.serde import QueryValidPathsResponse, StorePath as SerdeStorePath

        from .queries import QUERY_VALID_PATHS

        paths_json = json.dumps([str(p) for p in request.paths])
        async with self.db.execute(QUERY_VALID_PATHS, (paths_json,)) as cursor:
            rows = await cursor.fetchall()

        paths: set = {SerdeStorePath(path=r[0]) for r in rows}  # type: ignore[arg-type]
        return QueryValidPathsResponse(paths=paths)

    async def query_path_from_hash_part(self, request: Any, client: Any = None, suppress_last: bool = False) -> Any:
        """QueryPathFromHashPart — fast-path via SQLite."""

        from pynixd.serde import QueryPathFromHashPartResponse, StorePath as SerdeStorePath

        from .queries import QUERY_PATH_FROM_HASH_PART

        prefix = f"{store_prefix()}{request.path}"
        upper = prefix[:-1] + chr(ord(prefix[-1]) + 1)
        async with self.db.execute(QUERY_PATH_FROM_HASH_PART, (prefix, upper)) as cursor:
            row = await cursor.fetchone()
        if row:
            return QueryPathFromHashPartResponse(value=SerdeStorePath(path=row[0]))

        return None  # fall through

    async def query_closure(self, request: Any, client: Any = None, suppress_last: bool = False) -> Any:
        """QueryClosure — fast-path via SQLite recursive CTE."""

        import json

        from pynixd.serde import QueryClosureResponse, StorePath as SerdeStorePath

        from .queries import QUERY_CLOSURE

        seeds_json = json.dumps([str(p) for p in request.paths])
        async with self.db.execute(QUERY_CLOSURE, (seeds_json,)) as cursor:
            rows = await cursor.fetchall()
        paths: set = {SerdeStorePath(path=row[0]) for row in rows}  # type: ignore[arg-type]
        return QueryClosureResponse(paths=paths)

    async def query_closure_with_info(self, request: Any, client: Any = None, suppress_last: bool = False) -> Any:
        """QueryClosureWithInfo — fast-path via SQLite recursive CTE with full info."""

        if not request.paths:
            from pynixd.serde import QueryClosureWithInfoResponse

            return QueryClosureWithInfoResponse(infos=[])

        import json

        from pynixd.serde import QueryClosureWithInfoResponse, StorePath as SerdeStorePath
        from pynixd.serde.content_address import ContentAddress
        from pynixd.serde.nar_hash import NARHash
        from pynixd.serde.path_info import UnkeyedValidPathInfo as SerdeUnkeyedValidPathInfo
        from pynixd.serde.signature import Signature
        from pynixd.serde.valid_path_info import ValidPathInfo as SerdeValidPathInfo
        from pynixd.serde.wire_time import Time

        from .queries import QUERY_CLOSURE_WITH_INFO

        seeds_json = json.dumps([str(p) for p in request.paths])
        async with self.db.execute(QUERY_CLOSURE_WITH_INFO, (seeds_json,)) as cursor:
            rows = await cursor.fetchall()

        sorted_infos: list = []
        for path, deriver, nar_hash, reg_time, nar_size, ultimate, sigs, ca, refs_str in rows:
            sp = SerdeStorePath(path=path)
            references: set = {SerdeStorePath(path=r) for r in refs_str.split()} if refs_str else set()  # type: ignore[arg-type]
            sig_set: set = set()
            if sigs:
                for s in sigs.split():
                    sig_set.add(Signature(**Signature.from_str(s)))
            uinfo = SerdeUnkeyedValidPathInfo(
                deriver=SerdeStorePath(path=deriver or ""),
                nar_hash=NARHash(hash=nar_hash),
                references=references,
                registration_time=Time(ts=reg_time),
                nar_size=nar_size or 0,
                ultimate=bool(ultimate),
                sigs=sig_set,
                ca=ContentAddress(value=ca or ""),
            )
            sorted_infos.append(SerdeValidPathInfo(path=sp, info=uinfo))

        return QueryClosureWithInfoResponse(infos=sorted_infos)

    async def query_path_infos(self, request: Any, client: Any = None, suppress_last: bool = False) -> Any:
        """QueryPathInfos — batch path info query via SQLite with per-path cache check."""

        if not request.paths:
            from pynixd.serde import QueryPathInfosResponse

            return QueryPathInfosResponse(infos=[])

        cached: dict = {}
        uncached: list = []
        for path in request.paths:
            cached_info = self.get_path_info(path)
            if cached_info is not None:
                cached[path] = cached_info
            else:
                uncached.append(path)

        if not uncached:
            from pynixd.serde import QueryPathInfosResponse

            return QueryPathInfosResponse(infos=list(cached.values()))

        import json

        from pynixd.serde import QueryPathInfosResponse, StorePath as SerdeStorePath
        from pynixd.serde.content_address import ContentAddress
        from pynixd.serde.nar_hash import NARHash
        from pynixd.serde.path_info import UnkeyedValidPathInfo as SerdeUnkeyedValidPathInfo
        from pynixd.serde.signature import Signature
        from pynixd.serde.valid_path_info import ValidPathInfo as SerdeValidPathInfo
        from pynixd.serde.wire_time import Time

        from .queries import QUERY_PATH_INFOS_BATCH, QUERY_REFERENCES_BATCH

        paths_json = json.dumps([str(p) for p in uncached])
        async with self.db.execute(QUERY_PATH_INFOS_BATCH, (paths_json,)) as cursor:
            rows = await cursor.fetchall()
        async with self.db.execute(QUERY_REFERENCES_BATCH, (paths_json,)) as cursor:
            ref_rows = await cursor.fetchall()

        refs_map: dict = {}
        for referrer, reference in ref_rows:
            refs_map.setdefault(SerdeStorePath(path=referrer), set()).add(  # type: ignore[arg-type]
                SerdeStorePath(path=reference),
            )

        infos: list = []
        for path, deriver, nar_hash, reg_time, nar_size, ultimate, sigs, ca in rows:
            sp = SerdeStorePath(path=path)
            sig_set: set = set()
            if sigs:
                for s in sigs.split():
                    sig_set.add(Signature(**Signature.from_str(s)))
            uinfo = SerdeUnkeyedValidPathInfo(
                deriver=SerdeStorePath(path=deriver or ""),
                nar_hash=NARHash(hash=nar_hash),
                references=refs_map.get(sp, set()),
                registration_time=Time(ts=reg_time),
                nar_size=nar_size or 0,
                ultimate=bool(ultimate),
                sigs=sig_set,
                ca=ContentAddress(value=ca or ""),
            )
            infos.append(SerdeValidPathInfo(path=sp, info=uinfo))

        return QueryPathInfosResponse(infos=[*cached.values(), *infos])

    async def query_derivation_output_map_batch(
        self, request: Any, client: Any = None, suppress_last: bool = False
    ) -> Any:
        """QueryDerivationOutputMapBatch — batch output map via SQLite, fallback to drv parse."""

        if not request.drv_paths:
            from pynixd.serde.query_derivation_output_map_batch import DerivationOutputMapBatchResponse

            return DerivationOutputMapBatchResponse(outputs={})

        import json

        from pynixd.serde import StorePath as SerdeStorePath
        from pynixd.serde.query_derivation_output_map_batch import DerivationOutputMapBatchResponse

        from .queries import QUERY_DERIVATION_OUTPUT_MAP_BATCH

        paths_json = json.dumps([str(p) for p in request.drv_paths])
        async with self.db.execute(QUERY_DERIVATION_OUTPUT_MAP_BATCH, (paths_json,)) as cursor:
            rows = await cursor.fetchall()

        result: dict = {}
        for drv_path, output_name, output_path in rows:
            sp = SerdeStorePath(path=drv_path)
            val: SerdeStorePath | None = SerdeStorePath(path=output_path) if output_path else None
            result.setdefault(sp, {})[output_name] = val

        for drv_path in request.drv_paths:
            sp = SerdeStorePath(path=str(drv_path))
            if sp in result:
                continue
            try:
                parsed = await self.read_derivation(drv_path)
                if parsed is None:
                    continue
                result[sp] = dict(parsed.output_paths().items())
            except FileNotFoundError:
                pass

        return DerivationOutputMapBatchResponse(outputs=result)
