{
  config,
  pkgs,
  lib,
  ...
}:

let
  cfg = config.services.pynixd;

  jsonFormat = pkgs.formats.json { };
  configFile = jsonFormat.generate "pynixd.json" cfg.settings;
in
{
  options.services.pynixd = {
    enable = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Enable pynixd, a Python Nix daemon protocol proxy.";
    };

    package = lib.mkOption {
      type = lib.types.package;
      description = "The pynixd package to use.";
      # No default. `nixosModules.pynixd` in the repository's `flake.nix`
      # sets one, and it is the build of this repository. The old default
      # here was `(import ../.. { inherit pkgs; }).package`, which read
      # `pynixd/default.nix` and its own `flake.lock` -- a second pynixd,
      # pinned separately from the one the repository tests.
      #
      # A person who imports this file directly, and not through the flake,
      # states the package. That is better than a silent second build.
    };

    settings = lib.mkOption {
      type = jsonFormat.type;
      default = { };
      description = ''
        Extra settings serialized to JSON and passed to pynixd via PYNIXD_CONFIG.
        See PynixdSettings in pynixd/config.py for the full list.
        Defaults: { unix_path = "/run/pynixd/pynixd.sock" }
      '';
      example = lib.literalExpression ''
        {
          stores.builder1 = {
            type = "ssh-subprocess";
            host = "builder1";
            systems = [ "x86_64-linux" ];
            priority = 2.0;
          };
          stores.builder2 = {
            type = "ssh-subprocess";
            host = "builder2";
            systems = [ "aarch64-linux" ];
            priority = 0.5;
          };
          schedule_mode = "auto";
        }
      '';
    };
  };

  # One `mkIf`, and not a `mkMerge` with a branch outside it.
  # `environment.systemPackages` sat in a second element of that merge, so
  # importing this module installed pynixd on every system that read it,
  # whether or not the service was enabled. A module that does something when
  # it is disabled is a module that cannot be imported and left alone.
  config = lib.mkIf cfg.enable {
    services.pynixd.settings = lib.mapAttrsRecursive (n: v: lib.mkDefault v) {
      unix_path = "/run/pynixd/pynixd.sock";
      ssh_port = null;
      http_port = null;
    };
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
