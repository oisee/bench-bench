#!/usr/bin/env python3
"""Find, and mask, secrets in a run directory before it is published or submitted.

    bin/secret-scan.py RUN_DIR [--mask]

Secrets come from the environment, never the command line:
- BENCH_SECRET: the endpoint key bin/run.sh hands over, labelled CUSTOM_API_KEY;
- every env var named *_API_KEY, *_TOKEN, *_SECRET, *_MASTER_KEY, or ANTHROPIC_AUTH_TOKEN / CUSTOM_API_KEY;
- the same names exported in ~/.bashrc and ~/.profile (where bin/sandbox.sh reads provider keys from);
- token/key/secret fields of the OAuth logins ~/.claude/.credentials.json and ~/.codex/auth.json.
Values shorter than 8 characters, "none" and the sandbox's dummy key are skipped.

Each value is searched as is, URL-encoded, and base64-encoded at each of the three byte alignments. The search covers
every regular file under RUN_DIR and every object in RUN_DIR/snap.git (decompressed).
- --mask: rewrite the artifacts in place, replacing each match with its first character and the variable name,
  e.g. `E***** [ZAI_API_KEY]`. Artifacts are everything except work/ (the agent's tree, which bin/first-green.py
  compares with the final snapshot) and snap.git. Matches there are only reported.
- Output: one line per file that held a secret ("masked" or "found"), never a secret.
- Exit 1 if anything was found (masked or not), so the leak stays visible; 0 when clean; 2 on error.
- Exit 2 also when a file or directory cannot be read: an unread file is not a clean one.
Limit: this catches keys that leaked by accident (printed, echoed, encoded the usual ways). A key deliberately
obfuscated (compressed outside git, XORed, split) is not found; the defense against that is that the key never enters
the sandbox (bin/sandbox.sh, bin/record-proxy.py).
"""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse

MIN_LEN = 8
DUMMY_KEY = 'bench-sandbox-dummy-key'
NAME_RE = re.compile(r'^([A-Z0-9_]*_(API_KEY|TOKEN|SECRET|MASTER_KEY)|ANTHROPIC_AUTH_TOKEN|CUSTOM_API_KEY)$')
FIELD_RE = re.compile(r'token|secret|key', re.I)
UNMASKED = ('work', 'snap.git')                   # top-level entries that are reported, never rewritten


def collect(env=os.environ, home=None):
    """{secret value: label} from BENCH_SECRET, env vars, rc-file exports and OAuth login files."""
    home = home or env.get('HOME') or os.path.expanduser('~')
    found = {}

    def add(value, label):
        if isinstance(value, str) and len(value) >= MIN_LEN and value not in ('none', DUMMY_KEY):
            found.setdefault(value, label)
    add(env.get('BENCH_SECRET', ''), 'CUSTOM_API_KEY')
    for k, v in env.items():
        if NAME_RE.match(k):
            add(v, k)
    for rc in ('.bashrc', '.profile'):
        try:
            text = open(os.path.join(home, rc)).read()
        except OSError:
            continue
        for m in re.finditer(r'^\s*export\s+([A-Z0-9_]+)=(.*)$', text, re.M):
            if NAME_RE.match(m.group(1)):
                add(m.group(2).strip().strip('"\''), m.group(1))
    for rel, label in (('.claude/.credentials.json', 'CLAUDE_OAUTH'), ('.codex/auth.json', 'CODEX_OAUTH')):
        try:
            data = json.load(open(os.path.join(home, rel)))
        except (OSError, ValueError):
            continue
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, dict):
                for k, v in x.items():
                    if isinstance(v, str) and FIELD_RE.search(k):
                        add(v, label)
                    else:
                        stack.append(v)
            elif isinstance(x, list):
                stack.extend(x)
    return found


def forms(value):
    """Regex sources (bytes) that reveal VALUE: the value with any character optionally percent-encoded in either case
    (this covers the plain value too), and base64 at each byte alignment, standard and URL-safe."""
    k = value.encode()
    out = [b''.join(b'(?:%s|%%%02x|%%%02X)' % (re.escape(bytes([c])), c, c) for c in k)]
    for off in range(3):                      # base64 of the value inside a longer blob, at any byte alignment
        enc = base64.b64encode(b'\0' * off + k)
        core = enc[4 if off else 0:]          # drop the chars that depend on the bytes before
        core = core.rstrip(b'=')
        tail = (len(k) + off) % 3             # the last group depends on what follows
        if tail:
            core = core[:-(tail + 1)]
        if len(core) >= MIN_LEN:
            out.append(re.escape(core))
            out.append(re.escape(core.replace(b'+', b'-').replace(b'/', b'_')))
    return list(dict.fromkeys(out))


PREFIX = 16


class Matcher:
    """One regex for all secrets (a named group per secret) for confirming and masking, and a fast literal prefilter:
    the regex is slow on large trees, so a file is only run through it if a 16-byte prefix of some form appears in it,
    either as is or after percent-decoding."""

    def __init__(self, secrets):
        labels, parts, lits = {}, [], set()
        for i, (value, label) in enumerate(sorted(secrets.items(), key=lambda kv: -len(kv[0]))):
            labels['s%d' % i] = label.encode()
            parts.append(b'(?P<s%d>%s)' % (i, b'|'.join(forms(value))))
            k = value.encode()
            lits.add(k[:PREFIX])
            for f in forms(value)[1:]:               # base64 forms are plain literals
                lits.add(re.sub(rb'\\(.)', rb'\1', f)[:PREFIX])
        self.rx = re.compile(b'|'.join(parts))
        self.lits = sorted(lits)
        self.labels = labels

    def maybe(self, data):
        if any(x in data for x in self.lits):
            return True
        if b'%' in data:
            plain = urllib.parse.unquote_to_bytes(data)
            return any(x in plain for x in self.lits)
        return False

    def search(self, data):
        return self.maybe(data) and self.rx.search(data) is not None

    def mask(self, data):
        return self.rx.sub(lambda m: m.group(0)[:1] + b'***** [' + self.labels[m.lastgroup] + b']', data)


def check_file(path, mt, write):
    """'masked', 'found' or None. With WRITE, masks in place atomically and keeps the file mode. An unreadable file
    raises: the scan must not call a directory clean that it could not read."""
    with open(path, 'rb') as f:
        data = f.read()
    if not mt.search(data):
        return None
    if not write:
        return 'found'
    data = mt.mask(data)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix='.mask-')
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
    os.chmod(tmp, os.stat(path).st_mode & 0o7777)
    os.replace(tmp, path)
    return 'masked'


def object_hits(gitdir, mt):
    """Git objects (stored compressed) that hold a secret, in RUN/snap.git or in any repository under RUN, the
    agent's work/.git included. Read through a throwaway repository with our own config that borrows the objects
    (alternates), so no git command runs with the agent's config."""
    tmp = tempfile.mkdtemp(prefix='secret-scan-')
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': tmp, 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_DIR': tmp}
    try:
        subprocess.run(['git', 'init', '-q', '--bare', tmp], check=True, env=env, capture_output=True)
        with open(os.path.join(tmp, 'objects', 'info', 'alternates'), 'w') as f:
            f.write(os.path.abspath(os.path.join(gitdir, 'objects')) + '\n')
        p = subprocess.Popen(['git', 'cat-file', '--batch-all-objects', '--batch', '--unordered'],
                             stdout=subprocess.PIPE, env=env)
        hits = []
        while True:
            head = p.stdout.readline()
            if not head:
                break
            oid, _, size = head.split()
            data = p.stdout.read(int(size))
            p.stdout.read(1)                   # the newline after each object
            if mt.search(data):
                hits.append(oid.decode())
        if p.wait():
            raise RuntimeError('git cat-file failed on ' + gitdir)
        return hits
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _raise(e):
    raise e


def scan(run, secrets, mask=False):
    """Lines 'masked<TAB>path' / 'found<TAB>path' for every file or git object store that held a secret."""
    if not secrets:
        return []
    mt = Matcher(secrets)
    out = []
    for root, dirs, files in os.walk(run, onerror=_raise):
        rel_root = os.path.relpath(root, run)
        if os.path.basename(root) == 'objects' and any(
                d in ('pack', 'info') or re.fullmatch(r'[0-9a-f]{2}', d) for d in dirs):
            oids = object_hits(os.path.dirname(root), mt)   # a git object store, whatever surrounds it
            if oids:
                out.append('found\t%s (%d git objects)' % (rel_root, len(oids)))
        write = mask and rel_root.split(os.sep)[0] not in UNMASKED
        for n in sorted(files):
            p = os.path.join(root, n)
            if os.path.islink(p) or not os.path.isfile(p):
                continue
            r = check_file(p, mt, write)
            if r:
                out.append('%s\t%s' % (r, os.path.relpath(p, run)))
    return sorted(out)


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if a != '--mask']
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    try:
        hits = scan(args[0], collect(), mask='--mask' in sys.argv[1:])
    except Exception as e:                     # never let a crash read as a hit (1) or as clean (0)
        print('scan failed: %s' % type(e).__name__, file=sys.stderr)
        sys.exit(2)
    for h in hits:
        print(h)
    sys.exit(1 if hits else 0)
