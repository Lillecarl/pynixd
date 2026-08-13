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
      default = (import ../.. { inherit pkgs; }).package;
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

  config = lib.mkMerge [
    (lib.mkIf cfg.enable {
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
          # PrivateTmp = true;
          NoNewPrivileges = true;
        };
      };
    })
    {
      environment.systemPackages = [ cfg.package ];
    }
  ];
}
