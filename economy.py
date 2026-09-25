#!/usr/bin/env python3
"""Bounded transactional installer with scoped, secret-safe config edits."""
from __future__ import annotations

import argparse, hashlib, json, os, re, shutil, sys, tomllib, uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from workspace_temp import WorkspaceTempDirectory

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MANIFEST = SCRIPT_DIR / "manifest.json"
CONFIG_REL, AGENTS_REL, BACKUP_DIR_NAME = "config.toml", "AGENTS.md", "economy-backups"
CONFIG_MARKER = "# codex-economy-managed: config"
AGENT_MARKER_PREFIX = "# codex-economy-managed: agent:"
ROUTE_MARKER_PREFIX = "# codex-economy-managed: route:"
POLICY_BEGIN, POLICY_END = "<!-- codex-economy:begin -->", "<!-- codex-economy:end -->"
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
ROOT_KEYS = ("model", "model_reasoning_effort", "service_tier")
AGENT_KEYS = ("enabled", "default_subagent_model", "default_subagent_reasoning_effort", "max_concurrent_threads_per_session")
RUNTIME_KEYS = (("windows", "sandbox"), (None, "web_search"))
ALL_CONFIG_PATHS = tuple((None, k) for k in ROOT_KEYS) + tuple(("agents", k) for k in AGENT_KEYS) + RUNTIME_KEYS
MISSING_HASH, _ABSENT = "MISSING", object()
_AFTER_MANAGED_MUTATION: Callable[[str], None] | None = None  # tests only


class EconomyError(RuntimeError): pass


def _resolve_manifest_path(explicit: str | None = None, base: Path = SCRIPT_DIR) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    local = base / "manifest.local.json"
    return local if os.path.lexists(local) else base / "manifest.json"


def _sha256_bytes(data: bytes) -> str: return hashlib.sha256(data).hexdigest()
def _json_hash(value: Any) -> str: return _sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode())


def _read_bytes(path: Path) -> bytes | None:
    try: return path.read_bytes()
    except FileNotFoundError: return None


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.economy-{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("wb") as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        os.replace(temp, path)
    finally: temp.unlink(missing_ok=True)


def _is_relative_to(path: Path, root: Path) -> bool:
    try: return path.is_relative_to(root)
    except AttributeError: return path == root or str(path).startswith(str(root) + os.sep)


def _is_reparse(path: Path) -> bool:
    try:
        stat = path.lstat()
    except FileNotFoundError:
        return False
    return bool(getattr(stat, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)) or path.is_symlink()


def _validate_home(home: Path) -> Path:
    original = home.expanduser().absolute()
    anchor = Path(original.anchor)
    lexical_parts = original.parts[1:] if original.anchor else original.parts
    if not original.anchor or original == anchor or len(lexical_parts) < 3: raise EconomyError("profile home is too broad")
    resolved = original.resolve()
    user_home = Path.home()
    broad = {anchor.resolve(), user_home.resolve(), user_home.parent.resolve(), SCRIPT_DIR.resolve(),
             SCRIPT_DIR.parent.resolve(), SCRIPT_DIR.parents[1].resolve(), Path.cwd().resolve()}
    broad.update((user_home / name).resolve() for name in ("Documents", "Desktop", "Downloads") if (user_home / name).exists())
    if resolved in broad or resolved == Path(resolved.anchor): raise EconomyError("profile home resolves to a broad directory")
    # Reject aliases in the full existing path; callers must name the real
    # profile directory rather than a symlink/junction facade.
    current = anchor
    for part in lexical_parts:
        current /= part
        if current.exists() and _is_reparse(current): raise EconomyError("profile home must not use a symlink, junction, or reparse point")
    return resolved


def _safe_relpath(value: str, nested: bool = True) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value: raise EconomyError("unsafe managed path")
    normalized = value.replace("\\", "/"); rel = Path(normalized)
    if rel.is_absolute() or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized): raise EconomyError(f"unsafe managed path: {value!r}")
    if any(p in {"", ".", ".."} for p in rel.parts) or (not nested and len(rel.parts) != 1): raise EconomyError(f"unsafe managed path: {value!r}")
    for part in rel.parts:
        stem = part.split(".", 1)[0].upper()
        if (":" in part or part.endswith((".", " ")) or any(ord(ch) < 32 for ch in part)
                or stem in WINDOWS_RESERVED): raise EconomyError(f"unsafe Windows path component: {part!r}")
    if rel.parts[0].casefold() == BACKUP_DIR_NAME.casefold(): raise EconomyError(f"unsafe managed path: {value!r}")
    return rel


def _safe_home_path(home: Path, relpath: str) -> Path:
    rel, root = _safe_relpath(relpath), home.resolve()
    candidate = root.joinpath(*rel.parts)
    if candidate.resolve(strict=False) == root or not _is_relative_to(candidate.resolve(strict=False), root): raise EconomyError(f"managed path escapes profile home: {relpath}")
    current = root
    for part in rel.parts:
        current /= part
        if current.exists() and _is_reparse(current): raise EconomyError(f"managed path uses a symlink, junction, or reparse point: {relpath}")
    return candidate


def _safe_backup_path(txdir: Path, name: str) -> Path:
    rel, root = _safe_relpath(name, False), txdir.resolve()
    path = root / rel
    if not _is_relative_to(path.resolve(strict=False), root): raise EconomyError("backup path escapes transaction directory")
    if path.exists() and _is_reparse(path): raise EconomyError("backup path must not be a symlink, junction, or reparse point")
    return path


def _mutate(home: Path, relpath: str, data: bytes | None) -> None:
    path = _safe_home_path(home, relpath)  # re-resolve immediately before operation
    if data is None: path.unlink(missing_ok=True)
    else: _write_atomic(path, data)
    if _AFTER_MANAGED_MUTATION: _AFTER_MANAGED_MUTATION(relpath)


def _toml_literal(value: Any) -> str:
    if isinstance(value, bool): return "true" if value else "false"
    if isinstance(value, int): return str(value)
    if isinstance(value, str): return json.dumps(value, ensure_ascii=False)
    raise EconomyError("unsupported TOML scalar")


def _valid_scalar(value: Any) -> bool: return isinstance(value, (str, bool)) or (isinstance(value, int) and not isinstance(value, bool))


def _name(kind: str, value: Any) -> str:
    if (not isinstance(value, str) or SAFE_NAME.fullmatch(value) is None or value.upper() in WINDOWS_RESERVED
            or value.endswith((".", " "))): raise EconomyError(f"invalid {kind} name: {value!r}")
    return value


def _load_manifest(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes(); text = raw.decode(); m = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc: raise EconomyError(f"cannot read manifest: {path}") from exc
    if not isinstance(m, dict) or m.get("schema_version") != 1: raise EconomyError("unsupported manifest schema")
    profiles, routing, agents, settings = (m.get(k) for k in ("profiles", "routing", "agents", "agent_settings"))
    if not all(isinstance(x, dict) for x in (profiles, routing, agents, settings)): raise EconomyError("manifest requires profiles, routing, agents, and agent_settings")
    runtime_defaults = m.get("runtime_defaults")
    if (not isinstance(runtime_defaults, dict)
            or set(runtime_defaults) - {"windows_sandbox", "web_search"}
            or ("windows_sandbox" in runtime_defaults and runtime_defaults["windows_sandbox"] != "unelevated")
            or ("web_search" in runtime_defaults and runtime_defaults["web_search"] != "live")):
        raise EconomyError("runtime_defaults must be empty or explicitly opt into unelevated/live compatibility settings")
    for account, p in profiles.items():
        _name("account", account)
        if not isinstance(p, str) or not Path(p).expanduser().is_absolute(): raise EconomyError(f"profile {account!r} must be an absolute path")
    if len({n.casefold() for n in profiles}) != len(profiles): raise EconomyError("account names collide on a case-insensitive filesystem")
    if "DEFAULT" not in routing: raise EconomyError("routing requires DEFAULT")
    for n, route in routing.items():
        _name("route", n)
        if not isinstance(route, dict) or any(not isinstance(route.get(k), str) or not route[k] for k in ROOT_KEYS): raise EconomyError(f"invalid routing entry: {n}")
    if len({n.casefold() for n in routing}) != len(routing): raise EconomyError("route names collide on a case-insensitive filesystem")
    retired = m.get("retired_profiles", [])
    if not isinstance(retired, list): raise EconomyError("retired_profiles must be a list")
    seen: set[str] = set()
    for n in retired:
        n = _name("retired route", n)
        if n in routing or n in seen: raise EconomyError(f"invalid retired route: {n}")
        seen.add(n)
    if {n.casefold() for n in retired} & {n.casefold() for n in routing}: raise EconomyError("active and retired route names collide")
    maximum = settings.get("max_concurrent_threads_per_session")
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1: raise EconomyError("agent_settings.max_concurrent_threads_per_session must be positive")
    for n, spec in agents.items():
        _name("agent", n)
        required = ("description", "developer_instructions", "model", "model_reasoning_effort", "service_tier")
        if not isinstance(spec, dict) or any(not isinstance(spec.get(k), str) or not spec[k] for k in required): raise EconomyError(f"invalid agent entry: {n}")
        if n in {"explorer", "researcher"} and spec.get("sandbox_mode") != "read-only": raise EconomyError(f"agent {n} must be read-only")
        if n not in {"explorer", "researcher"} and "sandbox_mode" in spec: raise EconomyError(f"agent {n} has unapproved sandbox setting")
    if len({n.casefold() for n in agents}) != len(agents): raise EconomyError("agent names collide on a case-insensitive filesystem")
    if "worker" not in agents: raise EconomyError("agents requires worker role")
    # Git and Windows checkouts may represent the same JSON with LF or CRLF.
    # Provenance follows canonical LF text so line-ending normalization does
    # not invalidate an otherwise identical installed manifest.
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return m, _sha256_bytes(normalized.encode("utf-8"))


def _load_policy(manifest: dict[str, Any], base: Path = SCRIPT_DIR) -> tuple[str, str]:
    raw = manifest.get("policy_file", "policy.md")
    if not isinstance(raw, str): raise EconomyError("policy_file must be relative")
    path, root = (base.resolve() / _safe_relpath(raw, False)).resolve(strict=False), base.resolve()
    if path.parent != root: raise EconomyError("policy_file must stay beside manifest")
    try: text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc: raise EconomyError("cannot read policy file") from exc
    begin, end = text.find(POLICY_BEGIN), text.find(POLICY_END)
    if begin < 0 or end < begin: raise EconomyError("policy file requires marked block")
    return text[begin:end + len(POLICY_END)].strip() + "\n", _sha256_bytes(text.encode())


def _profile_paths(m: dict[str, Any], account: str | None, home: str | None) -> list[tuple[str, Path]]:
    if account: _name("account", account)
    if home: return [(account or "custom", _validate_home(Path(home)))]
    if not m["profiles"]: raise EconomyError("no profile homes configured; run init --home ABSOLUTE or pass --home")
    if account:
        if account not in m["profiles"]: raise EconomyError(f"unknown account: {account}")
        return [(account, _validate_home(Path(m["profiles"][account])))]
    return [(n, _validate_home(Path(p))) for n, p in m["profiles"].items()]


def _init_manifest(home: str, account: str, base: Path = SCRIPT_DIR,
                   model_choices: dict[str, tuple[str, str]] | None = None) -> Path:
    _name("account", account)
    candidate = Path(home).expanduser()
    if not candidate.is_absolute(): raise EconomyError("init --home must be an absolute path")
    resolved_home = _validate_home(candidate)
    source = base / "manifest.json"
    manifest, _ = _load_manifest(source)
    _load_policy(manifest, base)
    manifest["profiles"] = {account: str(resolved_home)}
    manifest.pop("deployment_root", None)
    if model_choices:
        if set(model_choices) != {"light", "balanced", "deep"}:
            raise EconomyError("supply all three model choices together")
        for model, effort in model_choices.values():
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", model):
                raise EconomyError("model IDs must be nonempty identifiers, not shell commands")
            if effort not in {"none", "minimal", "low", "medium", "high", "xhigh"}:
                raise EconomyError("unsupported effort choice")
        for route, tier in {"QUICK": "light", "DEFAULT": "balanced", "DEEP": "deep"}.items():
            model, effort = model_choices[tier]
            manifest["routing"][route].update(model=model, model_reasoning_effort=effort)
        for name, spec in manifest["agents"].items():
            tier = "balanced" if name == "implementer" else "light"
            model, effort = model_choices[tier]
            spec.update(model=model, model_reasoning_effort=effort)
    destination = base / "manifest.local.json"
    if os.path.lexists(destination): raise EconomyError(f"local manifest already exists: {destination}")
    data = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        with destination.open("xb") as handle:
            handle.write(data)
    except FileExistsError as exc:
        raise EconomyError(f"local manifest already exists: {destination}") from exc
    return destination


def _config_semantics(text: str) -> dict[str, Any]:
    try: parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc: raise EconomyError("config.toml is invalid; refusing to modify it") from exc
    if not isinstance(parsed, dict): raise EconomyError("config.toml root is invalid")
    return parsed


def _has_literal_sensitive_value(value: Any, key: str = "") -> bool:
    sensitive = re.search(r"(?i)(api.?key|access.?token|private.?key|password|secret|token)", key) is not None
    if isinstance(value, dict): return any(_has_literal_sensitive_value(child, str(name)) for name, child in value.items())
    if isinstance(value, list): return any(_has_literal_sensitive_value(child, key) for child in value)
    if not sensitive or not isinstance(value, str) or not value: return False
    return re.fullmatch(r"(?:\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|%[A-Za-z_][A-Za-z0-9_]*%|env\s*\([^)]*\))", value.strip()) is None


_SENSITIVE_TEXT = re.compile(
    r"(?im)^\s*(?:[-*]\s*)?[A-Za-z0-9_.-]*(?:api.?key|access.?token|private.?key|password|secret|token)"
    r"[A-Za-z0-9_.-]*\s*[=:]\s*(?P<value>[^#\r\n]+)"
)


def _assert_regular_backup_safe(relpath: str, data: bytes) -> None:
    """Refuse to create a raw backup when a managed file contains a literal secret."""
    try: text = data.decode("utf-8")
    except UnicodeDecodeError as exc: raise EconomyError(f"{relpath} is not safely inspectable for backup") from exc
    if relpath.endswith(".toml"):
        try: sensitive = _has_literal_sensitive_value(_config_semantics(text))
        except EconomyError as exc: raise EconomyError(f"{relpath} is not safely inspectable for backup") from exc
        if sensitive: raise EconomyError(f"{relpath} contains a literal sensitive value; refusing raw backup")
    for match in _SENSITIVE_TEXT.finditer(text):
        value = match.group("value").strip().strip('"\'')
        if value and re.fullmatch(r"(?:\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|%[A-Za-z_][A-Za-z0-9_]*%|env\s*\([^)]*\))", value) is None:
            raise EconomyError(f"{relpath} may contain a literal sensitive value; refusing raw backup")


def _split_comment(raw: str) -> tuple[str, str]:
    quote = None; escaped = False
    for i, ch in enumerate(raw):
        if quote == '"':
            if escaped: escaped = False
            elif ch == "\\": escaped = True
            elif ch == '"': quote = None
        elif quote == "'":
            if ch == "'": quote = None
        elif ch in {'"', "'"}: quote = ch
        elif ch == "#": return raw[:i].rstrip(), raw[i:]
    if quote: raise EconomyError("unsupported owned multiline string")
    return raw.rstrip(), ""


@dataclass(frozen=True)
class OwnedLine:
    index: int; prefix: str; literal: str; suffix: str; newline: str


def _path_name(path: tuple[str | None, str]) -> str: return path[1] if path[0] is None else f"{path[0]}.{path[1]}"


def _scan_lines(text: str, keys: tuple[tuple[str | None, str], ...]) -> tuple[list[str], dict[tuple[str | None, str], OwnedLine]]:
    wanted, lines, section, found = set(keys), text.splitlines(keepends=True), None, {}
    for i, line in enumerate(lines):
        nl = "\r\n" if line.endswith("\r\n") else ("\n" if line.endswith("\n") else ""); body = line[:-len(nl)] if nl else line
        header = re.fullmatch(r"\s*\[([A-Za-z0-9_-]+)\]\s*(?:#.*)?", body)
        if header: section = header.group(1); continue
        if re.match(r"\s*\[", body): section = "<unsupported>"; continue
        match = re.fullmatch(r"(?P<prefix>[ \t]*)(?P<key>[A-Za-z0-9_-]+)(?P<sep>[ \t]*=[ \t]*)(?P<raw>.*)", body)
        if not match or (section, match.group("key")) not in wanted: continue
        path = (section, match.group("key"))
        if path in found: raise EconomyError(f"duplicate owned assignment: {_path_name(path)}")
        literal, comment = _split_comment(match.group("raw"))
        if not literal: raise EconomyError(f"empty owned assignment: {_path_name(path)}")
        prefix = match.group("prefix") + match.group("key") + match.group("sep")
        # Keep comments out of transaction metadata; they stay in place in the source.
        spacing = match.group("raw")[len(literal):match.group("raw").find("#")] if comment else match.group("raw")[len(literal):]
        found[path] = OwnedLine(i, prefix, literal, spacing + comment, nl)
    return lines, found


def _semantic(parsed: dict[str, Any], path: tuple[str | None, str]) -> Any:
    section, key = path; container = parsed if section is None else parsed.get(section, _ABSENT)
    if container is _ABSENT: return _ABSENT
    if not isinstance(container, dict): raise EconomyError(f"config section {section} is not a table")
    return container.get(key, _ABSENT)


def _projection(text: str, keys: tuple[tuple[str | None, str], ...]) -> dict[str, dict[str, Any]]:
    parsed, (_lines, found), result = _config_semantics(text), _scan_lines(text, keys), {}
    for path in keys:
        value, n = _semantic(parsed, path), _path_name(path)
        if value is _ABSENT:
            if path in found: raise EconomyError(f"cannot reconcile owned assignment: {n}")
            result[n] = {"present": False}; continue
        if path not in found or not _valid_scalar(value): raise EconomyError(f"owned key {n} must use a simple bare scalar assignment")
        # Validate lexical scalar, but store only its parsed nonsecret value/presence.
        try: lexical = tomllib.loads("value = " + found[path].literal + "\n")["value"]
        except tomllib.TOMLDecodeError as exc: raise EconomyError(f"unsupported lexical form: {n}") from exc
        if lexical != value or not _valid_scalar(lexical): raise EconomyError(f"unsupported lexical form: {n}")
        result[n] = {"present": True, "value": value}
    return result


def _masked_hash(text: str, keys: tuple[tuple[str | None, str], ...]) -> str:
    lines, found = _scan_lines(text, keys)
    for line in found.values(): lines[line.index] = line.prefix + "<owned>" + line.suffix + line.newline
    return _sha256_bytes("".join(lines).encode())


def _config_state(text: str, keys: tuple[tuple[str | None, str], ...]) -> dict[str, Any]:
    parsed = copy_without_owned = copy_semantics = _config_semantics(text)
    copy_without_owned = json.loads(json.dumps(parsed, default=str))
    for section, key in keys:
        container = copy_without_owned if section is None else copy_without_owned.get(section)
        if isinstance(container, dict):
            container.pop(key, None)
            if section is not None and not container: copy_without_owned.pop(section, None)
    mcp = copy_semantics.get("mcp_servers", {})
    return {"owned": _projection(text, keys), "unowned_lexical_sha256": _masked_hash(text, keys),
            "unowned_semantic_sha256": _json_hash(copy_without_owned), "mcp_semantic_sha256": _json_hash(mcp)}


def _patch_config(text: str, desired: dict[tuple[str | None, str], Any]) -> str:
    _config_semantics(text); keys = tuple(desired); lines, found = _scan_lines(text, keys)
    for path, line in sorted(found.items(), key=lambda x: x[1].index, reverse=True):
        value = desired[path]
        if value is _ABSENT: del lines[line.index]
        else: lines[line.index] = line.prefix + _toml_literal(value) + line.suffix + line.newline
    missing = [(p, v) for p, v in desired.items() if p not in found and v is not _ABSENT]
    newline = "\r\n" if "\r\n" in text else "\n"
    for section in (None, "windows", "agents"):
        additions = [(p, v) for p, v in missing if p[0] == section]
        if not additions: continue
        if section is None: at = next((i for i, line in enumerate(lines) if re.match(r"\s*\[", line)), len(lines))
        else:
            header = next((i for i, line in enumerate(lines) if re.fullmatch(rf"\s*\[{section}\]\s*(?:#.*)?(?:\r?\n)?", line)), None)
            if header is None:
                if lines and not lines[-1].endswith(("\n", "\r")): lines[-1] += newline
                if lines and lines[-1].strip(): lines.append(newline)
                lines.append(f"[{section}]" + newline); at = len(lines)
            else: at = next((i for i in range(header + 1, len(lines)) if re.match(r"\s*\[", lines[i])), len(lines))
        lines[at:at] = [f"{p[1]} = {_toml_literal(v)}{newline}" for p, v in additions]
    result = "".join(lines); parsed = _config_semantics(result)
    for path, expected in desired.items():
        actual = _semantic(parsed, path)
        if (expected is _ABSENT and actual is not _ABSENT) or (expected is not _ABSENT and actual != expected): raise EconomyError(f"failed to patch {_path_name(path)}")
    return result


def _desired_config(m: dict[str, Any], defaults: bool) -> dict[tuple[str | None, str], Any]:
    d = {
        ("agents", "enabled"): True,
        ("agents", "default_subagent_model"): m["agents"]["worker"]["model"],
        ("agents", "default_subagent_reasoning_effort"): m["agents"]["worker"]["model_reasoning_effort"],
        ("agents", "max_concurrent_threads_per_session"): m["agent_settings"]["max_concurrent_threads_per_session"],
    }
    if "windows_sandbox" in m["runtime_defaults"]:
        d[("windows", "sandbox")] = m["runtime_defaults"]["windows_sandbox"]
    if "web_search" in m["runtime_defaults"]:
        d[(None, "web_search")] = m["runtime_defaults"]["web_search"]
    if defaults: d.update({(None, k): m["routing"]["DEFAULT"][k] for k in ROOT_KEYS})
    return d


def _agent_text(name: str, spec: dict[str, Any]) -> str:
    fields = [("name", name), ("description", spec["description"]), ("developer_instructions", spec["developer_instructions"]),
              ("model", spec["model"]), ("model_reasoning_effort", spec["model_reasoning_effort"]), ("service_tier", spec["service_tier"])]
    if "sandbox_mode" in spec: fields.append(("sandbox_mode", spec["sandbox_mode"]))
    return "\n".join([AGENT_MARKER_PREFIX + name, *(f"{k} = {_toml_literal(v)}" for k, v in fields)]) + "\n"


def _route_text(name: str, spec: dict[str, Any]) -> str:
    return "\n".join([ROUTE_MARKER_PREFIX + name, f"model = {_toml_literal(spec['model'])}",
                      f"model_reasoning_effort = {_toml_literal(spec['model_reasoning_effort'])}",
                      f"service_tier = {_toml_literal(spec['service_tier'])}", ""])


def _merge_policy(existing: str | None, policy: str) -> str:
    if not existing: return policy
    begin, end = existing.find(POLICY_BEGIN), existing.find(POLICY_END)
    if begin >= 0 and end >= begin: return existing[:begin] + policy.rstrip("\r\n") + existing[end + len(POLICY_END):]
    sep = "" if existing.endswith(("\n", "\r")) else "\n"
    if not existing.endswith(("\n\n", "\r\r", "\r\n\r\n")): sep += "\n"
    return existing + sep + policy


def _contains_marker(text: str, marker: str) -> bool: return text == marker or text.startswith(marker + "\n") or text.startswith(marker + "\r\n")


def _txroot(home: Path) -> Path:
    root = home.resolve() / BACKUP_DIR_NAME
    if not _is_relative_to(root.resolve(strict=False), home.resolve()): raise EconomyError("transaction directory escapes profile home")
    return root


def _txdirs(home: Path) -> list[Path]:
    root = _txroot(home)
    if _is_reparse(root): raise EconomyError("transaction directory must not be a symlink, junction, or reparse point")
    if not root.is_dir(): return []
    result = []
    for child in root.iterdir():
        if _is_reparse(child): raise EconomyError("transaction entry must not be a symlink, junction, or reparse point")
        if child.is_dir() and SAFE_NAME.fullmatch(child.name): result.append(child)
        elif child.is_dir(): raise EconomyError("unsafe transaction directory entry")
    return sorted(result, key=lambda p: p.name)


def _read_tx(txdir: Path) -> dict[str, Any]:
    metadata = _safe_backup_path(txdir, "transaction.json")
    try: record = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc: raise EconomyError(f"invalid transaction metadata: {txdir.name}") from exc
    if record.get("schema_version") not in {1, 2} or record.get("transaction") != txdir.name: raise EconomyError(f"invalid transaction metadata: {txdir.name}")
    return record


def _pending(home: Path) -> list[Path]:
    result = []
    for d in _txdirs(home):
        if not (d / "transaction.json").is_file(): result.append(d); continue
        if _read_tx(d).get("status") in {"preparing", "rolling_back", "recovery_failed", "rolling_back_legacy", "legacy_recovery_failed"}: result.append(d)
    return result


def _transaction_time(txdir: Path, record: dict[str, Any]) -> tuple[datetime | None, bool]:
    """Return a precise metadata time, or a second-precision legacy fallback."""
    created_at = record.get("created_at")
    if isinstance(created_at, str):
        try:
            parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.astimezone(timezone.utc), True
        except ValueError:
            pass
    match = re.match(r"^(\d{8}T\d{6}Z)-", txdir.name)
    if match:
        try: return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc), False
        except ValueError: pass
    return None, False


def _ordered_completed(home: Path, relpath: str | None = None) -> list[tuple[Path, dict[str, Any]]]:
    candidates: list[tuple[Path, dict[str, Any], datetime | None, bool]] = []
    for txdir in _txdirs(home):
        try: record = _read_tx(txdir)
        except EconomyError: continue
        if record.get("status") != "complete": continue
        if relpath is not None and not any(isinstance(entry, dict) and entry.get("path") == relpath for entry in record.get("files", [])): continue
        moment, precise = _transaction_time(txdir, record); candidates.append((txdir, record, moment, precise))
    if len(candidates) > 1:
        if any(moment is None for _txdir, _record, moment, _precise in candidates):
            raise EconomyError("completed transaction order is ambiguous; specify a transaction id")
        by_second: dict[datetime, list[tuple[Path, dict[str, Any], datetime | None, bool]]] = {}
        for candidate in candidates:
            assert candidate[2] is not None
            by_second.setdefault(candidate[2].replace(microsecond=0), []).append(candidate)
        for group in by_second.values():
            if len(group) > 1 and (any(not item[3] for item in group) or len({item[2] for item in group}) != len(group)):
                raise EconomyError("completed transaction order is ambiguous within one second")
    candidates.sort(key=lambda item: item[2] or datetime.min.replace(tzinfo=timezone.utc))
    return [(txdir, record) for txdir, record, _moment, _precise in candidates]


def _latest(home: Path) -> Path | None:
    complete = _ordered_completed(home)
    return complete[-1][0] if complete else None


def _latest_snapshot_hash(home: Path, relpath: str) -> str | None:
    for _txdir, record in reversed(_ordered_completed(home, relpath)):
        for entry in record.get("files", []):
            if isinstance(entry, dict) and entry.get("path") == relpath: return entry.get("after_hash")
    return None


@dataclass
class PlannedFile:
    relpath: str; before: bytes | None; after: bytes | None; action: str
    owned_paths: tuple[tuple[str | None, str], ...] = (); collision: bool = False
    @property
    def before_hash(self) -> str: return MISSING_HASH if self.before is None else _sha256_bytes(self.before)
    @property
    def after_hash(self) -> str: return MISSING_HASH if self.after is None else _sha256_bytes(self.after)


def _plan_home(home: Path, m: dict[str, Any], policy: str | None = None, *, include_defaults: bool = False) -> list[PlannedFile]:
    home = _validate_home(home); policy = policy if policy is not None else _load_policy(m)[0]
    desired, path = _desired_config(m, include_defaults), _safe_home_path(home, CONFIG_REL)
    before = _read_bytes(path); text = "" if before is None else before.decode("utf-8")
    after_text = _patch_config(text, desired)
    if before is None: after_text = CONFIG_MARKER + "\n" + after_text
    after = after_text.encode()
    files = [PlannedFile(CONFIG_REL, before, after, "create" if before is None else ("noop" if before == after else "update"), tuple(desired))]
    for n, spec in m["routing"].items():
        rel = f"{n}.config.toml"; before = _read_bytes(_safe_home_path(home, rel)); after = _route_text(n, spec).encode()
        owned = before is None or (_contains_marker(before.decode("utf-8", errors="strict"), ROUTE_MARKER_PREFIX + n)
                                   and _latest_snapshot_hash(home, rel) == _sha256_bytes(before))
        files.append(PlannedFile(rel, before, after, "create" if before is None else ("noop" if before == after else ("update" if owned else "collision")), collision=not owned))
    for n in m.get("retired_profiles", []):
        rel = f"{n}.config.toml"; before = _read_bytes(_safe_home_path(home, rel))
        if before is None: files.append(PlannedFile(rel, None, None, "noop")); continue
        owned = (_contains_marker(before.decode("utf-8", errors="strict"), ROUTE_MARKER_PREFIX + n)
                 and _latest_snapshot_hash(home, rel) == _sha256_bytes(before))
        files.append(PlannedFile(rel, before, None, "delete" if owned else "collision", collision=not owned))
    before = _read_bytes(_safe_home_path(home, AGENTS_REL)); existing = None if before is None else before.decode("utf-8")
    after = _merge_policy(existing, policy).encode(); files.append(PlannedFile(AGENTS_REL, before, after, "create" if before is None else ("noop" if before == after else "update")))
    for n, spec in m["agents"].items():
        rel = f"agents/{n}.toml"; before = _read_bytes(_safe_home_path(home, rel)); after = _agent_text(n, spec).encode()
        owned = before is None or (_contains_marker(before.decode("utf-8", errors="strict"), AGENT_MARKER_PREFIX + n)
                                   and _latest_snapshot_hash(home, rel) == _sha256_bytes(before))
        files.append(PlannedFile(rel, before, after, "create" if before is None else ("noop" if before == after else ("update" if owned else "collision")), collision=not owned))
    return files


def _backup_name(relpath: str) -> str: return relpath.replace("/", "__").replace("\\", "__") + ".before"
def _write_record(txdir: Path, record: dict[str, Any]) -> None: _write_atomic(_safe_backup_path(txdir, "transaction.json"), (json.dumps(record, indent=2, sort_keys=True) + "\n").encode())


def _config_record(item: PlannedFile) -> dict[str, Any]:
    before = "" if item.before is None else item.before.decode(); after = "" if item.after is None else item.after.decode()
    return {"path": item.relpath, "kind": "owned_config", "before_exists": item.before is not None, "before_hash": item.before_hash,
            "after_hash": item.after_hash, "action": item.action, "owned_keys": [_path_name(p) for p in item.owned_paths],
            "before_state": _config_state(before, item.owned_paths), "after_state": _config_state(after, item.owned_paths)}


def _regular_record(item: PlannedFile) -> dict[str, Any]:
    result = {"path": item.relpath, "kind": "bytes", "before_exists": item.before is not None, "before_hash": item.before_hash,
              "after_hash": item.after_hash, "action": item.action}
    if item.before is not None: result["backup"] = _backup_name(item.relpath)
    return result


def _refresh_noop_provenance(home: Path, planned: list[PlannedFile], manifest_hash: str,
                             policy_sha256: str | None = None) -> bool:
    latest = _latest(home)
    if latest is None: return False
    metadata_path = _safe_backup_path(latest, "transaction.json")
    metadata_before = metadata_path.read_bytes(); record = _read_tx(latest)
    manifest_matches = manifest_hash in {record.get("manifest_sha256"), record.get("verified_manifest_sha256")}
    policy_recorded = "policy_sha256" in record
    policy_matches = (policy_sha256 is None or not policy_recorded
                      or policy_sha256 in {record.get("policy_sha256"), record.get("verified_policy_sha256")})
    if manifest_matches and policy_matches: return False
    # The no-op plan is the evidence for this digest. Recheck it immediately
    # before updating provenance so concurrent profile edits cannot be blessed.
    for item in planned:
        if item.action != "noop" or _read_bytes(_safe_home_path(home, item.relpath)) != item.before:
            raise EconomyError(f"profile changed before provenance refresh: {item.relpath}")
    if metadata_path.read_bytes() != metadata_before: raise EconomyError("transaction metadata changed before provenance refresh")
    if not manifest_matches:
        record["verified_manifest_sha256"] = manifest_hash
    if policy_recorded and not policy_matches:
        record["verified_policy_sha256"] = policy_sha256
    record["verified_at"] = datetime.now(timezone.utc).isoformat()
    _write_record(latest, record)
    return True


def _install_home(home: Path, m: dict[str, Any], manifest_hash: str, policy: str | None = None,
                  policy_sha256: str | None = None, *, include_defaults: bool = False,
                  refresh_provenance: bool = False) -> dict[str, Any]:
    home = _validate_home(home)
    if _pending(home): raise EconomyError("pending transaction requires recover: " + ", ".join(d.name for d in _pending(home)))
    if policy is None or policy_sha256 is None:
        loaded_policy, loaded_policy_sha256 = _load_policy(m)
        if policy is None: policy = loaded_policy
        if policy_sha256 is None: policy_sha256 = loaded_policy_sha256
    planned = _plan_home(home, m, policy, include_defaults=include_defaults); collisions = [x.relpath for x in planned if x.collision]
    if collisions: raise EconomyError("unowned managed-file collision: " + ", ".join(collisions))
    changed = [x for x in planned if x.action != "noop"]; result = {"home": str(home), "changed": [x.relpath for x in changed], "noop": not changed}
    if not changed:
        if refresh_provenance:
            result["provenance_refreshed"] = _refresh_noop_provenance(home, planned, manifest_hash, policy_sha256)
        return result
    for item in changed:
        if item.relpath != CONFIG_REL and item.before is not None: _assert_regular_backup_safe(item.relpath, item.before)
    for x in planned:
        if _read_bytes(_safe_home_path(home, x.relpath)) != x.before: raise EconomyError(f"profile changed during planning: {x.relpath}")
    txid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    root = _txroot(home); root.mkdir(parents=True, exist_ok=True)
    if _is_reparse(root): raise EconomyError("transaction directory must not be a symlink, junction, or reparse point")
    txdir = root / txid
    if not _is_relative_to(txdir.resolve(strict=False), root.resolve()): raise EconomyError("transaction directory escapes home")
    txdir.mkdir(exist_ok=False)
    records = [_config_record(x) if x.relpath == CONFIG_REL else _regular_record(x) for x in changed]
    metadata = {"schema_version": 2, "status": "preparing", "transaction": txid, "manifest_sha256": manifest_hash,
                "policy_sha256": policy_sha256,
                "created_at": datetime.now(timezone.utc).isoformat(), "home": str(home), "before_directories": {"agents": (home / "agents").is_dir()}, "files": records}
    _write_record(txdir, metadata)
    for item, entry in zip(changed, records):
        if entry["kind"] == "bytes" and item.before is not None:
            backup = _safe_backup_path(txdir, entry["backup"]); _write_atomic(backup, item.before)
            if _read_bytes(backup) != item.before: raise EconomyError(f"backup verification failed: {item.relpath}")
    try:
        for item in changed:
            if _read_bytes(_safe_home_path(home, item.relpath)) != item.before: raise EconomyError(f"profile changed before write: {item.relpath}")
            _mutate(home, item.relpath, item.after)
        for item in changed:
            if _read_bytes(_safe_home_path(home, item.relpath)) != item.after: raise EconomyError(f"post-write verification failed: {item.relpath}")
        metadata["status"] = "complete"; _write_record(txdir, metadata)
    except Exception as original:
        try: _recover_transaction(home, txdir)
        except EconomyError as recovery: raise EconomyError(f"install failed; recovery also failed: {recovery}") from original
        raise
    result["transaction"] = txid; return result


def _entry_path(entry: dict[str, Any]) -> str:
    rel = entry.get("path")
    if not isinstance(rel, str): raise EconomyError("transaction contains unsafe path")
    path = _safe_relpath(rel); parts = path.parts
    allowed = rel.replace("\\", "/") in {CONFIG_REL, AGENTS_REL}
    if len(parts) == 1 and parts[0].endswith(".config.toml"):
        allowed = allowed or SAFE_NAME.fullmatch(parts[0][:-len(".config.toml")]) is not None
    if len(parts) == 2 and parts[0] == "agents" and parts[1].endswith(".toml"):
        allowed = allowed or SAFE_NAME.fullmatch(parts[1][:-len(".toml")]) is not None
    if not allowed: raise EconomyError(f"transaction contains unmanaged path: {rel!r}")
    return "/".join(parts)


def _entry_keys(entry: dict[str, Any]) -> tuple[tuple[str | None, str], ...]:
    names, allowed = entry.get("owned_keys"), {_path_name(p): p for p in ALL_CONFIG_PATHS}
    if not isinstance(names, list) or not names or len(set(names)) != len(names) or any(n not in allowed for n in names): raise EconomyError("transaction has invalid config ownership")
    return tuple(allowed[n] for n in names)


def _validate_state(state: Any, keys: tuple[tuple[str | None, str], ...]) -> dict[str, Any]:
    hash_keys = ("unowned_lexical_sha256", "unowned_semantic_sha256", "mcp_semantic_sha256")
    if (not isinstance(state, dict) or set(state) != {"owned", *hash_keys} or not isinstance(state.get("owned"), dict)
            or any(not re.fullmatch(r"[0-9a-f]{64}", str(state.get(key, ""))) for key in hash_keys)): raise EconomyError("transaction has invalid config state")
    if set(state["owned"]) != {_path_name(p) for p in keys}: raise EconomyError("transaction has invalid config state")
    for saved in state["owned"].values():
        if not isinstance(saved, dict) or not isinstance(saved.get("present"), bool): raise EconomyError("transaction has invalid config state")
        if saved["present"]:
            if set(saved) != {"present", "value"} or not _valid_scalar(saved.get("value")): raise EconomyError("transaction has invalid owned scalar")
        elif set(saved) != {"present"}: raise EconomyError("transaction has invalid config state")
    return state


def _classify_config(content: bytes | None, entry: dict[str, Any], keys: tuple[tuple[str | None, str], ...]) -> str:
    if content is None: return "before" if not entry.get("before_exists") else "other"
    try: state = _config_state(content.decode(), keys)
    except (UnicodeDecodeError, EconomyError): return "other"
    if state == entry["before_state"]: return "before"
    if state == entry["after_state"]: return "after"
    return "other"


def _restore_config(home: Path, entry: dict[str, Any], target_name: str) -> None:
    rel, keys = _entry_path(entry), _entry_keys(entry); target = _validate_state(entry[target_name], keys)
    if target_name == "before_state" and not entry.get("before_exists"): _mutate(home, rel, None); return
    current = _read_bytes(_safe_home_path(home, rel))
    if current is None: raise EconomyError("config.toml missing; refusing restore")
    desired = {p: (target["owned"][_path_name(p)]["value"] if target["owned"][_path_name(p)]["present"] else _ABSENT) for p in keys}
    restored_text = _patch_config(current.decode(), desired)
    # Appended [windows] and [agents] tables can be structural wrappers only.
    # Try bounded removals of those exact trailing headers and accept one only
    # when its saved full config state matches.
    candidates = [restored_text]
    for _ in range(2):
        expanded = []
        for candidate in candidates:
            match = re.search(r"(?m)(?:^|\r?\n)\[(?:windows|agents)\](?:\r?\n)?\Z", candidate)
            if match:
                prefix = candidate[:match.start()]
                if match.group(0).startswith("\r\n"):
                    prefix = candidate[:match.start() + 2]
                elif match.group(0).startswith("\n"):
                    prefix = candidate[:match.start() + 1]
                expanded.extend([prefix, prefix[:-1] if prefix.endswith("\n") else prefix,
                                 prefix[:-2] if prefix.endswith("\r\n") else prefix])
        candidates.extend(expanded)
    def matches(candidate: str) -> bool:
        try: return _config_state(candidate, keys) == target
        except EconomyError: return False
    selected = next((candidate for candidate in candidates if matches(candidate)), None)
    if selected is None: raise EconomyError("cannot restore config structure without touching unowned text")
    _mutate(home, rel, selected.encode())


def _verified_backup(txdir: Path, entry: dict[str, Any]) -> bytes | None:
    if not entry.get("before_exists"): return None
    rel, expected = _entry_path(entry), _backup_name(_entry_path(entry))
    if entry.get("backup") != expected: raise EconomyError(f"unsafe backup name: {rel}")
    data = _safe_backup_path(txdir, expected).read_bytes()
    if _sha256_bytes(data) != entry.get("before_hash"): raise EconomyError(f"backup hash mismatch: {rel}")
    return data


def _assert_entry_state(home: Path, entry: dict[str, Any], expected: str) -> None:
    """Compare immediately before mutation; never overwrite a raced edit."""
    rel = _entry_path(entry); actual = _read_bytes(_safe_home_path(home, rel))
    if entry.get("kind") == "owned_config":
        observed = _classify_config(actual, entry, _entry_keys(entry))
    elif entry.get("kind") == "bytes":
        digest = MISSING_HASH if actual is None else _sha256_bytes(actual)
        observed = "before" if digest == entry.get("before_hash") else ("after" if digest == entry.get("after_hash") else "other")
    else: raise EconomyError("transaction has invalid file kind")
    if observed != expected: raise EconomyError(f"concurrent edit detected in {rel}; refusing mutation")


def _recover_transaction(home: Path, txdir: Path) -> dict[str, Any]:
    record = _read_tx(txdir)
    if record.get("schema_version") == 1:
        return _recover_legacy_transaction(home, txdir, record)
    if record.get("schema_version") != 2 or record.get("status") not in {"preparing", "rolling_back", "recovery_failed"}: raise EconomyError("transaction is not recoverable")
    entries = record.get("files")
    if not isinstance(entries, list) or not entries: raise EconomyError("transaction has invalid file records")
    operations = []
    for entry in entries:
        if not isinstance(entry, dict): raise EconomyError("transaction has invalid file record")
        rel, actual = _entry_path(entry), _read_bytes(_safe_home_path(home, _entry_path(entry)))
        if entry.get("kind") == "owned_config":
            keys = _entry_keys(entry); _validate_state(entry.get("before_state"), keys); _validate_state(entry.get("after_state"), keys)
            state = _classify_config(actual, entry, keys)
        elif entry.get("kind") == "bytes":
            digest = MISSING_HASH if actual is None else _sha256_bytes(actual)
            state = "before" if digest == entry.get("before_hash") else ("after" if digest == entry.get("after_hash") else "other")
            if state == "after" and entry.get("before_exists"): _verified_backup(txdir, entry)
        else: raise EconomyError("transaction has invalid file kind")
        if state == "other":
            record["status"], record["recovery_failures"] = "recovery_failed", [rel]; _write_record(txdir, record)
            raise EconomyError(f"concurrent edit detected in {rel}; refusing recovery")
        operations.append((entry, state))
    try:
        for entry, state in reversed(operations):
            if state == "before": continue
            _assert_entry_state(home, entry, "after")
            if entry["kind"] == "owned_config": _restore_config(home, entry, "before_state")
            else: _mutate(home, _entry_path(entry), _verified_backup(txdir, entry))
        for entry, _ in operations:
            actual = _read_bytes(_safe_home_path(home, _entry_path(entry)))
            if entry["kind"] == "owned_config":
                if _classify_config(actual, entry, _entry_keys(entry)) != "before": raise EconomyError(f"recovery verification failed: {_entry_path(entry)}")
            elif (MISSING_HASH if actual is None else _sha256_bytes(actual)) != entry.get("before_hash"): raise EconomyError(f"recovery verification failed: {_entry_path(entry)}")
    except Exception as exc:
        record["status"], record["recovery_failures"] = "recovery_failed", [type(exc).__name__]; _write_record(txdir, record); raise
    record["status"], record["recovered_at"] = "recovered", datetime.now(timezone.utc).isoformat(); record.pop("recovery_failures", None); _write_record(txdir, record)
    return {"home": str(home), "transaction": record["transaction"], "recovered": True}


def _recover_home(home: Path) -> dict[str, Any]:
    home = _validate_home(home)
    pending = _pending(home)
    if not pending: return {"home": str(home), "recovered": [], "noop": True}
    recovered = []
    for tx in pending:
        if not (tx / "transaction.json").exists():
            # transaction.json is the first committed file. With no record,
            # profile mutation could not have started; remove only its known
            # atomic temp artifact (or an empty directory).
            children = list(tx.iterdir())
            for child in children:
                if (_is_reparse(child) or not child.is_file()
                        or re.fullmatch(r"\.transaction\.json\.economy-[0-9a-f]{32}\.tmp", child.name) is None):
                    raise EconomyError(f"orphan transaction {tx.name} contains unexpected data")
            for child in children:
                if not _is_relative_to(child.resolve(strict=False), tx.resolve()): raise EconomyError("orphan transaction path escapes directory")
                child.unlink()
            tx.rmdir(); recovered.append(tx.name); continue
        recovered.append(_recover_transaction(home, tx)["transaction"])
    return {"home": str(home), "recovered": recovered, "noop": False}


def _preferences(home: Path, m: dict[str, Any]) -> dict[str, Any]:
    raw = _read_bytes(_safe_home_path(home, CONFIG_REL)); parsed = {} if raw is None else _config_semantics(raw.decode())
    current = {k: (None if parsed.get(k, _ABSENT) is _ABSENT else (parsed[k] if _valid_scalar(parsed[k]) else "<unsupported>")) for k in ROOT_KEYS}
    canonical = {k: m["routing"]["DEFAULT"][k] for k in ROOT_KEYS}
    return {"current": current, "canonical": canonical, "matches_canonical": current == canonical}


def _plan_output(home: Path, m: dict[str, Any], policy: str, defaults: bool) -> dict[str, Any]:
    plan = _plan_home(home, m, policy, include_defaults=defaults)
    return {"home": str(home), "changes": [{"path": x.relpath, "action": x.action} for x in plan], "collisions": [x.relpath for x in plan if x.collision],
            "pending_transactions": [x.name for x in _pending(home)], "root_preferences": _preferences(home, m), "defaults_included": defaults}


def _verify_home(home: Path, m: dict[str, Any], manifest_hash: str, policy: str, *, include_defaults: bool = False,
                 policy_sha256: str | None = None) -> dict[str, Any]:
    if policy_sha256 is None: policy_sha256 = _load_policy(m)[1]
    plan, failures, checks, notices = _plan_home(home, m, policy, include_defaults=include_defaults), [], [], []
    if _pending(home): failures.append("pending transaction requires recover")
    latest = _latest(home)
    if latest is None: failures.append("completed transaction metadata missing")
    else:
        provenance = _read_tx(latest)
        if manifest_hash not in {provenance.get("manifest_sha256"), provenance.get("verified_manifest_sha256")}:
            # A manifest can change only its account alias/path while leaving
            # every managed artifact identical. In that case verify the live
            # artifacts, but preserve the historical transaction unchanged.
            # Any create/update/delete/collision still requires a matching
            # transaction digest (or an explicit no-op sync provenance refresh).
            managed_files_match = bool(plan) and all(
                item.action == "noop" and not item.collision for item in plan
            )
            if managed_files_match and not _pending(home):
                notices.append("manifest differs from historical transaction; all managed files match and history was preserved")
            else:
                failures.append("manifest hash differs from installed or no-op-verified transaction")
        recorded_policy_hashes = {provenance.get("policy_sha256"), provenance.get("verified_policy_sha256")} - {None}
        if recorded_policy_hashes and policy_sha256 not in recorded_policy_hashes:
            failures.append("policy hash differs from installed or no-op-verified transaction")
    for item in plan:
        if item.collision: failures.append(f"unowned collision in {item.relpath}")
        elif item.action == "noop": checks.append(item.relpath)
        elif item.relpath == CONFIG_REL and not include_defaults: failures.append("managed settings differ in config.toml")
        else: failures.append(f"managed content differs in {item.relpath}")
    prefs = _preferences(home, m)
    if not prefs["matches_canonical"]: (failures if include_defaults else notices).append("root preferences differ from canonical DEFAULT")
    return {"home": str(home), "ok": not failures, "checks": checks, "failures": failures, "notices": notices, "root_preferences": prefs, "defaults_included": include_defaults}


def _resolve_tx(home: Path, txid: str | None) -> Path:
    if txid:
        _name("transaction", txid); root = _txroot(home); candidate = root / txid
        if _is_reparse(candidate) or not candidate.is_dir() or not _is_relative_to(candidate.resolve(), root.resolve()): raise EconomyError(f"transaction not found: {txid}")
        return candidate
    latest = _latest(home)
    if latest is None: raise EconomyError("no completed transaction found")
    return latest


def _legacy_preflight(home: Path, txdir: Path, record: dict[str, Any]) -> list[tuple[dict[str, Any], str, bytes | None]]:
    entries = record.get("files")
    if not isinstance(entries, list): raise EconomyError("invalid legacy transaction")
    prepared = []
    for entry in entries:
        rel, actual = _entry_path(entry), _read_bytes(_safe_home_path(home, _entry_path(entry)))
        digest = MISSING_HASH if actual is None else _sha256_bytes(actual)
        state = "before" if digest == entry.get("before_hash") else ("after" if digest == entry.get("after_hash") else "other")
        if state == "other": raise EconomyError(f"drift detected in {rel}; refusing legacy rollback recovery")
        before = _verified_backup(txdir, entry)
        if before is not None: _assert_regular_backup_safe(rel, before)
        prepared.append((entry, state, before))
    return prepared


def _recover_legacy_transaction(home: Path, txdir: Path, record: dict[str, Any]) -> dict[str, Any]:
    if record.get("status") not in {"rolling_back_legacy", "legacy_recovery_failed"}: raise EconomyError("legacy transaction is not recoverable")
    try:
        prepared = _legacy_preflight(home, txdir, record)
        for entry, state, before in reversed(prepared):
            if state == "before": continue
            rel = _entry_path(entry); actual = _read_bytes(_safe_home_path(home, rel))
            if (MISSING_HASH if actual is None else _sha256_bytes(actual)) != entry.get("after_hash"): raise EconomyError(f"concurrent edit detected in {rel}; refusing legacy rollback")
            _mutate(home, rel, before)
        for entry, _state, _before in prepared:
            rel = _entry_path(entry); actual = _read_bytes(_safe_home_path(home, rel))
            if (MISSING_HASH if actual is None else _sha256_bytes(actual)) != entry.get("before_hash"): raise EconomyError(f"legacy rollback verification failed: {rel}")
    except Exception as exc:
        record["status"], record["recovery_failures"] = "legacy_recovery_failed", [type(exc).__name__]; _write_record(txdir, record); raise
    record["status"], record["rolled_back_at"] = "rolled_back", datetime.now(timezone.utc).isoformat(); record.pop("recovery_failures", None); _write_record(txdir, record)
    return {"home": str(home), "transaction": record["transaction"], "recovered": True}


def _rollback_home(home: Path, manifest_hash: str, txid: str | None) -> dict[str, Any]:
    home = _validate_home(home)
    if _pending(home): raise EconomyError("pending transaction requires recover before rollback")
    txdir, prepared = _resolve_tx(home, txid), []; record = _read_tx(_resolve_tx(home, txid))
    if record.get("status") != "complete": raise EconomyError("transaction is not completed")
    if record["schema_version"] == 1:
        _legacy_preflight(home, txdir, record)
        record["status"], record["rollback_started_at"] = "rolling_back_legacy", datetime.now(timezone.utc).isoformat(); _write_record(txdir, record)
        _recover_legacy_transaction(home, txdir, record)
    else:
        entries = record.get("files")
        if not isinstance(entries, list): raise EconomyError("invalid transaction")
        for entry in entries:
            rel, actual = _entry_path(entry), _read_bytes(_safe_home_path(home, _entry_path(entry)))
            if entry.get("kind") == "owned_config":
                keys = _entry_keys(entry); _validate_state(entry.get("before_state"), keys); _validate_state(entry.get("after_state"), keys)
                if _classify_config(actual, entry, keys) != "after": raise EconomyError(f"drift detected in {rel}; refusing rollback")
                prepared.append((entry, None))
            elif entry.get("kind") == "bytes":
                if (MISSING_HASH if actual is None else _sha256_bytes(actual)) != entry.get("after_hash"): raise EconomyError(f"drift detected in {rel}; refusing rollback")
                prepared.append((entry, _verified_backup(txdir, entry)))
            else: raise EconomyError("invalid transaction file kind")
        # Persist the recoverable intent before the first profile mutation.
        # recover treats both untouched after-state and already-restored
        # before-state entries idempotently.
        record["status"], record["rollback_started_at"] = "rolling_back", datetime.now(timezone.utc).isoformat()
        _write_record(txdir, record)
        for entry, before in reversed(prepared):
            _assert_entry_state(home, entry, "after")
            if entry["kind"] == "owned_config": _restore_config(home, entry, "before_state")
            else: _mutate(home, _entry_path(entry), before)
        for entry, _before in prepared: _assert_entry_state(home, entry, "before")
    record["status"], record["rolled_back_at"] = "rolled_back", datetime.now(timezone.utc).isoformat(); _write_record(txdir, record)
    return {"home": str(home), "transaction": record["transaction"], "rolled_back": True}


def _run_mode(mode: str, args: argparse.Namespace) -> int:
    manifest_path = _resolve_manifest_path(args.manifest)
    manifest, digest = _load_manifest(manifest_path); policy, policy_digest = _load_policy(manifest, manifest_path.parent)
    targets = _profile_paths(manifest, args.account, args.home); defaults = bool(getattr(args, "include_defaults", False))
    if mode == "doctor":
        # Read configuration only; do not start Codex, inspect auth, or call models.
        for _, home in targets:
            data = _read_bytes(_safe_home_path(home, CONFIG_REL))
            if data is not None: _config_semantics(data.decode("utf-8"))
        print(json.dumps({"python": sys.version.split()[0], "codex_on_path": bool(shutil.which("codex")),
            "homes": [{"account": name, "exists": home.is_dir(), "config_exists": (home / CONFIG_REL).is_file()} for name, home in targets],
            "routes": manifest["routing"], "model_availability": "not_checked",
            "next": "Confirm these models and efforts in your client, then run plan for the chosen home."}, indent=2))
        return 0
    if mode == "plan":
        profiles = [_plan_output(home, manifest, policy, defaults) for _, home in targets]
        output = {"mode": mode, "profiles": profiles}; print(json.dumps(output, indent=2, sort_keys=True))
        return 2 if any(p["collisions"] or p["pending_transactions"] for p in profiles) else 0
    if mode in {"install", "sync", "set-defaults"}:
        apply_defaults = mode == "set-defaults"
        for _, home in targets:
            if _pending(home): raise EconomyError(f"{home}: pending transaction requires recover")
            collisions = [x.relpath for x in _plan_home(home, manifest, policy, include_defaults=apply_defaults) if x.collision]
            if collisions: raise EconomyError(f"{home}: unowned collisions: " + ", ".join(collisions))
        output = {"mode": mode, "profiles": [_install_home(home, manifest, digest, policy, policy_sha256=policy_digest, include_defaults=apply_defaults,
                                                             refresh_provenance=(mode == "sync")) for _, home in targets]}
    elif mode == "verify": output = {"mode": mode, "profiles": [_verify_home(home, manifest, digest, policy, include_defaults=defaults,
                                                                                policy_sha256=policy_digest) for _, home in targets]}
    elif mode == "recover": output = {"mode": mode, "profiles": [_recover_home(home) for _, home in targets]}
    elif mode == "rollback": output = {"mode": mode, "profiles": [_rollback_home(home, digest, args.transaction) for _, home in targets]}
    else: raise EconomyError(f"unknown mode: {mode}")
    print(json.dumps(output, indent=2, sort_keys=True))
    return 1 if mode == "verify" and any(not p["ok"] for p in output["profiles"]) else 0


def _self_test() -> int:
    manifest_path = _resolve_manifest_path()
    manifest, digest = _load_manifest(manifest_path); policy = _load_policy(manifest, manifest_path.parent)[0]
    with WorkspaceTempDirectory(SCRIPT_DIR, prefix="economy-selftest-") as root:
        home = Path(root) / "home"; home.mkdir()
        original = 'model = "user-choice"\nservice_tier = "priority"\n\n[agents]\ncustom = true\n'
        (home / CONFIG_REL).write_text(original, encoding="utf-8")
        installed = _install_home(home, manifest, digest, policy)
        after = (home / CONFIG_REL).read_text(encoding="utf-8")
        if installed["noop"] or 'model = "user-choice"' not in after or 'service_tier = "priority"' not in after: raise AssertionError("ordinary install changed root preferences")
        if not _verify_home(home, manifest, digest, policy)["ok"]: raise AssertionError("verify failed")
        _rollback_home(home, digest, installed["transaction"])
        if (home / CONFIG_REL).read_text(encoding="utf-8") != original: raise AssertionError("config rollback was not lexical")
    print("self-test: PASS preserve_root_preferences=yes scoped_config_rollback=yes")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bounded Codex Economy installer"); sub = parser.add_subparsers(dest="mode", required=True)
    for mode in ("doctor", "plan", "install", "sync", "set-defaults", "verify", "recover", "rollback"):
        command = sub.add_parser(mode); command.add_argument("--manifest"); command.add_argument("--home"); command.add_argument("--account")
        if mode in {"plan", "verify"}: command.add_argument("--include-defaults", action="store_true")
        if mode == "rollback": command.add_argument("--transaction")
    command = sub.add_parser("init"); command.add_argument("--home", required=True); command.add_argument("--account", default="main")
    for tier, effort in (("light", "high"), ("balanced", "medium"), ("deep", "high")):
        command.add_argument(f"--{tier}-model", help="Use model IDs confirmed available to this account; provide all three together")
        command.add_argument(f"--{tier}-effort", default=effort, choices=("none", "minimal", "low", "medium", "high", "xhigh"))
    sub.add_parser("self-test"); return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.mode == "self-test":
        try: return _self_test()
        except (AssertionError, EconomyError, OSError, UnicodeError) as exc: print(f"self-test: FAIL {exc}", file=sys.stderr); return 1
    if args.mode == "init":
        try:
            choices = {tier: (getattr(args, tier + "_model"), getattr(args, tier + "_effort"))
                       for tier in ("light", "balanced", "deep") if getattr(args, tier + "_model")}
            print(f"Created {_init_manifest(args.home, args.account, model_choices=choices or None)}")
            return 0
        except (EconomyError, OSError, UnicodeError) as exc:
            print(f"economy: {exc}", file=sys.stderr); return 2
    try: return _run_mode(args.mode, args)
    except (EconomyError, OSError, UnicodeError) as exc: print(f"economy: {exc}", file=sys.stderr); return 2


if __name__ == "__main__": raise SystemExit(main())
