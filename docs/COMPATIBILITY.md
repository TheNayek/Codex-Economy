# Compatibility

Python **3.11+**, standard library only. Offline CI exercises Windows, Linux and
macOS on Python 3.11 and 3.13. See the actual workflow runs for passing revisions.

The client must support standalone NAME.config.toml profiles and
agents/NAME.toml roles, with the [agents] settings used by the installer.
No minimum version is claimed across all surfaces. Managed configuration can
restrict these options. The initializer does not test account entitlements.

The public preset preserves sandbox, approvals, web search, and MCP configuration.
For backwards compatibility only, an explicitly customized runtime_defaults may
opt into windows_sandbox=unelevated and/or web_search=live. Those fields are
empty in the distributed template. They are not necessary for quota management.

## Choosing models

Confirm available models and supported efforts before installation. Custom
init choices set all roles in the light tier to the chosen light effort, and
implementer to the balanced effort. The bundled template separately uses medium
for tester/mechanical. Inspect manifest.local.json for the actual values.

Codex role files can take precedence over explicit spawn arguments. Start new
tasks after updates. Read-only role instructions do not establish filesystem
isolation: effective parent runtime permissions may apply.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| No accounts configured | Run init with the intended absolute home, or pass --home to an installer command. |
| Local manifest exists | Edit deliberately; init never overwrites it. |
| Unowned collision | Preserve the named file; choose appropriate names or reconcile configuration. |
| Pending transaction | Run recover, inspect the result, then plan again. |
| Root preferences differ | Expected after sync; use set-defaults only if desired. |
| Unexpected child model | Inspect role precedence and attributable execution in a fresh task. |
| Observer database absent/schema unsupported | Keep results unverified; supported installation does not imply observer compatibility. |
| Permission denial | Diagnose the boundary. Do not disable protection or infer a need for elevation from a timeout. |

The observer depends on internal state_5.sqlite / rollout formats. A successful
offline test validates supported fixtures, not every client version.

Official references checked on 2026-09-25:
[configuration](https://learn.chatgpt.com/docs/config-file/config-reference),
[precedence](https://learn.chatgpt.com/docs/config-file/config-basic),
[subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents).

No controlled savings benchmark is included. See [the protocol](MEASURING.md).
