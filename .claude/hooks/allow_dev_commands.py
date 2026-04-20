#!/usr/bin/env python3
"""PreToolUse hook: auto-allow commands listed in settings.json Bash allow patterns."""
import json
import re
import sys
from fnmatch import fnmatch
from pathlib import Path

SETTINGS_PATH = Path(__file__).parent.parent / "settings.json"

# find -exec/-execdir runs arbitrary commands; -delete destroys files
_FIND_DANGEROUS_RE = re.compile(r'\bfind\b.*\s-(?:exec(?:dir)?|delete)\b')

# Path traversal: catches /../ style segments
_PATH_TRAVERSAL_RE = re.compile(r'(?:^|[\s/])\.\.(?:/|\s|$)')

# git add with bulk-staging flags anywhere in the argument list
_GIT_ADD_BULK_RE = re.compile(r'^git\s+add\s+(?:.*\s)?(?:\.|--all|-A)(?:\s|$)')


def load_allowed_patterns():
    settings = json.loads(SETTINGS_PATH.read_text())
    allow_list = settings.get("permissions", {}).get("allow", [])
    return [
        entry[5:-1]
        for entry in allow_list
        if entry.startswith("Bash(") and entry.endswith(")") and len(entry) > 6
    ]


def _split_shell_operators(text):
    """Split text on shell operators outside of quotes.

    Raises ValueError for:
    - Command/process substitution ($(), ``, ${}, <()) outside single quotes
    - Unclosed quotes

    Inside single quotes, substitution characters are literal (bash semantics),
    so they are collected without raising.

    Split points: &&, ||, |, ;, >, >>, newline (each causes a new token).
    Shell comments (# at start of a token, outside quotes) are skipped to EOL.
    Redirection targets (>file) become separate tokens that won't match any
    allowed pattern, so they are implicitly blocked.
    """
    parts = []
    current = []
    i = 0
    in_single = False
    in_double = False

    while i < len(text):
        ch = text[i]

        if in_single:
            # Inside single quotes everything is literal — no substitution possible
            if ch == "'":
                in_single = False
            current.append(ch)
            i += 1
            continue

        two = text[i:i + 2]

        # Outside single quotes: command/process substitution must be blocked
        # (dangerous even inside double quotes)
        if ch == '`' or two in ('$(', '${', '<('):
            raise ValueError(f"command/process substitution: {text!r}")

        if in_double:
            if ch == '"':
                in_double = False
            elif ch == '\\' and i + 1 < len(text):
                current.append(ch)
                i += 1
                current.append(text[i])
                i += 1
                continue
            current.append(ch)
        elif ch == "'":
            in_single = True
            current.append(ch)
        elif ch == '"':
            in_double = True
            current.append(ch)
        elif ch == '#' and not ''.join(current).strip():
            # Shell comment at start of token: skip until end of line
            while i < len(text) and text[i] != '\n':
                i += 1
            continue
        else:
            # Outside all quotes — split on operators and newlines
            if two in ('&&', '||', '>>'):
                parts.append(''.join(current).strip())
                current = []
                i += 2
                continue
            elif ch in ('|', ';', '>', '\n'):
                parts.append(''.join(current).strip())
                current = []
            else:
                current.append(ch)

        i += 1

    if in_single or in_double:
        raise ValueError(f"unclosed quote: {text!r}")

    if current:
        parts.append(''.join(current).strip())

    return parts


def extract_subcommands(command):
    """Return all sub-commands from command.

    Processes the full command string at once so multi-line quoted arguments
    (e.g. python -c "...") are parsed correctly.

    Raises ValueError (from _split_shell_operators) if the command contains
    command substitution or unclosed quotes.
    """
    parts = _split_shell_operators(command)
    return [p.strip() for p in parts if p.strip()]


def is_dangerous(cmd):
    """Return True if cmd should never be auto-allowed regardless of patterns."""
    return bool(
        _FIND_DANGEROUS_RE.search(cmd)
        or _PATH_TRAVERSAL_RE.search(cmd)
        or _GIT_ADD_BULK_RE.match(cmd)
    )


def is_allowed(cmd, patterns):
    return any(fnmatch(cmd, pattern) for pattern in patterns)


try:
    data = json.load(sys.stdin)
    command = data.get("tool_input", {}).get("command", "")

    if not command.strip():
        sys.exit(0)

    patterns = load_allowed_patterns()
    # No patterns: nothing to auto-allow; exit with no output = proceed to normal prompt
    if not patterns:
        sys.exit(0)

    # Raises ValueError on command substitution or unclosed quotes
    sub_commands = extract_subcommands(command)
    if not sub_commands:
        sys.exit(0)

    if all(not is_dangerous(cmd) and is_allowed(cmd, patterns) for cmd in sub_commands):
        print('{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}')

except ValueError:
    # Unsafe construct (command substitution, unclosed quote): silently block
    pass
except Exception as e:
    sys.stderr.write(f"allow_dev_commands error: {e}\n")
