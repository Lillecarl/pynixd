"""DerivedPath coordinator goal."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import TYPE_CHECKING

import structlog

from nix_daemon_protocol.exceptions import DaemonProtocolError

from ..derived_path import DerivedPath, OutputsNames
from ..drv_hash import output_hashes
from ..drv_parser import ChildMapNode, to_basic_derivation
from ..serde import (
    BuildDerivationRequest,
    BuildPathsRequest,
    BuildResult,
    BuildResultStatus,
    DerivedPath as SerdeDerivedPath,
    DrvOutput,
    EnsurePathRequest,
    IsValidPathRequest,
    KeyedDrvOutput,
    LogNext,
    Realisation,
    RegisterDrvOutputRequest,
    StorePath as SerdeStorePath,
    UnkeyedRealisation,
)
from ..store_path import StorePath
from .dependencies import DependencyGroupGoal
from .goal import GoalHolder
from .query_missing import client_names_a_substituter
from .realisations import realisations_of
from .resolution import _nix_drv_name, resolve_derivation, resolve_dynamic_derivation, unparse_basic_derivation
from .results import GoalResult, goal_failure, goal_success, result_succeeded

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from ..connection import ClientConn
    from ..drv_parser import Derivation
    from ..serde import BasicDerivation
    from .build_derivation import BuildDerivationGoal
    from .engine import GoalEngine
    from .results import DynamicPathMap

log = structlog.get_logger(__name__)


_ERROR_PREFIX = "\x1b[31;1merror:\x1b[0m "
"""The word that `showErrorInfo` of Nix writes in front of a failure.

The daemon colours the word itself, and it does not ask whether the client
wants colour. The wire parity run reads these bytes from `nix-daemon`.
"""


def _as_an_error(message: str) -> str:
    """Give *message* the shape that `showErrorInfo` of Nix gives it.

    `TunnelLogger::logEI` at `daemon.cc` formats the failure of a goal and
    sends the text as one `STDERR_NEXT`. Each line after the first carries
    seven spaces, which is the width of "error: ". The text carries no line
    feed at the end, because the client adds one.
    """
    return _ERROR_PREFIX + "\n       ".join(message.split("\n"))


@dataclass
class EnsureDerivedPathGoal(GoalHolder[GoalResult]):
    """Coordinate the production of a derived path via substitution or build."""

    engine: GoalEngine
    derived_path: DerivedPath
    build_mode: int
    substituter_ids: tuple[str, ...]
    _subscribers: list[ClientConn] = field(default_factory=list)
    _watchers: list[ClientConn] = field(default_factory=list)
    """Every client that ever subscribed, for a line that this goal writes.

    `_subscribers` holds the clients that the build goal has not taken yet,
    and it becomes empty as soon as the build starts. This one keeps them, so
    `_say` reaches the client after the build as well as before it.
    """

    _build_goal: BuildDerivationGoal | None = None

    _wanted_by_a_goal: bool = False
    """True when another goal waits for this one.

    `Goal::amDone` at `goal.cc:214` of Nix writes the failure of a goal as an
    error message when `waiters` is not empty. A goal at the top of the
    request writes no such line, because the caller reports that one through
    `STDERR_ERROR`. This flag is the `waiters` of Nix, reduced to the single
    question that the rule asks.
    """

    def note_a_parent(self) -> None:
        """Record that another goal waits for this one."""
        self._wanted_by_a_goal = True

    def __post_init__(self) -> None:
        """Initialize the GoalHolder base with the shared engine."""
        GoalHolder.__init__(self, self.engine)

    async def subscribe(self, client: ClientConn | None) -> None:
        """Register a client for real-time log forwarding from the underlying build goal."""
        if client is None:
            return
        async with self._lock:
            self._watchers.append(client)
            build_goal = self._build_goal
            if build_goal is None:
                self._subscribers.append(client)
                return
        await build_goal.subscribe(client)

    async def _run(self) -> GoalResult:
        result = await self._produce()
        return await self._tell_the_client_it_failed(result)

    async def _produce(self) -> GoalResult:
        if self.derived_path.is_opaque:
            return await self._ensure_opaque()
        if self.derived_path.is_nested:
            return await self._ensure_nested()
        return await self._ensure_flat_derivation()

    async def _tell_the_client_it_failed(self, result: GoalResult) -> GoalResult:
        """Write the failure of a child goal as an error message.

        Nix writes one `error:` block for each goal that failed and had a goal
        waiting for it, and one more for the goal at the top of the request.
        A client that asked for a derivation with a failing input therefore
        reads the reason of the input first, and "1 dependency failed" after
        it. pynixd wrote the second line alone, so the reason was lost.

        Issue #188 holds the measurement, and `main:build-remote` reads both
        blocks.

        NIX-DEFECT (#191): Nix takes this decision from the shape of the goal
        graph, and the shape is the wrong question. `waiters` at `goal.cc:214`
        answers "does another goal wait for me", and the reporting really asks
        "did the client learn this already". The two answers differ for a
        derivation that the client names *and* another derivation depends on:
        `waiters` is not empty, so `amDone` writes the block, and the caller
        of `buildPaths` then throws the same failure and the client writes it
        again. Nix cannot separate the two questions, because one goal graph
        carries both the dependency edges and the request. pynixd can carry
        the two apart, and it does not, because the wire parity run compares
        the bytes of the two daemons.
        """
        if not self._wanted_by_a_goal or result_succeeded(result.result):
            return result
        detail = str(result.result.error_msg)
        if detail:
            # One line for each block this writes, so a run can count them.
            # `main:build` reads the number of `error:` lines of the client,
            # and a block that goes out twice is invisible without this.
            log.debug("told_the_client_the_reason", derived_path=str(self.derived_path), detail=detail[:80])
            await self._say(_as_an_error(detail))
        return result

    async def _ensure_opaque(self) -> GoalResult:
        store_path = StorePath(self.derived_path.drv_path)
        path = SerdeStorePath(path=self.derived_path.drv_path)
        response = await self.engine.ctx.local_store.execute(IsValidPathRequest(path=path))
        if response.valid:
            return self._opaque_success(store_path)

        # A path a backend built is available through pynixd, and it is not in
        # the local store. `NarFromPathHandler` and `DaemonProxy` both read it
        # from the backend that holds it, so a client that asks for it gets it.
        #
        # Only the local store was asked here until issue #160. A fleet build
        # therefore succeeded, recorded its outputs in `ctx.output_locations`,
        # and then failed the very next request for those outputs with "opaque
        # path is not valid locally". `nix copy` asks this way: it realises its
        # installables against the source store first, and a store path
        # installable becomes an opaque derived path.
        backend = self.engine.ctx.store_for_output_path(str(store_path))
        if backend is not None:
            log.debug(
                "opaque_path_resident_on_backend",
                path=str(store_path),
                store_id=backend.store_id,
            )
            return self._opaque_success(store_path)

        substitute = await self._try_substitute_path(store_path)
        if substitute is not None:
            return substitute
        return goal_failure(f"pynixd: opaque path is not valid locally: {self.derived_path}", BuildResultStatus.UNKNOWN)

    @staticmethod
    def _opaque_success(store_path: StorePath) -> GoalResult:
        """An opaque path needs no build, so the path itself is the one output."""
        return GoalResult(
            result=goal_success().result,
            resolved_outputs={"out": store_path},
            produced_paths={store_path},
        )

    async def _ensure_nested(self) -> GoalResult:
        outer_goal = await self.engine.get_ensure_derived_path_goal(
            self.derived_path.outer,
            self.build_mode,
            self.substituter_ids,
        )
        outer_goal.note_a_parent()
        await outer_goal.subscribe_many(self._subscribers)
        outer_result = await self.run_child(outer_goal)

        chain_output = self.derived_path.chain[-1]
        inner_drv = outer_result.resolved_outputs.get(chain_output)
        if inner_drv is None:
            # `DerivationTrampolineGoal` at `derivation-trampoline-goal.cc:107`
            # answers this when the goal that makes the derivation failed. The
            # reason itself reaches the client from that goal, which writes it
            # as an error message. `dyn-drv:failing-outer` reads the sentence,
            # and pynixd gave it an internal note with a `!` in the path.
            if not result_succeeded(outer_result.result):
                return goal_failure(
                    f"failed to obtain derivation of '{self.derived_path.outer.to_string()}'",
                    BuildResultStatus.DEPENDENCY_FAILED,
                )
            return goal_failure(
                f"pynixd: nested derived path did not produce {chain_output}: {self.derived_path}",
                BuildResultStatus.UNKNOWN,
            )
        if not inner_drv.is_derivation():
            return outer_result

        wrapped = self.derived_path.wrap(inner_drv)
        remainder_goal = await self.engine.get_ensure_derived_path_goal(wrapped, self.build_mode, self.substituter_ids)
        remainder_goal.note_a_parent()
        await remainder_goal.subscribe_many(self._subscribers)
        result = await self.run_child(remainder_goal)

        nested_result = result.with_dynamic_outputs(self.derived_path.base_store_path())
        nested_result.produced_paths.add(inner_drv)
        return nested_result

    async def _ensure_flat_derivation(self) -> GoalResult:
        drv_path = SerdeStorePath(path=self.derived_path.drv_path)
        parsed = await self.engine.ctx.local_store.read_derivation(str(drv_path))
        if parsed is None:
            return goal_failure(f"pynixd: derivation not found: {drv_path}", BuildResultStatus.UNKNOWN)

        requested_outputs = self.derived_path.output_names
        if requested_outputs == {"*"}:
            selected_outputs = {output.name for output in parsed.outputs}
        else:
            selected_outputs = requested_outputs

        known_outputs = {output.name for output in parsed.outputs}
        missing_outputs = selected_outputs - known_outputs
        if missing_outputs:
            return goal_failure(
                f"pynixd: derivation {drv_path} does not define outputs: {', '.join(sorted(missing_outputs))}",
                BuildResultStatus.UNKNOWN,
            )

        early_outputs = {name: path for name, path in parsed.output_paths().items() if name in selected_outputs}
        substituted = await self._try_substitute_known_outputs(early_outputs)
        if substituted is not None:
            log.debug("ensure_derivation_substituted", drv_path=str(drv_path), outputs=sorted(selected_outputs))
            substituted = await self._say_what_it_produced(substituted, parsed, selected_outputs)
            return substituted.with_dynamic_outputs(self.derived_path.base_store_path())

        realised = await self._already_realised(parsed, selected_outputs)
        if realised is not None:
            log.debug("ensure_derivation_already_realised", drv_path=str(drv_path), outputs=sorted(selected_outputs))
            return realised.with_dynamic_outputs(self.derived_path.base_store_path())

        fetched = await self._try_realise_upstream(parsed, selected_outputs, drv_path)
        if fetched is not None:
            log.debug("ensure_derivation_realised_upstream", drv_path=str(drv_path))
            return fetched.with_dynamic_outputs(self.derived_path.base_store_path())

        refusal = await self._refuse_an_impure_input(parsed, drv_path)
        if refusal is not None:
            return refusal

        child_results = await self._realise_input_derivations(parsed)
        failed_inputs = await self._refuse_a_failed_input(child_results, drv_path)
        if failed_inputs is not None:
            return failed_inputs

        store_path = Path(self.engine.ctx.local_store.store_path)
        dynamic_paths = {}
        for result in child_results:
            dynamic_paths.update(result.dynamic_paths)

        # **A derivation with an input derivation is resolved, and never
        # merely flattened.** `to_basic_derivation` moves the output path of
        # each input into `inputSrcs` and rewrites nothing else, so a
        # `DownstreamPlaceholder` in the build command stayed as it was and
        # the builder read it as a path. `Derivation::tryResolve` of Nix does
        # both, and this takes the same decision for every derivation rather
        # than for the kinds of output that a predicate lists. That predicate
        # was wrong twice: it missed a floating content-addressed output
        # (#183), and it missed a fixed-output derivation with a
        # content-addressed input, which carries a placeholder as well.
        domain_drv_path = StorePath(str(drv_path))
        if parsed.dynamic_input_drvs and dynamic_paths:
            basic = resolve_dynamic_derivation(parsed, domain_drv_path, dynamic_paths)
        elif parsed.input_drvs:
            basic = resolve_derivation(parsed, domain_drv_path, await self._input_paths(parsed, dynamic_paths))
        else:
            basic = await to_basic_derivation(parsed, store_path)

        # **Every output of the derivation goes on the wire, and the wanted
        # ones alone do not.** `BuildDerivation` carries no set of wanted
        # outputs, so the daemon builds each output that the derivation names.
        # It also rewrites `builtins.placeholder <name>` for each of those
        # names, at `derivation-builder.cc:802` of Nix. A derivation that lost
        # an output therefore reached the builder with the placeholder of that
        # output unrewritten, and the builder read a path that is not there.
        # The name of the derivation is also the dedup key of the build, so a
        # request for `drv^out` and a request for `drv^bin` made two builds of
        # one derivation. Issue #178.
        selected_paths = {name: path for name, path in basic.output_paths().items() if name in selected_outputs}
        substituted = await self._try_substitute_known_outputs(selected_paths)
        if substituted is not None:
            log.debug("ensure_derivation_substituted", drv_path=str(drv_path), outputs=sorted(selected_outputs))
            substituted = await self._say_what_it_produced(substituted, parsed, selected_outputs)
            return substituted.with_dynamic_outputs(self.derived_path.base_store_path())

        build_drv_path = await self._path_of_what_it_builds(basic, drv_path) if parsed.should_resolve else drv_path
        request = BuildDerivationRequest(
            drv_path=build_drv_path,
            derivation=basic,
            build_mode=self.build_mode,
        )
        build_goal = await self.engine.get_build_derivation_goal(request)
        async with self._lock:
            self._build_goal = build_goal
            subscribers = list(self._subscribers)
            self._subscribers.clear()
        for client in subscribers:
            await build_goal.subscribe(client)
        result = await self.run_child(build_goal)
        result = await self._name_the_resolved_derivation(result, drv_path, build_drv_path)
        result = result.with_dynamic_outputs(self.derived_path.base_store_path())
        result = await self._under_the_original_id(result, parsed)
        result.result = _only_wanted_outputs(result.result, selected_outputs)
        return result

    async def _name_the_resolved_derivation(
        self,
        result: GoalResult,
        original: SerdeStorePath,
        built: SerdeStorePath,
    ) -> GoalResult:
        """The failure of a resolved build names the resolved derivation.

        Nix says two things when the build of a resolved derivation fails, and
        pynixd said neither.

        The goal of the resolved derivation has a goal that waits for it, so
        `Goal::amDone` at `goal.cc:214` writes the whole detail as an error
        message. A goal at the top of the request gets no such line, because
        the caller reports that one.

        `DerivationGoal` at `derivation-goal.cc:247` then answers "build of
        resolved derivation '%s' failed", and that short sentence is the
        result. `dyn-drv:failing-outer` reads it.
        """
        if str(original) == str(built) or result_succeeded(result.result):
            return result
        detail = str(result.result.error_msg)
        if detail:
            # The second site that writes a failure block, beside
            # `_tell_the_client_it_failed`. Counted for the same reason:
            # `main:build` reads the number of `error:` lines of the client.
            log.debug("told_the_client_the_resolved_reason", original=str(original), built=str(built))
            await self._say(_as_an_error(detail))
        return GoalResult(
            result=result.result.model_copy(
                update={"error_msg": f"build of resolved derivation '{built}' failed"},
            ),
            resolved_outputs=result.resolved_outputs,
            produced_paths=result.produced_paths,
            dynamic_paths=result.dynamic_paths,
        )

    async def _already_realised(self, parsed: Derivation, wanted: set[str]) -> GoalResult | None:
        """The realisation of each wanted output, when the store holds one for all of them.

        **A derivation that names no output path is not therefore unbuilt.**
        A floating content-addressed output takes its path from what the build
        makes, so the derivation cannot name it, and a deferred output cannot
        name it either. The store answers instead: a realisation maps
        `DrvOutput{staticOutputHashes(drv)[name], name}` to the path.

        `DerivationGoal::checkPathValidity` at `derivation-goal.cc:405` reads
        it there, and a valid path makes the goal `AlreadyValid`. pynixd read
        the derivation alone, so it found no path and built the derivation
        again. `ca:build` sees it in `testGC`, which builds with `-j0` after a
        garbage collection and expects the rooted output to answer.

        Answers `None` when any wanted output names a path already, or when
        the store holds no realisation for one of them. The caller then goes
        on to the inputs and the build. Issue #185.
        """
        paths_of_drv = parsed.output_paths()
        if not wanted or any(str(paths_of_drv.get(name, "")) for name in wanted):
            return None
        if parsed.is_impure:
            return None
        built = await realisations_of(parsed, wanted, self.engine.ctx.local_store, self.derived_path.base_store_path())
        if built is None:
            return None

        resolved_outputs: dict[str, StorePath] = {}
        for key, realisation in built.items():
            path = StorePath(str(realisation.out_path))
            valid = await self.engine.ctx.local_store.execute(IsValidPathRequest(path=SerdeStorePath(path=str(path))))
            if not valid.valid:
                return None
            resolved_outputs[key.rpartition("!")[2]] = path

        answer = goal_success()
        answer.resolved_outputs = resolved_outputs
        answer.produced_paths = set(resolved_outputs.values())
        answer.result = answer.result.model_copy(update={"built_outputs": built})
        return answer

    async def _try_realise_upstream(
        self,
        parsed: Derivation,
        wanted: set[str],
        drv_path: SerdeStorePath,
    ) -> GoalResult | None:
        """Let the daemon behind pynixd substitute a content-addressed output.

        **A content-addressed output has no store path to substitute by.** The
        derivation names none, so `_try_substitute_known_outputs` has nothing
        to pass and `EnsurePath` upstream has nothing to take. The path exists
        only in a realisation, and the realisation lives in the cache that the
        client named. `_already_realised` asks the store for one and finds
        none, so the goal took the build road. Issue #198 measured what that
        costs: `--max-jobs 0` says "substitute this, do not build it", pynixd
        built anyway, and a build makes **every** output, so
        `use-a-more-outputs^first` also produced `second`.

        This is the road that was missing. `BuildPaths` for the derived path
        goes to the daemon behind pynixd, with the option set of the client,
        so that daemon applies `substituters`, `max-jobs` and
        `require-sigs` and runs its own `DrvOutputSubstitutionGoal`. That
        goal is the one thing that reads a realisation out of a substituter,
        and pynixd has no such client of its own. `_try_substitute_upstream`
        takes the same decision for a path that is already known, and states
        the same reason. Issues #187, #195 and #198.

        It runs only for a client that named a substituter, and only for a
        derivation whose wanted outputs name no path. A derivation that names
        its paths has the older road, and a client that named no substituter
        gains nothing from asking.

        **A failure here is not a failure of the goal.** The daemon answers an
        error when no substituter holds the realisation, and that is the
        ordinary answer for a derivation the client must build. The caller
        goes on to the inputs and the build.
        """
        if not wanted or parsed.is_impure:
            return None
        paths_of_drv = parsed.output_paths()
        if any(str(paths_of_drv.get(name, "")) for name in wanted):
            return None
        client = next((c for c in self._watchers if c.options is not None), None)
        if not client_names_a_substituter(client):
            return None

        request = BuildPathsRequest(
            derived_paths=[SerdeDerivedPath(value=str(self.derived_path))],
            build_mode=int(self.build_mode),
        )
        try:
            await self.engine.ctx.local_store.execute(request, client=client)
        except (DaemonProtocolError, OSError, EOFError) as ex:
            # An upstream miss is the normal answer for a derivation that the
            # client must build, and a broken upstream connection must not end
            # the goal either: the build road is still there. Issue #195 holds
            # what an escape from here costs.
            log.debug("upstream_realise_miss", drv_path=str(drv_path), reason=str(ex))
            return None

        # The daemon holds the realisation now, so the ordinary reader finds
        # it. This does not trust the answer of the daemon on its own: that
        # method checks the path as well.
        return await self._already_realised(parsed, wanted)

    async def _under_the_original_id(self, result: GoalResult, parsed: Derivation) -> GoalResult:
        """Give each realisation the id that the original derivation makes, and register it.

        pynixd resolves a derivation before it sends it, so the daemon builds
        a different ATerm. `staticOutputHashes` of the daemon therefore answers
        a different hash, and the daemon registers each realisation under that
        hash. The client holds the original derivation and queries
        `DrvOutput{staticOutputHashes(original)[name], name}`, at
        `Store::queryPartialDerivationOutputMap` in `store-api.cc:406`. It
        found no realisation, and `nix-build.cc:730` and `built-path.cc:122`
        both stop the program with an assertion.

        Nix makes the same correction. `DerivationGoal` builds the resolved
        derivation and then re-registers each output under the hash of the
        original one, at `derivation-goal.cc:193-236`. The signatures go,
        because a signature covers the id. Issue #182.

        This goal makes the correction, and the build goal does not, because
        this goal is the one that holds the original derivation. Issue #184
        gave the build goal the path of the resolved derivation, so the build
        goal can no longer read the original one. A build goal is also shared
        between the clients that ask for it, and each one holds its own
        original derivation.

        NIX-DEFECT (#191): the client of Nix answers a missing realisation
        with `abort`. `nix-build.cc:730` asserts the output path, and
        `built-path.cc:122` asserts it again, so a store that registered the
        realisation under another id stops the program with SIGABRT and no
        message. A missing realisation is a state that a store can reach, and
        a client that reads a store over a socket cannot trust the store to
        agree with it. pynixd cannot correct the client, so it must never
        leave that state, and this method is the whole reason.
        """
        built = result.result.built_outputs
        if not built:
            return result

        hashes = await output_hashes(parsed, self.engine.ctx.local_store.read_derivation)
        answer: dict[str, Realisation] = {}
        changed = False
        for key, realisation in built.items():
            output_name = _realisation_output_name(key, realisation)
            digest = None if hashes is None else hashes.get(output_name)
            wanted = f"sha256:{digest}!{output_name}"
            if digest is None or wanted == key:
                answer[key] = realisation
                continue
            changed = True
            answer[wanted] = realisation.model_copy(update={"id": DrvOutput(wanted), "signatures": []})
            log.debug(
                "realisation_rekeyed",
                drv_path=self.derived_path.drv_path,
                output=output_name,
                sent=key,
                original=wanted,
            )

        if parsed.needs_realisations:
            await self._register_realisations(answer.values())
        if changed:
            result.result = result.result.model_copy(update={"built_outputs": answer})
        return result

    async def _register_realisations(self, realisations: Iterable[Realisation]) -> None:
        """Put each realisation in the local store, under the id it now carries."""
        for realisation in realisations:
            if realisation.out_path is None:
                continue
            # `Realisation` carries the bare `<hash>-<name>`, which is
            # `StorePath::to_string` of Nix. `StorePath` of pynixd puts the
            # store directory in front of it again, and `IsValidPath` needs
            # the whole path.
            out_path = StorePath(str(realisation.out_path))
            valid = await self.engine.ctx.local_store.execute(
                IsValidPathRequest(path=SerdeStorePath(path=str(out_path))),
            )
            if not valid.valid:
                continue
            try:
                # **Both shapes go in, and the codec writes the one the peers
                # agreed on.** `realisation-with-path-not-hash` decides
                # whether the wire carries one JSON string or a derivation
                # path, an output name, an output path and the signatures.
                # `needs_features` and `unless_features` on the fields pick
                # one and drop the other, so this code needs no branch of its
                # own. Issue #162.
                await self.engine.ctx.local_store.execute(
                    RegisterDrvOutputRequest(
                        realisation=realisation,
                        keyed_drv_output=KeyedDrvOutput(
                            drv_path=SerdeStorePath(path=str(self.derived_path.base_store_path())),
                            output_name=realisation.id.output_name,
                        ),
                        unkeyed_realisation=UnkeyedRealisation(
                            out_path=realisation.out_path,
                            signatures=set(realisation.signatures),
                        ),
                    ),
                )
            except Exception:
                log.warning("register_drv_output_failed", drv_output=str(realisation.id), exc_info=True)

    async def _path_of_what_it_builds(
        self,
        basic: BasicDerivation,
        original: SerdeStorePath,
    ) -> SerdeStorePath:
        """Put the resolved derivation in the store, and answer its path.

        **The daemon reads the derivation on the disk, and not the one that
        `BuildDerivation` carries.** `queryPartialDerivationOutputMap` at
        `derivation-building-goal.cc:1239` takes the store copy whenever
        `drvPath` is valid there, and the client put the original derivation
        in the store when it instantiated it. So a resolved derivation sent
        under the original path answered every question about its outputs from
        the unresolved one: a deferred output had no path, the builder got a
        fallback scratch path, and the build failed with "failed to produce
        output path".

        Nix writes the resolved derivation to the store and builds that path,
        at `derivation-resolution-goal.cc`. This does the same. Issue #184.

        A store that cannot take a text file keeps the original path, which is
        what pynixd did before.
        """
        name = f"{_nix_drv_name(StorePath(str(original)))}.drv"
        text = unparse_basic_derivation(basic)
        references = {str(path) for path in basic.input_srcs}
        try:
            path = await self.engine.ctx.local_store.add_text_to_store(name, text, references)
        except Exception:
            log.warning("resolved_derivation_not_stored", drv_path=str(original), exc_info=True)
            return original
        log.debug("resolved_derivation_stored", original=str(original), resolved=path)
        # **The client reads which derivation the daemon really builds.**
        # `DerivationResolutionGoal` starts an activity with this text at
        # `derivation-resolution-goal.cc:150`, and the plain logger of the
        # client prints the text of an activity with three points after it.
        await self._say(f"resolved derivation: '{original}' -> '{path}'...\n")
        return SerdeStorePath(path=path)

    async def _say(self, text: str) -> None:
        """Send one line to each client that watches this goal."""
        async with self._lock:
            watchers = list(self._watchers)
        for client in watchers:
            await client.send(LogNext(text=text))

    async def _refuse_an_impure_input(self, parsed: Derivation, drv_path: SerdeStorePath) -> GoalResult | None:
        """A pure derivation cannot depend on an impure one.

        `DerivationResolutionGoal::init` at `derivation-resolution-goal.cc:67`
        reads each input derivation and raises before it builds any of them.
        The message is the one below, and `impure-derivations.sh:50` of the
        functional suite greps for it.

        An impure derivation may depend on an impure one, and so may a
        fixed-output derivation. Neither one takes its output path from the
        hash of its inputs, so an input that changes every build changes
        nothing about it.

        Nix guards the check with the `impure-derivations` feature, and pynixd
        cannot ask the setting. It needs no guard: an impure derivation exists
        only when the feature is on.
        """
        if parsed.is_impure or parsed.is_fixed_output:
            return None
        for input_drv_path in parsed.input_drvs:
            given = await self.engine.ctx.local_store.read_derivation(input_drv_path)
            if given is not None and given.is_impure:
                return goal_failure(
                    f"pure derivation '{drv_path}' depends on impure derivation '{input_drv_path}'",
                    BuildResultStatus.UNKNOWN,
                )
        return None

    async def _input_paths(self, parsed: Derivation, dynamic_paths: DynamicPathMap) -> DynamicPathMap:
        """Name the store path of each output of each input derivation.

        `resolve_derivation` needs one path for each of them, and a child goal
        gives a path only for the outputs that it built.
        `_input_outputs_requiring_goals` starts no goal for an output that is
        valid already, and a content-addressed input has no path in its own
        derivation. So neither source answers alone.

        A derivation that is not in the store answers with its own path, which
        is what `to_basic_derivation` did before this. That path is not the
        output, and no build works from it, but `inputSrcs` is not empty and
        the error that the daemon gives names the derivation. An output that
        the derivation reads but does not name gets nothing, and
        `resolve_derivation` then says which one.
        """
        answer: DynamicPathMap = {}
        for input_drv_path, output_names in parsed.input_drvs.items():
            drv_path = StorePath(input_drv_path)
            static: dict[str, StorePath] | None = None
            for output_name in output_names:
                key = (drv_path, output_name)
                built = dynamic_paths.get(key)
                if built is not None:
                    answer[key] = built
                    continue
                if static is None:
                    input_parsed = await self.engine.ctx.local_store.read_derivation(str(drv_path))
                    if input_parsed is None:
                        log.warning("input_derivation_not_found", drv_path=str(drv_path))
                        static = dict.fromkeys(output_names, drv_path)
                    else:
                        static = input_parsed.output_paths()
                path = static.get(output_name)
                if path:
                    answer[key] = path
        return answer

    async def _refuse_a_failed_input(
        self,
        child_results: list[GoalResult],
        drv_path: SerdeStorePath,
    ) -> GoalResult | None:
        """Answer a failure when an input derivation did not build.

        **A derivation with a failed input is not built.**
        `DerivationBuildingGoal::inputsRealised` of Nix counts the failed
        dependencies and fails the goal, and it starts no builder.

        pynixd read the results of its input goals for their dynamic paths
        alone and then went on. Two things followed from that, and the
        recorded output of `main:build` holds both. `resolve_derivation` used
        the output path of an input that nothing built, so `AddToStore` of the
        resolved derivation failed with "path '...-x3' is not valid" and
        `_path_of_what_it_builds` wrote `resolved_derivation_not_stored`. The
        build request then went to the daemon, which refused it with "Cannot
        build '...-x4.drv'. Reason: 2 dependencies failed."

        The answer was therefore right and the road to it was wrong: pynixd
        asked a daemon to tell it something it already knew. Issue #196.

        **The number in the message is not the number of inputs that failed.**
        `Goal::amDone` at `goal.cc:242` gives the rule: the first waitee that
        fails increments `nrFailed`, and when `keep-going` is off the same
        branch drops every waitee that is left. The goal then wakes with
        `nrFailed == 1`, whatever number of inputs would have failed. With
        `keep-going` on no waitee is dropped, and the number is the whole
        count.

        `main:build` measured it. `nix build -f fod-failing.nix -L x4` builds
        x2 and x3, both give a hash mismatch, and `build.sh:196` asserts
        "Reason: 1 dependency failed." pynixd counted both and wrote 2.

        pynixd waits for each input goal and Nix stops waiting at the first
        failure. The message is the same, and the timing is not: a second
        input that is still building holds this goal until it ends. Nix does
        not cancel that build either -- it only removes the edge -- so no
        client sees a different set of builds, and a slow second input costs
        pynixd the wait that Nix saves.
        """
        failed = [result for result in child_results if not result_succeeded(result.result)]
        if not failed:
            return None
        client = next((c for c in self._watchers if c.options is not None), None)
        keep_going = bool(client.options.keep_going) if client is not None and client.options is not None else False
        # The shape that `_build_failure_message` of `goals/engine.py` uses
        # for `BuildPaths`. The child that failed has written its own reason
        # already, through `_tell_the_client_it_failed`, so this names the
        # count and does not repeat the text.
        count = len(failed) if keep_going else 1
        log.debug(
            "input_derivation_failed",
            drv_path=str(drv_path),
            failed=len(failed),
            reported=count,
            keep_going=keep_going,
        )
        dependency = "dependency" if count == 1 else "dependencies"
        return goal_failure(
            f"Cannot build '{drv_path}'.\n       Reason: {count} {dependency} failed.",
            BuildResultStatus.DEPENDENCY_FAILED,
        )

    async def _realise_input_derivations(self, parsed) -> list[GoalResult]:
        child_goals: list[EnsureDerivedPathGoal] = []
        for input_drv_path, output_names in parsed.input_drvs.items():
            needed_outputs = await self._input_outputs_requiring_goals(
                StorePath(input_drv_path),
                output_names,
            )
            child_goals.extend(
                [
                    await self._child_goal(StorePath(input_drv_path), output_name)
                    for output_name in output_names
                    if output_name in needed_outputs
                ]
            )

        child_goals.extend(
            [
                await self.engine.get_ensure_derived_path_goal(child_dp, self.build_mode, self.substituter_ids)
                for input_drv_path, node in parsed.dynamic_input_drvs.items()
                for child_dp in _child_map_to_derived_paths(StorePath(input_drv_path), node)
            ]
        )

        if not child_goals:
            return []

        # **The client watches the build of each input as well.** Nix sends
        # `building '<input>.drv'...` and, when the input fails, `Cannot build
        # '<input>.drv'. Reason: builder failed with exit code N.` A client of
        # pynixd saw neither: it read "1 dependency failed" for the derivation
        # it asked for, and nothing at all about the dependency that failed.
        # `_ensure_nested` already does this for the next level of a dynamic
        # derivation.
        async with self._lock:
            subscribers = list(self._subscribers)
        for goal in child_goals:
            goal.note_a_parent()
            await goal.subscribe_many(subscribers)

        return await self.run_child(DependencyGroupGoal(self.engine, child_goals))

    async def _input_outputs_requiring_goals(
        self,
        drv_path: StorePath,
        output_names: list[str],
    ) -> set[str]:
        parsed = await self.engine.ctx.local_store.read_derivation(str(drv_path))
        if parsed is None:
            return set(output_names)
        if parsed.builder.startswith("builtin:"):
            return set()
        paths = parsed.output_paths()
        needed: set[str] = set()
        for output_name in output_names:
            output_path = paths.get(output_name)
            if output_path is None:
                needed.add(output_name)
                continue
            response = await self.engine.ctx.local_store.execute(
                IsValidPathRequest(path=SerdeStorePath(path=str(output_path))),
            )
            if not response.valid:
                needed.add(output_name)
        return needed

    async def _child_goal(self, drv_path: StorePath, output_name: str) -> EnsureDerivedPathGoal:
        dp = DerivedPath._from_components(
            drv_path=drv_path,
            chain=(),
            outputs=OutputsNames(frozenset({output_name})),
        )
        return await self.engine.get_ensure_derived_path_goal(dp, self.build_mode, self.substituter_ids)

    async def _try_substitute_known_outputs(self, output_paths: Mapping[str, StorePath | None]) -> GoalResult | None:
        """Substitute each output whose path the derivation already names.

        **A content-addressed output has no path here, and the empty path is
        not a store path.** `output_paths` of `drv_parser.py` answers
        `StorePath("")` for such an output, because the name is what asks the
        store for a realisation. This method takes a path to the wire, so it
        must drop that entry.

        An empty path reached `EnsurePath` upstream before this filter, and
        the daemon then closed the connection with no error word:
        `daemon.cc:701` parses the store path **before**
        `logger->startWork()`, so `canSendStderr` is still false when
        `parseStorePath` throws, and `daemon.cc:1213` rethrows for that
        reason. The client of pynixd read the end of the file and reported
        `IncompleteReadError`. `ca:build-cache` and `ca:issue-13247` both
        failed that way. Issue #195.
        """
        selected = {name: path for name, path in output_paths.items() if path is not None and path.base()}
        if not selected:
            return None

        results: dict[str, GoalResult] = {}
        for output_name, path in selected.items():
            response = await self.engine.ctx.local_store.execute(
                IsValidPathRequest(path=SerdeStorePath(path=str(path)))
            )
            if response.valid:
                results[output_name] = GoalResult(
                    result=goal_success().result,
                    resolved_outputs={output_name: path},
                    produced_paths={path},
                )
                continue
            substituted = await self._try_substitute_path(path)
            if substituted is None:
                return None
            if not result_succeeded(substituted.result):
                return substituted
            results[output_name] = substituted.with_single_output(output_name, path)

        merged = goal_success()
        for output_name, result in results.items():
            merged.resolved_outputs[output_name] = selected[output_name]
            merged.produced_paths.update(result.produced_paths)
        return merged

    async def _say_what_it_produced(
        self,
        result: GoalResult,
        parsed: Derivation,
        wanted: set[str],
    ) -> GoalResult:
        """Put a realisation for each wanted output in the answer.

        A status alone does not name a path.
        `DerivationBuildingGoal::checkPathValidity` of Nix answers with one
        realisation for each output it found, and a client reads the output
        paths out of them. pynixd answered an already-valid derived path with
        an empty map, so `nix build --json` wrote no `outputs` key at all:
        `BuiltPath::Built::toJSON` writes that key once for each output.
        Issue #179.

        **The realisation also goes into the store.**
        `DerivationGoal::checkPathValidity` at `derivation-goal.cc:445` does
        that: the output path is valid, and no realisation names it, so it
        writes one. A client then reads the path back through
        `queryPartialDerivationOutputMap`. `ca:build` needs it, at the second
        build of `dependentNonCA`: that build is a cut-off, so nothing is
        built and this answer is the whole answer. Issue #184.
        """
        if result.result.built_outputs:
            return result
        hashes = await output_hashes(parsed, self.engine.ctx.local_store.read_derivation)
        if hashes is None:
            return result

        built: dict[str, Realisation] = {}
        for output_name, path in result.resolved_outputs.items():
            digest = hashes.get(output_name)
            if output_name not in wanted or digest is None:
                continue
            # `sha256:<hex>!<name>` is `DrvOutput::to_string` of Nix, and the
            # bare `<hash>-<name>` is `StorePath::to_string`, which is what
            # `Realisation` carries in its JSON.
            key = f"sha256:{digest}!{output_name}"
            built[key] = Realisation(id=key, out_path=SerdeStorePath(path=PurePath(str(path)).name))
        if not built:
            return result

        if parsed.needs_realisations:
            await self._register_realisations(built.values())

        answer = result.copy()
        answer.result = answer.result.model_copy(update={"built_outputs": built})
        return answer

    async def _try_substitute_path(self, path: StorePath) -> GoalResult | None:
        substitute_goal = await self.engine.get_substitute_path_goal(path, self.substituter_ids)
        attempt = await self.run_child(substitute_goal)
        if attempt.found:
            return attempt.result
        return await self._try_substitute_upstream(path)

    async def _try_substitute_upstream(self, path: StorePath) -> GoalResult | None:
        """Ask the daemon behind pynixd to fetch *path* from its substituters.

        **A client can name a substituter that pynixd has no store for.**
        `--substituters file:///...` is one. pynixd substitutes from its own
        backends, and it has no client for a binary cache of its own, so a
        path in such a cache reached the build road. The daemon behind pynixd
        speaks to every kind of substituter already.

        `Store::ensurePath` makes a substitution goal and nothing else, so
        this never starts a build. It runs only for a client that names a
        substituter; `client_names_a_substituter` in `goals/query_missing.py`
        states that rule, and the same rule keeps the plan and the work in
        agreement. Issue #187.

        **A failure here is not a failure of the goal.** `EnsurePath` answers
        an error when no substituter holds the path, and that answer is the
        normal one for a path that the client must build. The goal takes the
        build road after it, so this catches the error and answers `None`.

        **One goal serves many clients, and the first one with an option set
        wins.** pynixd shares a goal between the clients that ask for the same
        path, and each client has its own options. Nix has no such sharing, so
        it has no answer to copy. The first watcher is the client that made
        the goal, and `_run` of `goals/build_derivation.py` takes the same
        rule for a build. Issue #192 holds the question.
        """
        client = next((c for c in self._watchers if c.options is not None), None)
        if not client_names_a_substituter(client):
            return None
        wire_path = SerdeStorePath(path=str(path))
        try:
            await self.engine.ctx.local_store.execute(EnsurePathRequest(path=wire_path), client=client)
        except DaemonProtocolError as ex:
            log.debug("upstream_substitute_miss", path=str(path), reason=str(ex))
            return None
        response = await self.engine.ctx.local_store.execute(IsValidPathRequest(path=wire_path))
        if not response.valid:
            return None
        log.debug("substituted_through_the_local_daemon", path=str(path))
        return GoalResult(
            result=goal_success().result,
            resolved_outputs={},
            produced_paths={path},
        )

    async def subscribe_many(self, clients: list[ClientConn]) -> None:
        for client in clients:
            await self.subscribe(client)


def _child_map_to_derived_paths(drv_path: StorePath, node: ChildMapNode) -> list[DerivedPath]:
    results: list[DerivedPath] = []

    def walk(current: ChildMapNode, chain: tuple[str, ...]) -> None:
        for child_name, child_node in current.children.items():
            walk(child_node, (*chain, child_name))
        results.extend(
            [
                DerivedPath._from_components(
                    drv_path=drv_path,
                    chain=chain,
                    outputs=OutputsNames(frozenset({output_name})),
                )
                for output_name in current.outputs
            ]
        )

    walk(node, ())
    return results


def _realisation_output_name(key: str, realisation) -> str:
    name = getattr(realisation.id, "output_name", "")
    return str(name) if name else key.split("!", 1)[-1]


def _only_wanted_outputs(result: BuildResult, wanted: set[str]) -> BuildResult:
    """Keep the outputs that the derived path names, and drop the others.

    The daemon builds every output of the derivation, so it answers with every
    output. A derived path names the outputs that the client wants, and
    `derivation-goal.cc:291` of Nix removes each other output from the answer.
    This makes the same removal, so `drv^out` answers with `out` alone.
    """
    built = result.built_outputs
    if not built:
        return result
    kept = {key: item for key, item in built.items() if _realisation_output_name(key, item) in wanted}
    if len(kept) == len(built):
        return result
    return result.model_copy(update={"built_outputs": kept})
