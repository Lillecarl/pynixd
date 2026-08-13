# Test pynixd decomposed vs Nix decomposed BasicDerivation
We should investigate how to build a test that first builds a closure
with --builders (let's Nix decompose derivations into BasicDerivations for
BuildDerivation) and record the BasicDerivations we receive, cleans the store
and then builds with --store and records the pynixd generated BasicDerivations
and compares them with eachother. The goal would be to make them identical, but
since the build decomposition tests work we should be reasonable and not break
pynixd to chase a ghost.

# Todo
Build tests to compare pynixd vs Nix generated BasicDerivations

# Tips
I'm perfectly fine with using some global-style variable like
"WRITE_BASICDERIVATION = Path("/somewhere")" that enables writing
BasicDerivations to file to be able to hook into where they're received and
created since otherwise it'd be borderline impossible to achieve this

# Goals
Try to make them identical within reason
Have a reliable way to compare them
