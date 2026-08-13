#!/usr/bin/env bash
# A fake Nix build hook that answers the hook protocol just far enough to be
# accepted, then reports whether the parent process holds the output path lock.
#
# See research/reentrancy.md, "Fact 1" and "Fact 6". Protocol layout comes from
# HookInstance::HookInstance (src/libstore/unix/build/hook-instance.cc).
#
# Usage:
#   drv=$(nix-instantiate --store "$STORE" --expr '...')
#   out=$(nix-store --store "$STORE" -q --outputs "$drv")
#   nix-store --store "$STORE" --realise "$drv" \
#       --option max-jobs 0 \
#       --option build-hook "$PWD/probe-build-hook.sh $out /tmp/report.txt"
#
# Run this against a PRIVATE CHROOT STORE, never the machine's shared daemon:
# a --builders loop back to the same store wedges the daemon rather than
# erroring, because the parent holds the lock the callee waits on.
set -u

out="$1"      # store path to probe
report="$2"   # where to write the result
              # $3 is the verbosity level Nix appends; unused here

# stderr is the hook control channel. `# accept` is the reply to "try"; the
# following line is consumed as the machine name. Writing both immediately is
# safe: the parent reads lines until it sees one starting with "# ", and the
# locks are already held by then (acquireResources runs before tryBuildHook).
printf '# accept\nprobe://fake\n' >&2

# Give the parent time to enter buildWithHook before probing.
sleep 2

{
  echo "hook_pid=$$ euid=$(id -u) ppid=$PPID"
  echo "parent_cmd=$(tr '\0' ' ' < "/proc/$PPID/cmdline" 2>/dev/null)"
  echo "lockfile=${out}.lock"
  if [ -e "${out}.lock" ]; then
    echo "lockfile_exists=yes"
    if flock -n -x "${out}.lock" -c true 2>/dev/null; then
      echo "RESULT=lock_free            # parent holds NO lock on the output path"
    else
      echo "RESULT=lock_HELD_by_parent  # an independent writer would block here"
    fi
  else
    echo "lockfile_exists=no"
    echo "RESULT=no_lockfile_created  # parent took no output lock at all"
  fi
} > "$report" 2>&1

# Fail the build: we never produced the outputs.
exit 1
