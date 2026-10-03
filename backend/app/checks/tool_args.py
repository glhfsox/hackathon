"""tool_args: block dangerous tool-call arguments (cost rank 5, checkpoint tool_call).

Every string inside `reply.tool_calls[*].arguments` (nested dicts and lists included) is checked by
the categories the policy enables in `categories` (default: all four). The first hit blocks. The
reason is `<tool>.<argument path>: <category>: <rule>` and never quotes the value: pii_secrets
does not run at tool_call, and the reason reaches the audit log, the tracer, the export and the
refusal text, so an excerpt would leak the SSNs, card numbers and tokens an argument carries.

Decisions to keep in mind when editing the rules:

- shell: destructive or remote-exec primitives (rm -rf, find -delete or -exec rm, xargs rm,
  shred, wipefs, truncate -s, mkfs, dd or any write to a disk device, mv of / or to /dev/null,
  Remove-Item -Recurse, del or rmdir /s, fork bomb, chmod -R 777, download piped to a shell,
  reverse shells, a pipe into a shell, an interpreter or a network tool) are blocked in any
  argument when their syntax cannot occur in prose. Those that read like prose (`format c:`, a
  bare `shred notes`, output redirects), chaining and substitution (`;` `&&` `||` `&`, a newline,
  `$(`, backticks, `<(`) and sudo are blocked only in arguments that are shell commands (the
  tool or argument name says shell, cmd, command ...), because free text is full of semicolons,
  `>` and words like "shred". A plain pipe between ordinary commands (`ps aux | grep x`) is
  allowed: every stage is still checked by the rules above. Before matching, quotes and
  backslashes are removed and ${IFS} reads as a space, so `r''m -rf` and `rm${IFS}-rf` are
  caught. The price is that a quoted `;` (`echo "a;b"`) is blocked too: we fail closed.
- shell, length: every scan for a flag or a target after a command word stops after
  `max_command_chars` characters, so the rest of a longer command could hide `-rf /` behind
  padding. In a shell command, a single command (the text between `;` `&` `|` and newlines,
  measured after the normalization above) longer than that is blocked as too long to inspect.
  Free text has no such limit; its scans keep a fixed bound of 1000 characters after each
  command word, so padding can hide a primitive there (free text is not run by a shell).
- shell, output redirection (shell commands only): an overwrite redirect that starts the command
  or follows a no-op (`> f`, `: > f`, `true > f`, `cat /dev/null > f`, also `cp /dev/null f`)
  exists only to empty its target, and a redirect, tee or dd `of=` into a database file (.db,
  .sqlite ...) or a system directory (/etc, /usr, /proc ...) destroys data. Every other redirect
  (`echo hi > /tmp/out.txt`, `report.py > report.csv`, `>> log.txt`) is ordinary output and
  passes: we cannot tell whether its target already holds data.
- sql: DELETE and UPDATE count only with statement syntax (`DELETE FROM t`, `UPDATE t SET`), so
  free text like "delete the draft where needed" passes. Comments are stripped before looking
  for WHERE, so `-- WHERE` cannot hide a missing one, and a WHERE that is only `1=1` counts as
  none. UNION SELECT is blocked only in a value that is not itself a query: a query argument is
  SQL by design, and which tables it may read is the database's permission model, not ours.
- path_traversal: `..` segments, URL-encoded traversal (overlong UTF-8 `%c0%af`, `%c1%9c` and
  `%c0%ae` included) and sensitive files are blocked in every argument, with or without
  `allowed_root`. Containment in `allowed_root` is checked only for arguments named like a path
  (path, file, dir, cwd ...); a relative path counts as inside.
- deserialization: loaders that run code while loading, and base64 blobs with a pickle header.
- labels: the reason is echoed in the refusal, the agent re-sends that as an assistant message,
  and signatures scans the whole conversation. A label that matched the signature feed (a path
  such as /etc/shadow) would block every later turn, so labels name things in words.
"""

from __future__ import annotations

import functools
import posixpath
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

from app.checks.base import CheckContext, make_result, safe_label
from app.models import CanonicalRequest, Checkpoint, CheckResult, Verdict

CATEGORIES = ("shell", "sql", "path_traversal", "deserialization")

# Words in a tool or argument name that mark the value as a shell command or a file path.
_SHELL_WORDS = frozenset(
    {"shell", "bash", "sh", "zsh", "cmd", "command", "commands", "cmdline", "terminal"}
)
_PATH_WORDS = frozenset(
    {"path", "paths", "file", "files", "filename", "filenames", "filepath", "filepaths"}
    | {"dir", "dirs", "dirname", "directory", "folder", "cwd"}
)

# Upper bound for the lazy scans below, so a huge argument cannot make a regex quadratic. Shell
# scans in a shell-command argument use the policy's max_command_chars instead.
_SCAN = 1000
_DEFAULT_MAX_COMMAND_CHARS = 2000

Hit = str  # the rule name; never text from the value, which may hold PII or secrets
Rules = list[tuple[str, re.Pattern[str]]]


@dataclass(frozen=True)
class _Arg:
    value: str
    shell: bool  # the tool or the argument name says this is a shell command
    pathish: bool  # the argument name says this is a file path
    root: str | None  # normalized allowed_root
    max_command: int  # max_command_chars: the shell scan bound and the longest allowed command


def _compile(rules: list[tuple[str, str]], flags: int = 0) -> Rules:
    return [(name, re.compile(pattern, flags)) for name, pattern in rules]


def _first(rules: Rules, text: str) -> Hit | None:
    for name, pattern in rules:
        if pattern.search(text):
            return name
    return None


# --- shell -----------------------------------------------------------------------------------

# One word of a simple command. Not \S: that runs across `|` and `;`, so every pipe in a long
# argument would rescan to its end (quadratic: 200 KB of `|a` took 36 s).
_WORD = r"[^\s;&|]"
_INTERP = (
    rf"(?:{_WORD}*/)?(?:(?:ba|da|z|k|c|tc|fi|a)?sh|python[\d.]*|perl|ruby|node|php|lua|pwsh)"
    r"(?![\w.-])"
)
_PIPE_TO = rf"\|\s*(?:(?:sudo|exec|(?:{_WORD}*/)?env)\s+|xargs(?:\s+-{_WORD}+)*\s+)*"
_DISK = r"/dev/(?:sd|hd|vd|xvd|nvme|mmcblk|r?disk|md\d|dm-|mapper/)"
# Commands that delete what they are handed, as run by find -exec or fed by a pipe.
_DELETER = (
    rf"(?:{_WORD}*/)?(?:rm|rmdir|unlink|shred|truncate|wipefs|remove-item|ri|del|erase)(?![\w.-])"
)
# What ends one simple command; the length rule measures the text between these.
_COMMAND_END = re.compile(r"[;&|\n]")


@functools.lru_cache(maxsize=4)
def _shell_rules(scan: int) -> tuple[Rules, Rules]:
    """(dangerous anywhere, dangerous in a shell command), every scan bounded by `scan`.

    `scan` is max_command_chars: a command the length rule lets through is scanned to its end,
    and a huge argument still cannot make a regex quadratic.
    """
    seg = rf"[^;&|\n]{{0,{scan}}}?"  # the rest of one simple command
    # Dangerous wherever they appear, even outside a shell-command argument.
    shell_any = _compile(
        [
            ("rm -rf", rf"(?<![\w.-])rm\s{seg}(?<!\S)(?:-[a-z]*r[a-z]*|--recursive)(?!\S)"),
            (
                "Remove-Item -Recurse",
                rf"(?<![\w.-])(?:remove-item|ri|rd|rmdir|del|erase)\s{seg}(?<!\S)-r[a-z]*"
                r"(?![\w-])",
            ),
            (
                "del/rmdir /s",  # cmd.exe switches may be glued together: /s/q, /f/s/q
                rf"(?<![\w.-])(?:rmdir|rd|del|erase)\s{seg}(?<!\S)(?:/[a-z](?::?[a-z-]*)?)*?/s"
                r"(?![\w.-])",
            ),
            ("find -delete", rf"(?<![\w.-])find\s{seg}(?<!\S)-delete(?![\w-])"),
            (
                "find -exec rm",
                rf"(?<![\w.-])find\s{seg}(?<!\S)-(?:exec|execdir|ok|okdir)\s+{_DELETER}",
            ),
            (
                "bulk delete from a pipe",
                rf"(?<![\w.-])xargs\s{seg}(?<!\S){_DELETER}|\|\s*remove-item(?![\w-])",
            ),
            # A bare `shred <word>` is prose ("shred the statements"); a flag or a path is a
            # command.
            ("shred", r"(?<![\w.-])shred\s+(?:[-~/.]|[^\s;&|]*[./]\w)"),
            ("disk wipe", r"(?<![\w.-])(?:wipefs|blkdiscard)(?![\w.-])"),
            (
                "truncate -s",
                rf"(?<![\w.-])truncate\s{seg}(?<!\S)(?:-[a-z]*[sr]|--size|--reference)(?![a-z])",
            ),
            ("mv to /dev/null", rf"(?<![\w.-])mv\s{seg}(?<!\S)/dev/null(?![^\s;&|])"),
            # Only / as the source: a lone "/" later on is common in prose ("MV Ever Given / ship").
            ("mv /", r"(?<![\w.-])mv\s+(?:-\S+\s+)*/\*?(?![^\s;&|])"),
            ("mkfs", r"(?<![\w-])mkfs(?:\.\w+)?(?![\w-])"),
            (
                "dd to a device",
                rf"(?<![\w-])dd\s{seg}\bof=/dev/(?!(?:null|zero|stdout|stderr)\b)",
            ),
            (
                "write to a disk device",  # redirect (also >| and >&), tee, or cp as the target
                rf"(?:>[|&]?\s*|(?<![\w.-])tee\s{seg}(?<!\S)){_DISK}"
                rf"|(?<![\w.-])cp\s{seg}(?<!\S){_DISK}[^\s;&|]*\s*(?:$|[;&|\n])",
            ),
            ("fork bomb", r"([\w:.]{1,40})\s*\(\s*\)\s*\{[^}]{0,40}?\1\s*\|\s*\1\s*&"),
            (
                "chmod -R 777",
                rf"(?<![\w-])chmod\s(?={seg}(?<!\S)-[a-z]*r){seg}(?<!\S)(?:0?777|a\+rwx)(?!\S)",
            ),
            (
                "chmod/chown on /",
                rf"(?<![\w-])ch(?:mod|own|grp)\s{seg}(?<!\S)/\*?(?![^\s;&|])",
            ),
            (
                "download piped to shell",
                rf"(?<![\w-])(?:curl|wget)\s[^\n;&]{{0,{scan}}}{_PIPE_TO}{_INTERP}",
            ),
            ("shell runs a download", r"[<$]\(\s*(?:curl|wget)\s"),
            ("reverse shell /dev/tcp", r"/dev/(?:tcp|udp)/"),
            (
                "nc -e",
                rf"(?<![\w-])(?:nc|ncat|netcat)\s{seg}(?<!\S)"
                r"(?:-[a-z]*[ec][a-z]*|--(?:sh-)?exec)(?!\S)",
            ),
            ("socat exec", rf"(?<![\w-])socat\s{seg}\bexec:"),
            ("pipe to shell or interpreter", _PIPE_TO + _INTERP),
            (
                "pipe to network tool",
                r"\|\s*(?:sudo\s+)?(?:nc|ncat|netcat|telnet|socat|curl|wget)(?![\w.-])",
            ),
        ]
    )
    # Dangerous in a shell command, harmless punctuation or prose in free text.
    shell_command = _compile(
        [
            ("sudo", r"(?<![\w./-])(?:sudo|doas|su)(?![\w.-])"),
            ("command substitution $(", r"\$\("),
            ("command substitution `", r"`"),
            ("process substitution", r"[<>]\("),
            ("command chaining &&", r"&&"),
            ("command chaining ||", r"\|\|"),
            ("command chaining ;", r";"),
            ("background &", r"(?<![&<>|])&(?![&>])"),
            ("command chaining newline", r"\n"),
            # Destructive commands whose words also occur in prose.
            ("format a drive", r"(?<![\w.-])format(?:\.com|\.exe)?\s+[a-z]:(?![^\s/])"),
            ("shred", r"(?<![\w.-])shred(?![\w.-])"),
            ("bulk delete from a pipe", rf"\|\s*{_DELETER}"),
            # Output redirection: the decision is in the module docstring.
            (
                "truncate by redirect",
                r"(?:^|\|)\s*(?:(?::|true|false|exec|cat\s+/dev/null|echo\s+-n|printf)\s*)?"
                r"\d*&?>\|?(?![>&])|(?<![\w.-])cp\s+(?:-\S+\s+)*/dev/null\s",
            ),
            (
                "redirect into a database or system file",
                rf"(?:>[>|&]?\s*|(?<![\w.-])tee\s{seg}(?<!\S)|(?<![\w-])of=)"
                r"(?:[^\s;&|<>]*\.(?:db|sqlite3?|db3|mdb|accdb)(?![\w.])"
                r"|/(?:etc|boot|bin|sbin|usr|lib\w*|var/lib|proc|sys)/)",
            ),
        ]
    )
    return shell_any, shell_command


_IFS = re.compile(r"\$(?:\{ifs[^}]*\}|ifs\b)")
_BRACE_LIST = re.compile(r"\{([^{}\s]*,[^{}\s]*)\}")
_HSPACE = re.compile(r"[ \t\r\f\v]+")


def _shell_text(value: str) -> str:
    """Undo the cheap obfuscations that bash itself undoes before running a command."""
    s = value.lower().replace("\\\n", "")
    s = _IFS.sub(" ", s)
    s = re.sub(r"\$[0-9@*]", "", s)  # empty positional parameters: rm$9 -rf
    s = re.sub(r"\$(?=['\"])", "", s)  # $'...' quoting
    s = re.sub(r"[\\'\"]", "", s)
    s = _BRACE_LIST.sub(lambda m: m.group(1).replace(",", " "), s)  # {rm,-rf,/}
    return _HSPACE.sub(" ", s).strip()


def _shell(arg: _Arg) -> Hit | None:
    text = _shell_text(arg.value)
    if arg.shell and max(map(len, _COMMAND_END.split(text))) > arg.max_command:
        # The scans stop after max_command characters, so the rest could hide `-rf /`: fail
        # closed. Checked first, so a long command never reaches the regexes at all.
        return "command too long to inspect"
    # A shell command is now at most max_command long, so its scans reach the end. Free text has
    # no length rule, so a longer scan would close nothing; it keeps the fixed bound and cost.
    shell_any, shell_command = _shell_rules(arg.max_command if arg.shell else _SCAN)
    hit = _first(shell_any, text)
    if hit is None and arg.shell:
        hit = _first(shell_command, text)
    return hit


# --- sql -------------------------------------------------------------------------------------

_IDENT = r"[\w.`\"\[\]]+"
_STATEMENT = (
    rf"(?:select\s+(?:[^;]{{0,{_SCAN}}}?\sfrom\s|@@|\d|\*|'|\w+\s*\()"
    rf"|insert\s+into\s|update\s+{_IDENT}\s+set\s|delete\s+from\s|truncate\s"
    r"|(?:drop|create(?:\s+or\s+replace)?)\s+"
    r"(?:table|database|schema|view|index|user|role|function|procedure|trigger)\b"
    r"|alter\s+(?:table|database|schema|user|role)\b"
    r"|exec(?:ute)?\s+(?:xp|sp)_|shutdown\b|waitfor\s+delay\b|declare\s+@)"
)

_SQL_DESTRUCTIVE = _compile(
    [
        ("DROP TABLE", r"\bdrop\s+table\b"),
        ("DROP DATABASE", r"\bdrop\s+(?:database|schema)\b"),
        (
            "TRUNCATE",
            rf"\btruncate\s+table\b|(?:^|[;('\"])\s*truncate\s+{_IDENT}"
            r"\s*(?:$|[;,'\")]|cascade\b|restart\b|continue\b)",
        ),
        ("ALTER ... DROP", r"\balter\s+table\s+\S+\s+drop\b"),
    ]
)
_SQL_INJECTION = _compile(
    [
        ("stacked statement", rf";\s*{_STATEMENT}"),
        ("OR tautology", r"\bor\s+(?:(\d+)\s*=\s*\1\b|'([^']*)'\s*=\s*'\2)"),
    ]
)
_UNSCOPED_WRITES = _compile(
    [
        ("DELETE without WHERE", r"\bdelete\s+from\s"),
        ("UPDATE without WHERE", rf"\bupdate\s+{_IDENT}\s+set\s"),
    ]
)
_WHERE = re.compile(r"\bwhere\b(.*)")
_TAUTOLOGY = re.compile(r"\s*(?:1\s*=\s*1|true|(\d+)\s*=\s*\1|'([^']*)'\s*=\s*'\2')['\"\s)]*")
_COMMENT_AFTER_QUOTE = re.compile(r"'\s*\)*\s*(?:--(?![a-z0-9])|#|/\*)")
_UNION_SELECT = re.compile(r"\bunion\s+(?:all\s+|distinct\s+)?select\b")
_IS_QUERY = re.compile(r"\(*\s*(?:select|with)\b")
_MYSQL_EXEC_COMMENT = re.compile(r"/\*!\d*(.*?)\*/", re.S)  # MySQL runs the body of /*! ... */
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"(?:--|#)[^\n]*")
_WS = re.compile(r"\s+")


def _has_real_where(rest: str) -> bool:
    """`rest` is the statement after DELETE FROM / UPDATE ... SET, comments already removed."""
    m = _WHERE.search(rest)
    return m is not None and not _TAUTOLOGY.fullmatch(m.group(1))


def _sql(arg: _Arg) -> Hit | None:
    low = arg.value.lower()
    code = _MYSQL_EXEC_COMMENT.sub(r" \1 ", low)
    code = _LINE_COMMENT.sub(" ", _BLOCK_COMMENT.sub(" ", code))
    code = _WS.sub(" ", code).strip()
    # Check with and without comments: comments can split keywords or hide a WHERE.
    texts = list(dict.fromkeys((code, _WS.sub(" ", low).strip())))

    for text in texts:
        if hit := _first(_SQL_DESTRUCTIVE, text):
            return hit
    for name, start in _UNSCOPED_WRITES:
        for m in start.finditer(code):
            if not _has_real_where(code[m.end() :].split(";", 1)[0]):
                return name
    for text in texts:
        if hit := _first(_SQL_INJECTION, text):
            return hit
    if _COMMENT_AFTER_QUOTE.search(low):
        return "comment after quote"
    if not _IS_QUERY.match(code) and any(_UNION_SELECT.search(text) for text in texts):
        return "UNION SELECT in a non-query value"
    return None


# --- path traversal --------------------------------------------------------------------------

_DOTDOT = re.compile(r"(?:^|(?<=[\s/'\"=:;,(|&<>]))\.\.(?=$|[\s/'\";,)|&<>])")
# Labels in words, not paths: see "labels" in the module docstring.
_SENSITIVE = _compile(
    [
        ("system account list", r"(?<![\w.-])etc/passwd\b"),
        ("system password hashes", r"(?<![\w.-])etc/shadow\b"),
        ("SSH key directory", r"(?<![\w.-])\.ssh(?![\w-])"),
        ("cloud credentials file", r"(?<![\w-])\.aws/credentials\b"),
        (
            "environment secrets file",
            r"(?<![\w.-])\.env(?!\.(?:example|sample|template)\b)(?![\w-])",
        ),
    ]
)


# Overlong UTF-8 for '.', '/' and '\': invalid, so unquote() drops them, but lenient decoders
# (old IIS, some Java and Node stacks) still read them as the ASCII character.
_OVERLONG = {"%c0%ae": ".", "%c0%af": "/", "%c1%9c": "\\"}
_OVERLONG_RE = re.compile("|".join(_OVERLONG), re.I)


def _url_decoded(value: str) -> str:
    s = value
    for _ in range(3):  # double and triple encoding: %252e%252e%252f
        decoded = unquote(_OVERLONG_RE.sub(lambda m: _OVERLONG[m.group().lower()], s))
        if decoded == s:
            break
        s = decoded
    return s


def _outside_root(path: str, root: str) -> Hit | None:
    if path.lower().startswith("file://"):
        path = path[len("file://") :]
    if path.startswith("~"):
        return "home path outside allowed root"
    if not path.startswith("/"):
        return None  # relative: resolved under the root, and '..' was already rejected
    norm = posixpath.normpath(path)
    if root == "/" or norm == root or norm.startswith(root + "/"):
        return None
    return "absolute path outside allowed root"


def _path(arg: _Arg) -> Hit | None:
    raw = arg.value.replace("\\", "/")
    decoded = _url_decoded(raw).replace("\\", "/")
    variants = [("'..' segment", raw), ("url-encoded traversal", decoded)]
    if arg.shell:
        variants.append(("'..' segment", _shell_text(arg.value)))
    for dotdot_rule, text in variants:
        if _DOTDOT.search(text):
            return dotdot_rule
        if hit := _first(_SENSITIVE, text.lower()):
            return hit
    if arg.pathish and arg.root is not None:
        return _outside_root(decoded.strip(), arg.root)
    return None


# --- deserialization -------------------------------------------------------------------------

_DESER = _compile(
    [
        ("pickle.load", r"\b(?:c|_|cloud)?pickle\s*\.\s*(?:loads?|unpickler)\b"),
        ("cPickle", r"\bcpickle\b"),
        ("dill.load", r"\bdill\s*\.\s*loads?\b"),
        (
            "pickle import",
            r"\bimport\s+(?:_?pickle|dill|cloudpickle)\b"
            r"|\bfrom\s+(?:_?pickle|dill|cloudpickle)\s+import\b",
        ),
        ("joblib.load", r"\bjoblib\s*\.\s*load\b"),
        ("read_pickle", r"\bread_pickle\s*\("),
        ("__reduce__", r"__reduce(?:_ex)?__"),
        ("yaml.unsafe_load", r"\byaml\s*\.\s*(?:unsafe|full)_load(?:_all)?\b"),
        ("!!python YAML tag", r"!!python/"),
        ("marshal.loads", r"\bmarshal\s*\.\s*loads?\b"),
        ("allow_pickle=True", r"\ballow_pickle\s*=\s*true\b"),
    ]
)
# (rule, call, argument pattern, True = the call is safe only WITH it / False = unsafe with it)
_GUARDED_CALLS = [
    (
        "yaml.load without SafeLoader",
        re.compile(r"\byaml\s*\.\s*load(?:_all)?\s*\("),
        re.compile(r"(?:c?safe|base)loader"),
        True,
    ),
    (
        "torch.load without weights_only=True",
        re.compile(r"\btorch\s*\.\s*load\s*\("),
        re.compile(r"\bweights_only\s*=\s*true\b"),
        True,
    ),
    (
        "eval/exec of decoded data",
        re.compile(r"\b(?:eval|exec)\s*\("),
        re.compile(
            r"b64decode|b32decode|b85decode|a85decode|decodebytes|unhexlify|fromhex"
            r"|decompress|\.decode\b|\bloads\b"
        ),
        False,
    ),
]
# Base64 of pickle starts: protocol 2-5 headers (\x80\x02 .. \x80\x05), protocol 0 dict/list,
# and protocol 0 imports of os, posix, nt, builtins, __builtin__ and subprocess (`cos\n` ...).
_PICKLE_B64 = re.compile(
    r"(?<![A-Za-z0-9+/])(?:gASV|gAWV|gAN|gAJ|KGRw|KGxw|Y29zC|Y3Bvc2l4C|Y250C|Y2J1aWx0aW5zC"
    r"|Y19fYnVpbHRpbl9fC|Y3N1YnByb2Nlc3MK)[A-Za-z0-9+/]{12,}"
)


def _call_args(text: str, start: int) -> str:
    """The argument text of a call whose '(' ends just before `start`, up to the matching ')'."""
    depth = 1
    end = min(len(text), start + _SCAN)
    for i in range(start, end):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start:i]
    return text[start:end]


def _deserialization(arg: _Arg) -> Hit | None:
    low = arg.value.lower()
    if hit := _first(_DESER, low):
        return hit
    for name, call, needle, safe_with in _GUARDED_CALLS:
        for m in call.finditer(low):
            if (needle.search(_call_args(low, m.end())) is not None) != safe_with:
                return name
    if _PICKLE_B64.search(arg.value):
        return "base64 pickle payload"
    return None


_INSPECTORS: dict[str, Callable[[_Arg], Hit | None]] = {
    "shell": _shell,
    "sql": _sql,
    "path_traversal": _path,
    "deserialization": _deserialization,
}


# --- the check -------------------------------------------------------------------------------


def _words(name: str) -> set[str]:
    snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()
    return set(re.split(r"[^a-z0-9]+", snake))


def _strings(arguments: dict[str, Any]) -> Iterator[tuple[str, str, str]]:
    """Yield (argument path, nearest key, value) for every string, in argument order.

    Iterative, so a deeply nested argument from the model cannot hit the recursion limit.
    """
    stack: list[tuple[str, str, Any]] = [(safe_label(k), str(k), v) for k, v in arguments.items()][
        ::-1
    ]
    while stack:
        path, key, node = stack.pop()
        if isinstance(node, str):
            yield path, key, node
        elif isinstance(node, dict):
            stack.extend([(f"{path}.{safe_label(k)}", str(k), v) for k, v in node.items()][::-1])
        elif isinstance(node, list):
            stack.extend([(f"{path}[{i}]", key, v) for i, v in enumerate(node)][::-1])


class ToolArgsCheck:
    id = "tool_args"
    cost_rank = 5
    checkpoints = frozenset({Checkpoint.TOOL_CALL})

    async def run(
        self, request: CanonicalRequest, settings: dict[str, Any], ctx: CheckContext
    ) -> CheckResult:
        started = time.perf_counter()

        def result(verdict: Verdict, reason: str) -> CheckResult:
            return make_result(self.id, request, verdict, reason, started)

        enabled = settings.get("categories", list(CATEGORIES))
        if not isinstance(enabled, list) or not all(isinstance(c, str) for c in enabled):
            return result("error", f"setting 'categories' must be a list of names, got {enabled!r}")
        unknown = sorted(set(enabled) - set(CATEGORIES))
        if unknown:
            return result("error", f"unknown categories {unknown}; known: {list(CATEGORIES)}")
        root = settings.get("allowed_root")
        if root is not None and (not isinstance(root, str) or not root.strip()):
            return result("error", f"setting 'allowed_root' must be a path, got {root!r}")
        root = posixpath.normpath(root.strip()) if root else None
        max_command = settings.get("max_command_chars", _DEFAULT_MAX_COMMAND_CHARS)
        if isinstance(max_command, bool) or not isinstance(max_command, int) or max_command < 1:
            return result(
                "error",
                f"setting 'max_command_chars' must be a positive integer, got {max_command!r}",
            )
        try:
            _shell_rules(max_command)
        except (re.error, OverflowError) as exc:  # beyond what a regex repeat can count
            return result("error", f"setting 'max_command_chars' is too large: {exc}")
        # Fixed order, so the reported rule does not depend on how the policy lists categories.
        inspectors = [(c, _INSPECTORS[c]) for c in CATEGORIES if c in enabled]

        calls = request.reply.tool_calls if request.reply else []
        if not calls:
            return result("allow", "no tool calls")
        for call in calls:
            shell_tool = bool(_words(call.name) & _SHELL_WORDS)
            for path, key, value in _strings(call.arguments):
                key_words = _words(key)
                arg = _Arg(
                    value=value,
                    shell=shell_tool or bool(key_words & _SHELL_WORDS),
                    pathish=bool(key_words & _PATH_WORDS),
                    root=root,
                    max_command=max_command,
                )
                for category, inspect in inspectors:
                    rule = inspect(arg)
                    if rule is not None:
                        return result(
                            "block", f"{safe_label(call.name)}.{path}: {category}: {rule}"
                        )
        return result("allow", f"no dangerous arguments in {len(calls)} tool call(s)")


CHECK = ToolArgsCheck()
