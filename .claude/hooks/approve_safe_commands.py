#!/usr/bin/env python3
"""
PreToolUse hook for Claude Code — auto-approves safe Bash commands,
blocks dangerous ones, and lets everything else go through the standard prompt.

Security model:
- ALL segments of piped/chained commands must pass is_safe_segment()
- Multi-line commands are normalized (heredocs stripped, comments removed,
  lines joined with ;). Control flow (if/while/for) → passthrough.
- Redirects (>, >>) allowed to: /dev/null, /tmp/, .tasks/, paths inside cwd.
  Redirects to home dir, system paths, etc. → passthrough.
- curl/wget are NOT auto-approved (passthrough) — exfiltration risk
- python3/ruby only approved for known project paths
- security (macOS Keychain) only approved for safe subcommands
- killall only approved for Xcode-related processes
- cp: dest must be inside cwd, sources also from /tmp/
- mv/ln: all paths must be inside cwd
- xargs: write commands (rm/cp/mv/ln/chmod) blocked, read-only commands allowed
- npm/pip install/add/remove are NOT auto-approved (passthrough)
"""

import json
import os
import re
import shlex
import sys


def _strip_env_vars(cmd: str) -> str:
    """Strip leading env var assignments: GIT_EDITOR=true cmd → cmd"""
    return re.sub(r'^(\w+=\S*\s+)+', '', cmd)


def _extract_subshell(cmd: str, start: int) -> tuple[str, int] | None:
    """Extract balanced $(...) starting at the '$' position.

    Returns (inner_command, end_pos) where end_pos is the index of the closing ')'.
    Returns None if parens are unbalanced.
    """
    if start + 1 >= len(cmd) or cmd[start] != '$' or cmd[start + 1] != '(':
        return None
    depth = 0
    i = start + 1  # at '('
    in_single = False
    in_double = False
    escaped = False
    while i < len(cmd):
        c = cmd[i]
        if escaped:
            escaped = False
            i += 1
            continue
        if c == '\\' and not in_single:
            escaped = True
            i += 1
            continue
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0:
                    inner = cmd[start + 2:i]
                    return (inner, i)
        i += 1
    return None


_MAX_SUBSHELL_DEPTH = 3


def _resolve_subshells(cmd: str, depth: int = 0) -> tuple[str, bool]:
    """Replace safe $(...) with placeholders, recursively.

    Returns (resolved_cmd, all_safe) where:
    - resolved_cmd has $(...) replaced with '__SUBSHELL__' if safe
    - all_safe is True if every $(...) contained only safe commands
    """
    if depth > _MAX_SUBSHELL_DEPTH:
        return (cmd, False)

    result = []
    i = 0
    all_safe = True
    in_single = False
    escaped = False

    while i < len(cmd):
        c = cmd[i]

        if escaped:
            escaped = False
            result.append(c)
            i += 1
            continue

        if c == '\\':
            escaped = True
            result.append(c)
            i += 1
            continue

        if c == "'" and not escaped:
            in_single = not in_single
            result.append(c)
            i += 1
            continue

        if in_single:
            result.append(c)
            i += 1
            continue

        # Arithmetic expansion $((expr)) — no command execution, always safe
        if c == '$' and i + 2 < len(cmd) and cmd[i + 1] == '(' and cmd[i + 2] == '(':
            close = cmd.find('))', i + 3)
            if close >= 0:
                result.append('__ARITH__')
                i = close + 2
                continue
            else:
                all_safe = False
                result.append(c)
                i += 1
                continue

        if c == '$' and i + 1 < len(cmd) and cmd[i + 1] == '(':
            extracted = _extract_subshell(cmd, i)
            if extracted is None:
                all_safe = False
                result.append(c)
                i += 1
                continue
            inner, end_pos = extracted
            # Heredoc pattern: $(cat <<'EOF'...EOF) — safe string interpolation
            if re.match(r'^cat\s+<<', inner):
                result.append('__SUBSHELL__')
                i = end_pos + 1
                continue
            # Recursively resolve nested subshells first
            resolved_inner, inner_ok = _resolve_subshells(inner, depth + 1)
            if not inner_ok:
                all_safe = False
                result.append(cmd[i:end_pos + 1])
                i = end_pos + 1
                continue
            # Check if the resolved inner command is safe
            if is_safe(resolved_inner):
                result.append('__SUBSHELL__')
            else:
                all_safe = False
                result.append(cmd[i:end_pos + 1])
            i = end_pos + 1
            continue

        result.append(c)
        i += 1

    return (''.join(result), all_safe)


def _split_command_chain(cmd: str) -> list[str]:
    """Split a command string into segments by pipes, &&, ||, ;.

    Quote-aware: pipes/operators inside single or double quotes are not split on.
    """
    segments: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    escaped = False
    i = 0

    while i < len(cmd):
        c = cmd[i]

        if escaped:
            escaped = False
            current.append(c)
            i += 1
            continue

        if c == '\\' and not in_single:
            escaped = True
            current.append(c)
            i += 1
            continue

        if c == "'" and not in_double:
            in_single = not in_single
            current.append(c)
            i += 1
            continue

        if c == '"' and not in_single:
            in_double = not in_double
            current.append(c)
            i += 1
            continue

        if in_single or in_double:
            current.append(c)
            i += 1
            continue

        # Check for operators: &&, ||, |, ;
        rest = cmd[i:]
        op = None
        if rest.startswith('&&'):
            op = '&&'
        elif rest.startswith('||'):
            op = '||'
        elif c == '|':
            op = '|'
        elif c == ';':
            op = ';'

        if op:
            seg = ''.join(current).strip()
            if seg:
                segments.append(seg)
            current = []
            i += len(op)
            continue

        current.append(c)
        i += 1

    seg = ''.join(current).strip()
    if seg:
        segments.append(seg)

    return segments


def _contains_unsupported_shell_syntax(cmd: str) -> bool:
    """Return True for shell syntax that this regex-based parser does not model safely."""
    in_single_quotes = False
    in_double_quotes = False
    escaped = False
    index = 0

    while index < len(cmd):
        char = cmd[index]
        next_char = cmd[index + 1] if index + 1 < len(cmd) else ''
        prev_char = cmd[index - 1] if index > 0 else ''

        if escaped:
            escaped = False
            index += 1
            continue

        if char == '\\':
            escaped = True
            index += 1
            continue

        if char == "'" and not in_double_quotes:
            in_single_quotes = not in_single_quotes
            index += 1
            continue

        if char == '"' and not in_single_quotes:
            in_double_quotes = not in_double_quotes
            index += 1
            continue

        if in_single_quotes:
            index += 1
            continue

        if char == '`':
            return True

        if char == '$' and next_char == '(':
            return True

        if not in_double_quotes:
            if char in '<>' and next_char == '(':
                return True

            # Newlines are handled by _normalize_multiline() before this check
            # so they should not appear here; if they do, it's unsupported.
            if char == '\n':
                return True

            if char == '&' and prev_char not in {'>', '&'} and next_char not in {'&', '>'}:
                return True

        index += 1

    return False


def _is_safe_redirect_target(target: str) -> bool:
    """Return True if the redirect target is safe to write to automatically.

    Safe targets: fd duplication (&1, &2), /dev/null, /tmp/*, .tasks/*, cwd/*.
    """
    # fd duplication: >&2, 2>&1, etc.
    if target.startswith('&'):
        return True
    if target == '/dev/null':
        return True
    # /tmp/ is ephemeral (also /private/tmp/ on macOS)
    if target.startswith('/tmp/') or target.startswith('/private/tmp/'):
        return True
    # .tasks/ within project — already writable via permissions
    if target.startswith('.tasks/') or '/.tasks/' in target:
        return True
    # Paths inside cwd (project directory)
    if _path_inside_cwd(target):
        return True
    return False


def _has_dangerous_redirects(cmd: str) -> bool:
    """Check if the command has redirects to unsafe files.

    Safe redirects: fd duplication, /dev/null, /tmp/*, .tasks/*, cwd/*
    Dangerous: outside project, home directory dotfiles, system paths
    """
    # Strip quoted strings so ">" inside quotes doesn't trigger false positives
    stripped = re.sub(r'"[^"]*"', '""', cmd)
    stripped = re.sub(r"'[^']*'", "''", stripped)
    # Find all redirect patterns: optional fd number + > or >> + target
    # Target stops at shell metacharacters (;, |, &, ), space)
    redirects = re.finditer(r'(\d*)>{1,2}\s*([^\s;|&)]+)', stripped)
    for m in redirects:
        target = m.group(2)
        if not _is_safe_redirect_target(target):
            return True
    return False


def _extract_base(segment: str) -> str:
    """Extract the base command from a single segment (no pipes/chains).

    Strips redirects and env var assignments.
    """
    # Remove redirects: 2>&1, >/dev/null, 2>/dev/null, etc.
    cleaned = re.sub(r'\d*>[>&]?\d*\s*\S*', '', segment)
    cleaned = _strip_env_vars(cleaned.strip())
    return cleaned.strip()


def _paths_inside_cwd(cmd: str, skip_flags: bool = True) -> bool:
    """Return True only if every path argument resolves inside cwd."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return False
    # Extract non-flag arguments after the command itself
    paths = [t for t in tokens[1:] if not t.startswith('-')] if skip_flags else tokens[1:]
    if not paths:
        return False
    for p in paths:
        if not _path_inside_cwd(p):
            return False
    return True


def _file_op_targets_inside_cwd(cmd: str) -> bool:
    """For mv/ln: check that all path args are inside cwd."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return False
    args = [t for t in tokens[1:] if not t.startswith('-')]
    if len(args) < 2:
        return False
    for p in args:
        if not _path_inside_cwd(p):
            return False
    return True


def _cp_is_safe(cmd: str) -> bool:
    """For cp: destination must be inside cwd, sources can also be in /tmp/.

    Agents often clone/download to /tmp/ and then cp into the project.
    """
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return False
    args = [t for t in tokens[1:] if not t.startswith('-')]
    if len(args) < 2:
        return False
    # Last arg is destination — must be inside cwd
    dest = args[-1]
    if not _path_inside_cwd(dest):
        return False
    # Sources: must be inside cwd OR inside /tmp/
    for src in args[:-1]:
        if not _path_inside_cwd(src) and not _path_inside_tmp(src):
            return False
    return True


def _path_inside_cwd(path: str) -> bool:
    """Resolve symlinks and ensure the final path stays inside cwd."""
    cwd = os.path.realpath(os.getcwd())
    expanded_path = os.path.expanduser(path)
    resolved = os.path.realpath(os.path.join(cwd, expanded_path))

    try:
        return os.path.commonpath([cwd, resolved]) == cwd
    except ValueError:
        return False


def _path_inside_tmp(path: str) -> bool:
    """Check if path resolves inside /tmp/ (or /private/tmp/ on macOS)."""
    expanded = os.path.expanduser(path)
    if os.path.isabs(expanded):
        resolved = os.path.realpath(expanded)
    else:
        resolved = os.path.realpath(os.path.join(os.getcwd(), expanded))
    return (resolved.startswith('/tmp/') or resolved.startswith('/private/tmp/')
            or resolved in ('/tmp', '/private/tmp'))


# ── Sensitive path protection ───────────────────────────────────────────
# Paths that should never be auto-approved for reading.

_SENSITIVE_PATH_PATTERNS: list[re.Pattern] = [
    # macOS / Unix system secrets
    re.compile(r'/etc/(shadow|master\.passwd|sudoers)'),
    re.compile(r'\.ssh/'),
    re.compile(r'\.gnupg/'),
    re.compile(r'\.gpg'),
    # Cloud credentials
    re.compile(r'\.aws/'),
    re.compile(r'\.azure/'),
    re.compile(r'\.config/gcloud/'),
    re.compile(r'\.kube/config'),
    # Shell history (credentials often leaked here)
    re.compile(r'\.\w*_?history$'),
    re.compile(r'\.lesshst$'),
    # Package manager tokens
    re.compile(r'\.netrc$'),
    re.compile(r'\.npmrc$'),
    re.compile(r'\.pypirc$'),
    re.compile(r'\.gem/credentials$'),
    re.compile(r'\.docker/config\.json$'),
    re.compile(r'\.nuget/NuGet\.Config$'),
    # macOS Keychain and cookie stores
    re.compile(r'Library/Keychains/'),
    re.compile(r'Library/Cookies/'),
    # Browser credential stores
    re.compile(r'Login Data$'),
    re.compile(r'Cookies$'),
    re.compile(r'Web Data$'),
    # Certificates and keys (by extension)
    re.compile(r'\.(pem|key|p12|pfx|cer|crt|keystore|jks)$'),
    # Project-specific sensitive files (mirrors Claude deny rules)
    re.compile(r'\.env(\.|$)'),
    re.compile(r'(^|/)credentials', re.IGNORECASE),
    re.compile(r'(^|/)secret', re.IGNORECASE),
    # Fastlane match / signing (project-specific)
    re.compile(r'match/'),
    re.compile(r'Matchfile$'),
    re.compile(r'\.mobileprovision$'),
]

# Commands that read file content (as opposed to metadata-only like ls, find, stat)
_FILE_READING_COMMANDS = {
    'cat', 'less', 'head', 'tail', 'more',
    'strings', 'hexdump', 'od',
    'bat', 'pygmentize',
}


def _has_sensitive_path(cmd: str) -> bool:
    """Return True if any argument looks like a sensitive file path."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return False
    for token in tokens[1:]:
        if token.startswith('-'):
            continue
        expanded = os.path.expanduser(token)
        for pattern in _SENSITIVE_PATH_PATTERNS:
            if pattern.search(expanded):
                return True
    return False


def _is_safe_xargs(base: str) -> bool:
    """Check if xargs invokes a safe command.

    Strips xargs flags and evaluates the remaining command.
    xargs with no command defaults to echo (safe).
    """
    try:
        tokens = shlex.split(base)
    except ValueError:
        return False
    # Skip 'xargs' itself
    tokens = tokens[1:]
    # Skip xargs flags: -0, -n N, -I str, -P N, -L N, -t, -p, -r, --no-run-if-empty, etc.
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in ('-0', '-t', '-r', '--no-run-if-empty', '-x', '--null'):
            i += 1
        elif t in ('-n', '-I', '-P', '-L', '-s', '-J', '--max-args', '--max-procs',
                    '--replace', '--max-lines', '--max-chars'):
            i += 2  # flag + value
        elif t.startswith('-I') and len(t) > 2:
            i += 1  # -I{} combined form
        elif t.startswith('-n') and len(t) > 2 and t[2:].isdigit():
            i += 1  # -n5 combined form
        elif t.startswith('-P') and len(t) > 2 and t[2:].isdigit():
            i += 1  # -P4 combined form
        elif t.startswith('-'):
            i += 1  # unknown flag — skip conservatively
        else:
            break
    # Remaining tokens are the command xargs will execute
    cmd_tokens = tokens[i:]
    if not cmd_tokens:
        return True  # xargs with no command = echo (safe)
    # Write commands can't be safe via xargs — paths come from stdin
    # and we can't validate them at parse time.
    # Read-only commands (cat, head, etc.) are allowed since the agent
    # already has Read tool auto-approved — blocking them here is redundant.
    write_dependent = {'rm', 'cp', 'mv', 'ln', 'chmod', 'chown', 'chgrp'}
    if cmd_tokens[0] in write_dependent:
        return False
    # Reconstruct and check via is_safe_segment
    inner_cmd = shlex.join(cmd_tokens + ['placeholder_arg'])
    return is_safe_segment(inner_cmd)


def is_safe_segment(segment: str) -> bool:
    """Check if a single command segment (no pipes/chains) is safe."""
    base = _extract_base(segment)
    if not base:
        return True  # empty segment

    # --- Bare variable assignment: VAR=value (no command to execute) ---
    if re.match(r'^\w+=\S*$', base):
        return True

    # --- time / timeout: strip prefix and evaluate wrapped command ---
    # `time` only measures execution time, doesn't change behavior
    if re.match(r'^time(\s|$)', base):
        inner = re.sub(r'^time\s+(-[plv]\s+)*', '', base)
        if not inner:
            return False
        return is_safe_segment(inner)

    # `timeout` wraps a command with a time limit — evaluate the inner command
    if re.match(r'^timeout(\s|$)', base):
        inner = re.sub(r'^timeout\s+(-[kvs]\s+\S+\s+)*\S+\s+', '', base)
        if not inner:
            return False
        return is_safe_segment(inner)

    # --- xargs: evaluate the command it will execute ---
    if re.match(r'^xargs(\s|$)', base):
        return _is_safe_xargs(base)

    # --- Read-only file/system tools ---
    # NOTE: 'open' is handled separately below (URLs are not auto-approved)
    readonly_tools = [
        'true', 'false', 'test', r'\[',
        'cat', 'less', 'head', 'tail', 'more', 'wc', 'file', 'stat',
        'strings', 'hexdump', 'od', 'md5sum', 'shasum',
        'ls', 'tree', 'find', 'du', 'df', 'pwd', 'cd',
        'grep', 'rg', 'ag', 'ack', 'fzf',
        'tee',
        'sort', 'uniq', 'cut', 'diff', 'cmp', 'diffstat', 'comm',
        'tr', 'seq', 'column', 'fold', 'fmt', 'paste', 'join', 'nl',
        'echo', 'printf',
        'basename', 'dirname', 'realpath', 'readlink',
        'which', 'where', 'type',
        'whoami', 'uname', 'date', 'env', 'printenv',
        'sw_vers', 'uptime', 'sysctl', 'system_profiler',
        'man', 'tldr',
        'sleep', 'wait',
        'lsof', 'ps', 'top', 'nproc', 'getconf',
        'pbcopy', 'pbpaste',
        'jq', 'yq', 'xmllint',
        # file operations (touch only — cp/mv/ln handled separately)
        'touch',
        # archives
        'tar', 'zip', 'unzip', 'gzip', 'gunzip',
        # macOS utilities (killall handled separately)
        'xattr', 'ditto',
    ]
    for tool in readonly_tools:
        if re.match(rf'^{re.escape(tool)}(\s|$)', base):
            # File-reading commands must not read sensitive paths
            if tool in _FILE_READING_COMMANDS and _has_sensitive_path(base):
                return False
            return True

    # --- open: only local paths, NOT URLs (exfiltration/phishing risk) ---
    if re.match(r'^open(\s|$)', base):
        return not re.search(r'https?://', segment)

    # --- cp: dest inside cwd, sources also from /tmp/ ---
    if re.match(r'^cp\b', base):
        return _cp_is_safe(segment)

    # --- mv/ln: all paths must be inside cwd ---
    if re.match(r'^(mv|ln)\b', base):
        return _file_op_targets_inside_cwd(segment)

    # --- killall: only Xcode-related processes ---
    if re.match(r'^killall\s', base):
        allowed_processes = (
            r'^killall\s+(Simulator|CoreSimulatorService|'
            r'Xcode|xcodebuild|IBDesignablesAgent|IBAgent|'
            r'XCBBuildService|sourcekit-lsp|SourceKitService|'
            r'com\.apple\.dt\.SKAgent)\b'
        )
        return bool(re.match(allowed_processes, base))

    # --- awk/sed (safe unless writing files or executing commands) ---
    if re.match(r'^(awk|gawk|mawk|sed|gsed)(\s|$)', base):
        dangerous_awk = r'system\s*\(|"\s*\||\|\s*"|>\s*"|\bgetline\s*<'
        return not re.search(dangerous_awk, segment)

    # --- plutil / PlistBuddy (read-only flags) ---
    if re.match(r'^plutil\s+-p\b', base):
        return True
    if re.match(r'^/usr/libexec/PlistBuddy\s+-c\s+["\']?Print\b', base):
        return True
    if re.match(r'^defaults\s+read\b', base):
        return True

    # --- Git ---
    git_safe = [
        'status', 'log', 'diff', 'show', 'branch', 'tag', 'remote',
        'stash list', 'stash show', 'blame', 'shortlog', 'rev-parse',
        'ls-files', 'ls-tree', 'config', 'merge-base',
        'describe', 'name-rev', 'rev-list',
        'reset', 'lfs', 'worktree', 'clean -n',
        # read-only info commands
        'check-ignore', 'submodule', 'for-each-ref',
        'count-objects', 'reflog', 'notes',
        # write ops (allowed)
        'add', 'commit', 'checkout', 'switch', 'cherry-pick',
        'stash push', 'stash pop', 'stash apply', 'stash drop',
        'stash save', 'stash',
        'merge', 'rebase', 'fetch', 'pull',
        'restore', 'mv', 'rm',
    ]
    if re.match(r'^git\s', base):
        git_sub = re.sub(r'^git\s+', '', base)
        git_sub = re.sub(r'^(-C\s+\S+\s+)+', '', git_sub)
        git_sub = re.sub(r'^(-c\s+\S+=\S+\s+)+', '', git_sub)
        git_sub = re.sub(r'^(--git-dir[=\s]\S+\s+)+', '', git_sub)
        git_sub = re.sub(r'^(--work-tree[=\s]\S+\s+)+', '', git_sub)
        # git push — always passthrough (prompt user)
        if git_sub.startswith('push'):
            return False
        # Risky git flags — passthrough
        if re.search(r'--hard\b', segment) and git_sub.startswith('reset'):
            return False
        for pattern in git_safe:
            if git_sub.startswith(pattern):
                return True
        return False

    # --- Swift / Xcode toolchain ---
    # swift package init/update/resolve/fetch download code — passthrough
    if re.match(r'^swift\s+package\s+(init|update|resolve|fetch|add-dependency|remove-dependency)\b', base):
        return False
    swift_tools = [
        'swift', 'swiftc', 'swift-format', 'swiftlint', 'sourcekit-lsp',
        'xcodebuild', 'xcode-select', 'xcrun', 'xed',
        'xcresulttool', 'instruments', 'agvtool',
    ]
    for tool in swift_tools:
        if re.match(rf'^{re.escape(tool)}(\s|$)', base):
            return True

    # --- iOS ecosystem tools (without ruby/gem/security — handled separately) ---
    ios_tools = [
        'tuist', 'mise', 'mint', 'pod', 'carthage', 'periphery',
        'otool', 'nm', 'lipo', 'dwarfdump', 'dsymutil', 'size',
        'codesign',
        'idevice_id', 'ideviceinfo', 'idevicename', 'idevicepair',
        'symbolicatecrash', 'atos', 'sample', 'heap', 'leaks', 'vmmap',
        'malloc_history', 'xctool',
        # Ruby / Bundler (but NOT raw ruby or gem)
        'bundle', 'bundler', 'fastlane',
    ]
    for tool in ios_tools:
        if re.match(rf'^{re.escape(tool)}(\s|$)', base):
            return True

    # --- ruby: only for project scripts (Tools/) ---
    if re.match(r'^ruby\s', base):
        return bool(re.match(r'^ruby\s+(\./)?Tools/', base))

    # --- gem: only read-only subcommands ---
    if re.match(r'^gem\s', base):
        return bool(re.match(r'^gem\s+(list|info|which|environment|specification|contents)\b', base))

    # --- security (macOS Keychain): only safe subcommands ---
    if re.match(r'^security\s', base):
        safe_security = (
            r'^security\s+(find-identity|list-keychains|list-smartcards|'
            r'show-keychain-info|cms|verify-cert|find-certificate|'
            r'default-keychain|login-keychain)\b'
        )
        return bool(re.match(safe_security, base))

    # --- ./project CLI (all subcommands) ---
    if re.match(r'^\./(project|otp)\b', base):
        return True

    # --- Project scripts (.ai/, .claude/hooks/) ---
    if re.match(r'^bash\s+\.ai/', base):
        return True
    if re.match(r'^bash\s+\.claude/', base):
        return True

    # --- Docker (read-only) ---
    docker_ro = ['ps', 'images', 'logs', 'inspect', 'stats', 'top', 'port']
    if re.match(r'^docker\s', base):
        docker_sub = re.sub(r'^docker\s+', '', base)
        for sub in docker_ro:
            if docker_sub.startswith(sub):
                return True
        return False

    # --- Tools/ scripts (direct invocation, except dangerous ones) ---
    if re.match(r'^(bash\s+)?Tools/', base):
        dangerous_scripts = ['clean_env', 'bootstrap', 'artifactory_login']
        if not any(s in base for s in dangerous_scripts):
            return True

    # --- Homebrew (read-only) ---
    if re.match(r'^brew\s+(list|ls|info|search|config|doctor|deps|uses|leaves|outdated|desc|cat|home|log)\b', base):
        return True

    # --- Package managers (read-only + run/test/build, NO install/add/remove/ci) ---
    if re.match(r'^(npm|yarn|pip|cargo)\s+(list|ls|show|info|view|outdated|why|explain|audit|tree|metadata|freeze|check|run|test|build)\b', base):
        return True

    # --- python3: only known project paths ---
    if re.match(r'^python3\s+(\./?)?(\.ai/|\.claude/|Tools/)\S+\.py\b', base):
        return True

    # --- mkdir (safe) ---
    if re.match(r'^mkdir\b', base):
        return True

    # --- rm: auto-allow only for paths inside cwd, otherwise ask ---
    if re.match(r'^rm\b', base):
        return _paths_inside_cwd(segment)

    # --- Misc version/help ---
    if re.search(r'--version$|--help$', base):
        return True

    return False


def _strip_heredocs(cmd: str) -> str | None:
    """Remove heredoc bodies from command text.

    Keeps the command line with the <<DELIM marker but removes content lines
    between the marker and the closing delimiter.
    Returns None if a heredoc delimiter is unmatched.
    """
    lines = cmd.split('\n')
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        heredoc_match = re.search(r'<<-?\s*\\?[\'"]?(\w+)[\'"]?', line)
        if heredoc_match:
            delim = heredoc_match.group(1)
            result.append(line)
            i += 1
            found = False
            while i < len(lines):
                if lines[i].strip() == delim:
                    found = True
                    i += 1
                    break
                i += 1
            if not found:
                return None
            continue
        result.append(line)
        i += 1
    return '\n'.join(result)


# Shell control-flow keywords that make a multi-line command too complex to
# validate with our regex-based approach.
_CONTROL_FLOW_KW = frozenset({
    'if', 'then', 'else', 'elif', 'fi',
    'while', 'until', 'do', 'done',
    'for', 'case', 'esac', 'select', 'function',
})


def _normalize_multiline(cmd: str) -> str | None:
    """Convert a multi-line command into a ;-separated single line.

    Steps:
    1. Join line continuations (trailing backslash).
    2. Strip heredoc bodies.
    3. Drop empty lines and comment-only lines.
    4. Drop lone ``{`` / ``}`` grouping braces.
    5. Bail (return None) if any segment starts with a control-flow keyword.
    6. Join remaining lines with `` ; ``.
    """
    # Join line continuations: line ending with \ + next line
    joined: list[str] = []
    for raw_line in cmd.split('\n'):
        if joined and joined[-1].endswith('\\'):
            joined[-1] = joined[-1][:-1] + ' ' + raw_line
        else:
            joined.append(raw_line)

    text = '\n'.join(joined)

    # Strip heredoc bodies
    stripped = _strip_heredocs(text)
    if stripped is None:
        return None

    segments: list[str] = []
    for line in stripped.split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line in ('{', '}'):
            continue

        # Determine first meaningful word (skip leading '{')
        first_word = ''
        words = line.split()
        if words:
            first_word = words[0]
            if first_word == '{' and len(words) > 1:
                first_word = words[1]

        if first_word in _CONTROL_FLOW_KW:
            return None

        segments.append(line)

    return ' ; '.join(segments) if segments else ''


def is_safe(cmd: str) -> bool:
    """Check if the full command (including pipes, chains, redirects) is safe.

    All segments must be safe AND no dangerous redirects.
    Recursively resolves $() subshells before checking.
    """
    # Normalize multi-line commands first
    if '\n' in cmd:
        normalized = _normalize_multiline(cmd)
        if normalized is None:
            return False
        if not normalized:
            return True  # empty after stripping comments/blanks
        cmd = normalized

    # Resolve $() subshells — replace safe ones with placeholders
    resolved, all_subshells_safe = _resolve_subshells(cmd)
    if not all_subshells_safe:
        return False

    if _contains_unsupported_shell_syntax(resolved):
        return False

    # Check for dangerous redirects first (to files, not /dev/null)
    if _has_dangerous_redirects(resolved):
        return False

    # Split into segments and check each one
    segments = _split_command_chain(resolved)
    if not segments:
        return False

    return all(is_safe_segment(seg) for seg in segments)


def is_dangerous(cmd: str) -> bool:
    """Check if the command matches dangerous patterns (hard-block)."""
    dangerous_patterns = [
        r'\bgit\s+push\s+.*--delete\b',
        r'\bgit\s+push\s+\S+\s+:\S',
        r'\bgit\s+clean\s+-\w*f',
        r'clean_env\.sh',
        r'artifactory_login\.sh',
        r'bootstrap\.sh',
        r'\brm\s+-\w*r\w*\s+(/|~|\*)',
    ]
    for pattern in dangerous_patterns:
        if re.search(pattern, cmd):
            return True
    return False


def main():
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    tool_input = data.get('tool_input', {}) or data.get('toolInput', {})
    cmd = tool_input.get('command', '')
    if not cmd:
        sys.exit(0)

    if is_dangerous(cmd):
        print(json.dumps({
            'hookSpecificOutput': {
                'hookEventName': 'PreToolUse',
                'permissionDecision': 'deny',
                'permissionDecisionReason': f'Blocked: {cmd[:80]}',
            },
        }))
        sys.exit(0)

    if is_safe(cmd):
        print(json.dumps({
            'hookSpecificOutput': {
                'hookEventName': 'PreToolUse',
                'permissionDecision': 'allow',
                'permissionDecisionReason': 'Auto-approved safe command',
            },
        }))
        sys.exit(0)

    # Everything else — standard prompt (no output = passthrough)
    sys.exit(0)


if __name__ == '__main__':
    main()
