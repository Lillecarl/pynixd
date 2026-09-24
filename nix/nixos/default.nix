{
  config,
  pkgs,
  lib,
  ...
}:

let
  cfg = config.services.pynixd;

  # The option block and the settings defaults are shared with the darwin
  # module. `../common.nix` says why, and holds the reason `package` has no
  # default.
  common = import ../common.nix { inherit lib pkgs; };

  configFile = common.configFileFor cfg.settings;
in
{
  options.services.pynixd = common.options;

  # One `mkIf`, and not a `mkMerge` with a branch outside it.
  # `environment.systemPackages` sat in a second element of that merge, so
  # importing this module installed pynixd on every system that read it,
  # whether or not the service was enabled. A module that does something when
  # it is disabled is a module that cannot be imported and left alone.
  config = lib.mkIf cfg.enable {
    services.pynixd.settings = common.settingsDefaultsFor cfg;

    # `replace`: nix-daemon listens behind pynixd. The empty entry clears
    # the `ListenStream` of the unit Nix ships, which a second entry would
    # only add to.
    systemd.sockets.nix-daemon.socketConfig.ListenStream = lib.mkIf (cfg.mode == "replace") [
      ""
      common.upstreamSocket
    ];
    environment.etc."pynixd/pynixd.json".source = configFile;

    systemd.services.pynixd = {
      description = "pynixd - Python Nix daemon protocol proxy";
      wantedBy = [ "multi-user.target" ];
      after = [
        "nix-daemon.socket"
        "nix-daemon.service"
      ];
      wants = [ "nix-daemon.service" ];
      requires = lib.mkIf (cfg.mode == "replace") [ "nix-daemon.socket" ];
      # In `replace` mode this is the daemon, so it is up before anything
      # that uses one.
      before = lib.mkIf (cfg.mode == "replace") [ "multi-user.target" ];
      restartTriggers = [ configFile ];
      # pynixd reads `trusted-users` and `allowed-users` with `nix config
      # show`, from the same Nix as nix-daemon.
      path = [ config.nix.package ];

      serviceConfig = {
        # pynixd says READY=1 once its listeners are bound. `simple` called
        # it active two seconds before the socket existed.
        Type = "notify";
        ExecStart = "${lib.getExe cfg.package} daemon";
        User = "root";
        Group = "root";
        RuntimeDirectory = "pynixd";
        RuntimeDirectoryMode = "755";
        Environment = "PYNIXD_CONFIG=${configFile}";
        Restart = "on-failure";
        RestartSec = "5s";
        NoNewPrivileges = true;
      };
    };

    networking.firewall.allowedTCPPorts = lib.mkIf (cfg.metrics.enable && cfg.metrics.openFirewall) [
      cfg.metrics.port
    ];

    environment.systemPackages = [ cfg.package ];
  };
}
