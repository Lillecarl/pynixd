Task: Convert from_reader from classmethod to instance method on OpRequest, OpResponse, and related types
Goal
Change all from_reader methods from @classmethod returning a new instance, to instance methods that populate self and return self. This enables binding reader.identifier / writer.identifier to per-instance logger state for better
debuggability.
The Hard Parts (do these FIRST, carefully)
1. Change the abstract signatures in pynixd/operations/base.py
OpRequest (line ~178):
# BEFORE:
@classmethod
@abstractmethod
async def from_reader(cls, reader: NixReader, version: int) -> Self: ...
# AFTER:
@abstractmethod
async def from_reader(self, reader: NixReader, version: int) -> Self: ...
OpResponse (line ~255):
# BEFORE:
@classmethod
@abstractmethod
async def from_reader(cls, reader: NixReader, version: int) -> Self: ...
# AFTER:
@abstractmethod
async def from_reader(self, reader: NixReader, version: int) -> Self: ...
OperationLogs (line ~222) — NOT OpRequest/OpResponse but also has a classmethod from_reader. Convert similarly:
# BEFORE:
@classmethod
async def from_reader(cls, reader: NixReader) -> Self:
# AFTER:
async def from_reader(self, reader: NixReader) -> Self:
2. Fix OpRequest.__init_subclass__ logger pattern (line ~118-128)
The logger is currently a ClassVar[structlog.BoundLogger]. Rename it to _logger as the ClassVar, and add an instance-level logger that is a property binding _read_identifier and _write_identifier:
# On OpRequest:
_read_identifier: str = field(default="unknown", init=False)
_write_identifier: str = field(default="unknown", init=False)
_logger: ClassVar[structlog.BoundLogger]  # renamed from logger
@property
def logger(self) -> structlog.BoundLogger:
return type(self)._logger.bind(
read_identifier=self._read_identifier,
write_identifier=self._write_identifier,
)
Do the same for OpResponse (line ~232-244), which has the same pattern.
In __init_subclass__, change cls.logger = ... to cls._logger = ... for both base classes.
Important: BuildResult (line ~773-833) has its own _log: ClassVar — leave that alone, it's not an OpRequest/OpResponse. Same for the module-level log variable.
3. Fix Connection.call() in pynixd/connection.py (line ~185)
This is the main callsite where responses are deserialized from a backend daemon:
# BEFORE:
response = await response_type.from_reader(self.r, self.version)
# BEFORE:
response = await response_type.from_reader(self.r, self.version)
# AFTER:
response = await response_type().from_reader(self.r, self.version)
4. Fix OpRequest.handle() in pynixd/operations/base.py (line ~137)
The handle() classmethod creates a request by calling cls.from_reader(). Since it's a classmethod, construct the instance first:
# BEFORE:
request = await cls.from_reader(ctx.proxy.r, ctx.version)
# AFTER:
request = await cls().from_reader(ctx.proxy.r, ctx.version)
This same pattern appears in subclasses that override handle(). Find and fix them all — search for cls.from_reader(ctx.proxy.r in all operation files:
- pynixd/operations/build_paths.py line ~203 and ~294
- pynixd/operations/build_derivation.py line ~78
- pynixd/operations/collect_garbage.py line ~109
- pynixd/operations/add_multiple_to_store.py line ~59
- pynixd/operations/add_to_store_nar.py line ~126
- pynixd/operations/add_to_store.py line ~89
- pynixd/operations/sign_path_info.py line ~79
- pynixd/operations/optimise_store.py line ~56
- pynixd/operations/nar_from_path.py line ~104
- pynixd/operations/verify_store.py line ~65
- pynixd/operations/add_build_log.py line ~60
5. Fix stderr.py _PARSERS dispatch (line ~255)
The stderr message types (StderrNext, StderrStartActivity, etc.) have from_reader classmethods called via a dispatch dict. These use slots=True and are not OpRequest/OpResponse, but they also need converting. Change:
# BEFORE:
msg = await parser.from_reader(r)
# AFTER:
msg = await parser().from_reader(r)
But StderrError, StderrNext, etc. use @dataclass(slots=True) — you can't add non-slot fields. Since these types don't need identifier tracking, just convert from_reader to instance method without adding _read_identifier. The key
constraint: every from_reader on these types must still return self after populating fields.
6. Handle UnkeyedValidPathInfo / ValidPathInfo / SubstitutablePathInfo / BasicDerivation / BuildResult / KeyedBuildResult
These are complex types in base.py that have from_reader but are NOT OpRequest/OpResponse subclasses. Convert their from_reader to instance methods too (they're used by OpResponse from_reader methods), but do NOT add
_read_identifier — they don't have a logger property.
Special case — ValidPathInfo.from_reader (line ~336):
# BEFORE:
@classmethod
async def from_reader(cls, reader: NixReader) -> Self:
path = await reader.read_string(StorePath)
info = await UnkeyedValidPathInfo.from_reader(reader)
return info.with_path(path)
# AFTER:
async def from_reader(self, reader: NixReader) -> Self:
path = await reader.read_string(StorePath)
info = await UnkeyedValidPathInfo().from_reader(reader)
return info.with_path(path)
Special case — BuildResult.from_reader (line ~788): returns cls(...) — change to populate self and return self. Note BuildResult uses _log ClassVar not logger.
Special case — KeyedBuildResult.from_reader (line ~874): calls BuildResult.from_reader(reader, version) — change to BuildResult().from_reader(reader, version).
The Easy Parts (broadly)
For every concrete OpRequest and OpResponse subclass in pynixd/operations/*.py, apply this mechanical transformation to ALL their from_reader methods:
# BEFORE:
@classmethod
async def from_reader(cls, reader: NixReader, version: int) -> Self:
... read fields ...
return cls(field1=..., field2=...)
# AFTER:
async def from_reader(self, reader: NixReader, version: int) -> Self:
... read fields ...
self.field1 = ...
self.field2 = ...
return self
For OperationLogs.from_reader calls within response from_reader methods, change:
# BEFORE:
logs = await OperationLogs.from_reader(reader)
# AFTER:
logs = await OperationLogs().from_reader(reader)
For nested type from_reader calls (like ValidPathInfo.from_reader, UnkeyedValidPathInfo.from_reader, SubstitutablePathInfo.from_reader, BasicDerivation.from_reader, BuildResult.from_reader, KeyedBuildResult.from_reader), change:
# BEFORE:
info = await SomeType.from_reader(reader, version)
# AFTER:
info = await SomeType().from_reader(reader, version)
For all cls.logger.debug(...) calls in from_reader methods, change to self.logger.debug(...) (since self.logger is now a property that binds identifiers).
Additionally, at the top of each from_reader impl on OpRequest/OpResponse, add:
self._read_identifier = reader.identifier
And at the top of each to_writer impl on OpRequest/OpResponse, add:
self._write_identifier = writer.identifier
Verification
After making all changes, run just check (which runs ruff and pyright). Fix any errors. The transformation is mechanical — there should be no logic changes, only signature and callsite changes.
Files to modify
- pynixd/operations/base.py — abstract signatures, base classes, complex types
- pynixd/connection.py — Connection.call() callsite
- pynixd/stderr.py — stderr type from_reader methods and dispatch
- pynixd/store.py — any callsites of .from_reader( or .response_type.from_reader(
- ALL files in pynixd/operations/ — every concrete OpRequest/OpResponse subclass
