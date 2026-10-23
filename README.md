# hermes-check-in

Timed **same-conversation** check-ins for [Hermes Agent](https://hermes-agent.nousresearch.com/docs).

You arm it in a chat; when the conversation has been quiet for N minutes, the prompt you
gave it is injected back into **that same session** as a user turn — with full context, not
a fresh stateless cron run. It repeats until closed, and the agent can close it itself with
the `checkin_close` tool when the work is done.

```
/check-in 10m "Give me a progress report and if everything has been done give me a final
               report and close the check-in"
/check-in          # status + time to next fire
/check-in stop     # close
checkin_close      # model-facing close
```

"10m" is ten minutes of *silence*: the countdown restarts on every model call in the session,
so it never fires mid-turn. The timer lives in the gateway process; armed check-ins are
persisted to `~/.hermes/check-ins.json` and re-armed on load, so a gateway restart does not
lose them.

## Requirements

- Hermes Agent with the plugin API (`ctx.register_command`, `ctx.register_tool`,
  `ctx.register_hook`, `ctx.inject_message`).
- Gateway injection consent (Hermes gates it deliberately — a plugin cannot publish turns
  into your chats without it):

```bash
hermes config set plugins.entries.check-in.allow_gateway_injection true
```

## Install

### Nix / NixOS (declarative)

Pin the plugin in your flake inputs or a `fetchFromGitHub`, then link it into the Hermes
plugin dir with home-manager — e.g. in a host's `home.nix`:

```nix
let
  checkIn = pkgs.fetchFromGitHub {
    owner = "ottobunge";
    repo = "hermes-check-in";
    rev = "<commit sha>";
    hash = "sha256-...";
  };
in {
  home.file.".hermes/plugins/check-in".source = "${checkIn}";
}
```

Rebuild, then enable + consent (config, not files — see above):

```bash
hermes config set plugins.enabled '[..., check-in]'   # once; merge into your existing list
hermes config set plugins.entries.check-in.allow_gateway_injection true
hermes gateway restart
```

### Manual

```bash
git clone https://github.com/ottobunge/hermes-check-in ~/.hermes/plugins/check-in
hermes config set plugins.enabled '[..., check-in]'
hermes config set plugins.entries.check-in.allow_gateway_injection true
hermes gateway restart
```

Verify with `python3 ~/.hermes/plugins/check-in/selfcheck.py` (no gateway needed — exercises
interval parsing, arm/reset/fire/close and persistence) and `hermes plugins validate ~/.hermes/plugins/check-in`.

## Notes

- Session targeting uses `gateway.session_context.bound_session_key` — a private seam with no
  public equivalent; that is what the host's own keyless injector uses too. If it ever moves,
  the plugin fails closed (the command reports "no session bound" instead of injecting).
- Injection is refused unless consent is granted; on refusal the plugin closes the check-in
  rather than retrying forever.
- Enablement lives in `~/.hermes/config.yaml`, so a Nix install that symlinks the files still
  needs the two `hermes config set` lines once per machine.

MIT.
