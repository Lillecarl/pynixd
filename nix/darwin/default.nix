{
  config,
  pkgs,
  lib,
  ...
}:

let
  cfg = config.services.pynixd;

  common = import ../common.nix { inherit lib pkgs; };
  configFile = common.configFileFor cfg.settings;
in
{
  # The nix-darwin counterpart of `nixos/default.nix`. The option block, the
  # settings defaults and the JSON file come from `common.nix`, so the two
  # platforms cannot drift. Only the service block below is platform work.
  #
  # There is no `checks.darwin-module` gate, and this is a deliberate gap. The
  # NixOS gate calls `pkgs.nixos` to evaluate that module. nixpkgs ships no
  # darwin equivalent, and nix-darwin is not an input of this flake, so a gate
  # here would cost a lock entry and an extra evaluation in every CI workflow.
  # This module is evaluated instead by every darwin rebuild that imports it.
  # That is weaker than the NixOS gate, and it is the coverage this file has.
  options.services.pynixd = common.options;

  # One `mkIf`, for the reason the NixOS module states: a module that installs
  # a package while it is disabled cannot be imported and left alone.
  config = lib.mkIf cfg.enable {
    services.pynixd.settings = common.settingsDefaults;
    environment.etc."pynixd/pynixd.json".source = configFile;

    launchd.daemons.pynixd = {
      # `command`, and not `serviceConfig.ProgramArguments` directly. nix-darwin
      # wraps `command` in `/bin/wait4path /nix/store && exec ...`. A daemon
      # starts early at boot, and the Nix store can still be unmounted then.
      command = "${lib.getExe cfg.package} daemon";

      serviceConfig = {
        RunAtLoad = true;

        # The `Restart = "on-failure"` of the NixOS unit. `SuccessfulExit =
        # false` restarts the job when it exits non-zero and leaves a clean
        # exit alone. `ThrottleInterval` is the `RestartSec = "5s"`.
        #
        # Load-bearing, and not a general safety net. It is the retry path for
        # an unmanaged store: `LocalStore.ensure_daemon` raises at once, with
        # no retry of its own, when the system Nix daemon socket does not exist
        # yet. launchd cannot order one job after another the way systemd
        # orders units, so the restart *is* the ordering. A managed store,
        # which is the default, spawns its own `nix daemon` and never waits for
        # a system socket.
        KeepAlive.SuccessfulExit = false;
        ThrottleInterval = 5;

        # The `User` and `Group` of the NixOS unit. A launchd daemon already
        # runs as root. Both keys are here to say so, and to fail loudly if
        # that ever stops being true.
        UserName = "root";
        GroupName = "wheel";

        # The plist holds the store path of the configuration file. A change to
        # `settings` gives a new path, so the plist changes and nix-darwin
        # reloads the job. This is what `restartTriggers` does on NixOS, and
        # here it needs no option of its own.
        EnvironmentVariables.PYNIXD_CONFIG = "${configFile}";

        # NixOS reads the log from journald. launchd has no journal, so this
        # module names a file. Without these two keys the output goes nowhere a
        # person can read.
        StandardOutPath = "/var/log/pynixd.log";
        StandardErrorPath = "/var/log/pynixd.log";
      };
    };

    # No directory is made for the socket. `_ensure_unix_socket_parent` in
    # `pynixd/instance.py` creates the parent of `unix_path` before it binds.
    # The `RuntimeDirectory` of the NixOS unit is redundant for the same
    # reason. `common.nix` states the length limit that the default respects.
    environment.systemPackages = [ cfg.package ];
  };
}
