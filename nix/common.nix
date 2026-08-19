# What the NixOS module and the darwin module of pynixd both need.
#
# The two service blocks have nothing in common -- one is a systemd unit and
# the other is a launchd job -- but the option block and the settings defaults
# are the same text twice. Two copies of an option block drift, and the drift
# is silent: a renamed option keeps evaluating on the platform that was
# changed and stops on the other.
#
# The service block stays in each platform's own file. That is the part a
# person opens the module to read, and an indirection there costs more than it
# saves. Nobody reads an option block to learn what a service does.
{ lib, pkgs }:

let
  jsonFormat = pkgs.formats.json { };
in
{
  inherit jsonFormat;

  # `options.services.pynixd`, whole. Each platform module assigns this to
  # that attribute and adds nothing.
  options = {
    enable = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Enable pynixd, a Python Nix daemon protocol proxy.";
    };

    package = lib.mkOption {
      type = lib.types.package;
      description = "The pynixd package to use.";
      # No default, on purpose, and this file must not add one. The flake
      # wrappers -- `nixosModules.pynixd` and `darwinModules.pynixd` -- set it
      # to the build of this repository. The default this option once had read
      # `pynixd/default.nix` and that project's own `flake.lock`, which built a
      # second pynixd pinned apart from the one this repository tests.
      #
      # A person who imports a platform module directly, and not through the
      # flake, states the package. That is better than a silent second build.
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
            # The file that names the host key of this builder. A store sees
            # the whole content of every build pushed to it, so this is the
            # check that makes the far side the machine named here.
            known_hosts = "/etc/ssh/ssh_known_hosts";
            systems = [ "x86_64-linux" ];
            priority = 2.0;
          };
          stores.builder2 = {
            type = "ssh-subprocess";
            host = "builder2";
            # `null` accepts any host key, which is what every SSH store did
            # before this field existed. It is worth writing only for a peer
            # with no exposure, such as a local virtual machine.
            known_hosts = null;
            systems = [ "aarch64-linux" ];
            priority = 0.5;
          };
          schedule_mode = "auto";
        }
      '';
    };
  };

  # The settings each platform applies inside its own `mkIf cfg.enable`.
  #
  # Already wrapped in `mkDefault`, so a platform module cannot drop the
  # wrapper by accident. Without it these are not defaults: they are values,
  # and a user who sets `services.pynixd.settings.unix_path` gets a conflict
  # rather than an override.
  #
  # `/run` is reachable on darwin as well as on NixOS -- nix-darwin makes it
  # through `/etc/synthetic.conf`, as a symlink to `private/var/run` -- so the
  # socket path is shared. A platform that ever needs a different one
  # overrides it in its own merge, and does not branch in this file.
  #
  # Keep the path short. `sun_path` holds 104 bytes on darwin and 108 on
  # Linux, and pynixd refuses a longer one at startup
  # (`Server._check_unix_socket_length`). This default is 25.
  settingsDefaults = lib.mapAttrsRecursive (n: v: lib.mkDefault v) {
    unix_path = "/run/pynixd/pynixd.sock";
    ssh_port = null;
    http_port = null;
  };

  # The configuration file, as a derivation.
  #
  # A function of the settings, and not a bare value, because `cfg.settings`
  # exists only inside a module. Both platforms need the *same* derivation:
  # NixOS puts its path in `restartTriggers`, and darwin embeds it in the
  # plist's `EnvironmentVariables`, so each reloads the service when the
  # settings change.
  configFileFor = settings: jsonFormat.generate "pynixd.json" settings;
}
