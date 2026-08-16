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
    services.pynixd.settings = common.settingsDefaults;
    environment.etc."pynixd/pynixd.json".source = configFile;

    systemd.services.pynixd = {
      description = "pynixd - Python Nix daemon protocol proxy";
      wantedBy = [ "multi-user.target" ];
      after = [ "nix-daemon.service" ];
      wants = [ "nix-daemon.service" ];
      restartTriggers = [ configFile ];

      serviceConfig = {
        Type = "simple";
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

    environment.systemPackages = [ cfg.package ];
  };
}
