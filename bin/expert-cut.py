#!/usr/bin/env python3
"""Cut the routed experts of a Hugging Face safetensors MoE checkpoint to a keep list.

Standard library only (no torch, numpy or safetensors package). Streams shard by shard:
every source byte is read once, kept tensors are written byte-exact (same dtype, same
bytes), experts are renumbered 0..K-1 in ascending original-id order, router rows are
sliced in the same order. Writes new shards (same file names), model.safetensors.index.json,
config.json with num_experts=K, copies tokenizer/chat-template/other small files as-is
and writes cnm-cut-manifest.json (source sha256s, kept ids, new->original mapping).

Modes:
  hf-cut-experts.py SRC KEEP_JSON DST [--dry-run] [--mtp drop]
  hf-cut-experts.py --verify SRC DST [--keep KEEP_JSON] [--hashes]
  hf-cut-experts.py --compare-router SRC REF      (REF: a local dir or hf:ORG/REPO[@REV])

KEEP_JSON: {"0": [original ids], ..., "47": [...]} for every routed MoE layer of the
main model; MTP layers, if cut, as "mtp.0". Keys starting with "_" are ignored.
Supported layout: per-expert tensors  <prefix>layers.N.mlp.experts.E.<proj>.<leaf>
with the router <prefix>layers.N.mlp.gate.weight (rows = experts). Fused 3D expert
tensors, unknown leaves, unknown router tensors and expert-indexed quantization configs
are refused, not guessed.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import struct
import sys
import urllib.request
from array import array

TOOL_VERSION = 1
INDEX = 'model.safetensors.index.json'
CONFIG = 'config.json'
MANIFEST = 'cnm-cut-manifest.json'
PARTIAL = '.partial'    # shards are written under this suffix and renamed when all are complete
CHUNK = 64 << 20

DTYPE_SIZE = {'F64': 8, 'F32': 4, 'F16': 2, 'BF16': 2, 'I64': 8, 'I32': 4, 'I16': 2, 'I8': 1,
              'U64': 8, 'U32': 4, 'U16': 2, 'U8': 1, 'BOOL': 1, 'F8_E4M3': 1, 'F8_E5M2': 1,
              'F8_E8M0': 1}
EXPERT_COUNT_KEYS = ('num_experts', 'num_local_experts', 'n_routed_experts', 'moe_num_experts')
TOPK_KEYS = ('num_experts_per_tok', 'moe_topk', 'top_k')
EXPERT_PROJS = {'gate_proj', 'up_proj', 'down_proj', 'w1', 'w2', 'w3'}
# Leaves that belong to exactly one expert's projection (whole tensor copied with the expert).
EXPERT_LEAVES = {'weight', 'bias', 'weight_scale', 'weight_scale_2', 'weight_scale_inv',
                 'input_scale', 'weight_packed', 'weight_global_scale', 'input_global_scale',
                 'weight_zero_point', 'weight_shape', 'qweight', 'qzeros', 'scales', 'g_idx'}
ROUTER_LEAVES = {'weight', 'bias', 'e_score_correction_bias'}
WEIGHT_FILE_EXT = ('.bin', '.pt', '.pth', '.gguf', '.ckpt', '.h5', '.msgpack')

LAYER_RE = re.compile(r'^(?P<pfx>(?:[A-Za-z0-9_]+\.)*?)layers\.(?P<L>\d+)\.mlp\.(?P<rest>.+)$')
EXPERT_RE = re.compile(r'^experts\.(?P<E>\d+)\.(?P<sub>.+)$')
MAIN_PREFIXES = ('model.language_model.', 'model.', 'language_model.model.', 'language_model.', '')


class CutError(Exception):
    pass


# ---------------------------------------------------------------- safetensors I/O

class Source:
    """Random-access reader over a local dir or an HF repo (HTTP range requests)."""

    def __init__(self, spec):
        self.spec = spec
        if spec.startswith('hf:'):
            repo = spec[3:]
            repo, _, rev = repo.partition('@')
            self.base = 'https://huggingface.co/%s/resolve/%s/' % (repo, rev or 'main')
            self.local = None
        else:
            self.local = spec
            self.base = None

    def path(self, name):
        return os.path.join(self.local, name)

    def exists(self, name):
        return os.path.isfile(self.path(name)) if self.local else True

    def read_json(self, name):
        if self.local:
            with open(self.path(name), 'rb') as f:
                return json.loads(f.read())
        with urllib.request.urlopen(self.base + name, timeout=120) as r:
            return json.loads(r.read())

    def read_at(self, name, off, n):
        if n == 0:
            return b''
        if self.local:
            with open(self.path(name), 'rb') as f:
                f.seek(off)
                b = f.read(n)
        else:
            req = urllib.request.Request(self.base + name, headers={'Range': 'bytes=%d-%d' % (off, off + n - 1)})
            with urllib.request.urlopen(req, timeout=300) as r:
                b = r.read()
        if len(b) != n:
            raise CutError('%s: short read at %d (%d of %d bytes)' % (name, off, len(b), n))
        return b


def parse_header(raw_len, raw, fname, file_size=None):
    try:
        h = json.loads(raw)
    except ValueError as e:
        raise CutError('%s: bad safetensors header: %s' % (fname, e))
    meta = h.pop('__metadata__', None)
    data_start = 8 + raw_len
    tensors = {}
    for name, v in h.items():
        dt, shape, (b, e) = v['dtype'], v['shape'], v['data_offsets']
        n = 1
        for d in shape:
            n *= d
        if dt in DTYPE_SIZE and n * DTYPE_SIZE[dt] != e - b:
            raise CutError('%s: %s: %s%s needs %d bytes, offsets give %d' % (fname, name, dt, shape, n * DTYPE_SIZE[dt], e - b))
        if b > e or (file_size is not None and data_start + e > file_size):
            raise CutError('%s: %s: offsets %s outside the file' % (fname, name, [b, e]))
        tensors[name] = {'dtype': dt, 'shape': list(shape), 'start': data_start + b, 'end': data_start + e}
    return tensors, meta, data_start


def read_header(src, fname):
    if src.local:
        size = os.path.getsize(src.path(fname))
        with open(src.path(fname), 'rb') as f:
            (n,) = struct.unpack('<Q', f.read(8))
            if n > 512 << 20 or 8 + n > size:
                raise CutError('%s: not a safetensors file (header length %d)' % (fname, n))
            raw = f.read(n)
        return parse_header(n, raw, fname, size)
    (n,) = struct.unpack('<Q', src.read_at(fname, 0, 8))
    return parse_header(n, src.read_at(fname, 8, n), fname)


def header_bytes(entries, meta):
    """entries: list of (name, dtype, shape, nbytes) in data order. Returns (bytes, offsets)."""
    h = {}
    if meta is not None:
        h['__metadata__'] = meta
    off = 0
    for name, dt, shape, nb in entries:
        h[name] = {'dtype': dt, 'shape': shape, 'data_offsets': [off, off + nb]}
        off += nb
    raw = json.dumps(h, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    raw += b' ' * ((8 - len(raw) % 8) % 8)
    return struct.pack('<Q', len(raw)) + raw, off


def write_safetensors(path, tensors, meta=None):
    """Small helper (tests): tensors = list of (name, dtype, shape, bytes)."""
    hb, _ = header_bytes([(n, d, s, len(b)) for n, d, s, b in tensors], meta)
    with open(path, 'wb') as f:
        f.write(hb)
        for _, _, _, b in tensors:
            f.write(b)


# ---------------------------------------------------------------- layout

def scope_of(pfx):
    """Return 'main' / 'mtp' / None for the part before 'layers.N.mlp.'."""
    if pfx.startswith('mtp.') or '.mtp.' in pfx or pfx == 'mtp.':
        return 'mtp'
    if pfx in MAIN_PREFIXES:
        return 'main'
    return None


def layer_key(scope, L):
    return str(L) if scope == 'main' else 'mtp.%d' % L


def is_mtp_name(name):
    return name.startswith('mtp.') or '.mtp.' in name


def classify(name):
    """('expert', key, E, sub) | ('router', key, leaf) | ('copy',) ; raises CutError on unknown layouts."""
    m = LAYER_RE.match(name)
    if not m:
        if re.search(r'(^|\.)experts?\.', name) or '.router.' in name:
            raise CutError('unknown layout: expert-like tensor outside <prefix>layers.N.mlp.: %s' % name)
        return ('copy',)
    pfx, L, rest = m.group('pfx'), int(m.group('L')), m.group('rest')
    scope = scope_of(pfx)
    me = EXPERT_RE.match(rest)
    if me:
        if scope is None:
            raise CutError('unknown layout: expert tensor under unknown prefix %r: %s' % (pfx, name))
        sub = me.group('sub')
        parts = sub.split('.')
        if len(parts) != 2 or parts[0] not in EXPERT_PROJS or parts[1] not in EXPERT_LEAVES:
            raise CutError('unknown layout: per-expert tensor with unknown projection/leaf %r: %s '
                           '(add it to EXPERT_PROJS/EXPERT_LEAVES only after checking it belongs to one expert)'
                           % (sub, name))
        return ('expert', layer_key(scope, L), int(me.group('E')), sub)
    if rest.startswith('experts.'):
        raise CutError('unknown layout: fused/3D expert tensor (no per-expert index): %s; '
                       'this script cuts per-expert layouts only' % name)
    if rest.startswith('gate.'):
        leaf = rest[len('gate.'):]
        if leaf not in ROUTER_LEAVES:
            raise CutError('unknown layout: router tensor %r (quantised or unknown router?): %s' % (leaf, name))
        if scope is None:
            raise CutError('unknown layout: router under unknown prefix %r: %s' % (pfx, name))
        return ('router', layer_key(scope, L), leaf)
    if rest.startswith(('shared_expert.', 'shared_expert_gate.', 'shared_experts.')):
        return ('copy',)
    if re.match(r'^(gate_proj|up_proj|down_proj|gate_up_proj|fc1|fc2)\.', rest):
        return ('copy',)          # dense MLP layer
    if 'expert' in rest or 'router' in rest or rest.startswith('gate'):
        raise CutError('unknown layout: unrecognised MoE tensor: %s' % name)
    return ('copy',)


def expert_name(src_name, new_id):
    m = LAYER_RE.match(src_name)
    me = EXPERT_RE.match(m.group('rest'))
    return '%slayers.%s.mlp.experts.%d.%s' % (m.group('pfx'), m.group('L'), new_id, me.group('sub'))


def text_cfg(cfg):
    return cfg['text_config'] if isinstance(cfg.get('text_config'), dict) else cfg


def scan_quant_configs(src, cfg):
    blobs = []
    if 'quantization_config' in cfg:
        blobs.append(('config.json:quantization_config', cfg['quantization_config']))
    tc = text_cfg(cfg)
    if tc is not cfg and 'quantization_config' in tc:
        blobs.append(('config.json:text_config.quantization_config', tc['quantization_config']))
    for extra in ('hf_quant_config.json', 'quantization_config.json', 'quant_config.json'):
        if src.local and not src.exists(extra):
            continue
        try:
            blobs.append((extra, src.read_json(extra)))
        except (OSError, ValueError):   # hf: source, file absent
            if src.local:
                raise
    for where, b in blobs:
        hit = re.search(r'experts\.\d+', json.dumps(b))
        if hit:
            raise CutError('unknown layout: %s names an individual expert (%r); renumbering it is not '
                           'implemented, refusing' % (where, hit.group(0)))


def load_model(src):
    """Read config + index + all shard headers; validate the layout. Returns a model dict."""
    cfg = src.read_json(CONFIG)
    tc = text_cfg(cfg)
    E = tc.get('num_experts')
    if not isinstance(E, int):
        raise CutError('config.json: no integer num_experts (text_config or top level)')
    topk = next((tc[k] for k in TOPK_KEYS if isinstance(tc.get(k), int)), None)
    if src.exists(INDEX):
        index = src.read_json(INDEX)
        wm = index['weight_map']
        shards = sorted(set(wm.values()))
    elif src.exists('model.safetensors'):
        index, wm, shards = None, None, ['model.safetensors']
    else:
        raise CutError('no %s or model.safetensors in %s' % (INDEX, src.spec))
    headers = {}
    where = {}
    for s in shards:
        t, meta, ds = read_header(src, s)
        headers[s] = (t, meta, ds)
        for n in t:
            if n in where:
                raise CutError('tensor %s in two shards (%s, %s)' % (n, where[n], s))
            where[n] = s
    if wm is not None:
        missing = set(wm) - set(where)
        extra = set(where) - set(wm)
        wrong = [n for n in wm if n in where and where[n] != wm[n]]
        if missing or extra or wrong:
            raise CutError('source index inconsistent with shard headers: %d listed-not-found, %d unlisted, '
                           '%d in another shard (e.g. %s)' % (len(missing), len(extra), len(wrong),
                                                              sorted(missing | extra | set(wrong))[:3]))
    layers = {}       # key -> {'experts': {E: {sub: name}}, 'router': {leaf: name}}
    kinds = {}
    for n in where:
        c = classify(n)
        kinds[n] = c
        if c[0] == 'expert':
            layers.setdefault(c[1], {'experts': {}, 'router': {}})['experts'].setdefault(c[2], {})[c[3]] = n
        elif c[0] == 'router':
            layers.setdefault(c[1], {'experts': {}, 'router': {}})['router'][c[2]] = n
    for key, lay in sorted(layers.items()):
        ex = lay['experts']
        if sorted(ex) != list(range(E)):
            raise CutError('layer %s: expert ids are not exactly 0..%d (found %d ids, e.g. missing %s)'
                           % (key, E - 1, len(ex), sorted(set(range(E)) - set(ex))[:5]))
        if 'weight' not in lay['router']:
            raise CutError('layer %s: routed experts without router %s' % (key, 'mlp.gate.weight'))
        subs0 = ex[0]
        for e, subs in ex.items():
            if set(subs) != set(subs0):
                raise CutError('layer %s expert %d: tensors %s differ from expert 0 %s (missing scale?)'
                               % (key, e, sorted(set(subs) ^ set(subs0)), sorted(subs0)))
            for sub, n in subs.items():
                a, b = headers[where[n]][0][n], headers[where[subs0[sub]]][0][subs0[sub]]
                if a['dtype'] != b['dtype'] or a['shape'] != b['shape']:
                    raise CutError('layer %s expert %d %s: %s%s differs from expert 0 %s%s'
                                   % (key, e, sub, a['dtype'], a['shape'], b['dtype'], b['shape']))
        for leaf, n in lay['router'].items():
            t = headers[where[n]][0][n]
            if not t['shape'] or t['shape'][0] != E:
                raise CutError('layer %s router %s: shape %s, first dim must be %d' % (key, leaf, t['shape'], E))
    return {'src': src, 'cfg': cfg, 'E': E, 'topk': topk, 'index': index, 'shards': shards,
            'headers': headers, 'where': where, 'kinds': kinds, 'layers': layers}


def tensor_bytes(model, name):
    t = model['headers'][model['where'][name]][0][name]
    return t['end'] - t['start']


# ---------------------------------------------------------------- keep / plan

def load_keep(path):
    with open(path) as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise CutError('%s: expected {"layer": [original expert ids]}' % path)
    keep = {}
    for k, v in raw.items():
        if k.startswith('_'):
            continue
        if not isinstance(v, list) or not all(isinstance(x, int) and not isinstance(x, bool) for x in v):
            raise CutError('%s: layer %s: expected a list of integer expert ids' % (path, k))
        if len(set(v)) != len(v):
            raise CutError('%s: layer %s: duplicate expert ids' % (path, k))
        keep[k] = v
    return keep


def order_keep(ids):
    """New expert j = j-th smallest kept original id (stable original order)."""
    return sorted(ids)


def build_inverse(new_to_orig):
    return {o: j for j, o in enumerate(new_to_orig)}


def make_plan(model, keep, mtp_policy):
    E, layers = model['E'], model['layers']
    moe_keys = sorted(layers, key=lambda k: (k.startswith('mtp.'), int(k.split('.')[-1])))
    main_keys = [k for k in moe_keys if not k.startswith('mtp.')]
    mtp_keys = [k for k in moe_keys if k.startswith('mtp.')]
    if not main_keys:
        raise CutError('no routed MoE layers found')
    drop_mtp = False
    if mtp_keys:
        have = [k for k in mtp_keys if k in keep]
        if have and len(have) == len(mtp_keys) and mtp_policy != 'drop':
            pass
        elif mtp_policy == 'drop':
            if have:
                raise CutError('--mtp drop given but KEEP_JSON has MTP keys %s' % have)
            drop_mtp = True
        else:
            raise CutError(
                'source has MTP MoE layer(s) %s with %d experts, and config has ONE num_experts shared by the '
                'trunk and MTP (vLLM sizes the MTP router/FusedMoE from the same text_config.num_experts). '
                'Either add keep lists for %s to KEEP_JSON, or pass --mtp drop to remove the MTP module '
                '(then do not enable MTP speculative decoding).' % (mtp_keys, E, mtp_keys))
    cut_keys = main_keys + ([] if drop_mtp else mtp_keys)
    unknown = sorted(set(keep) - set(cut_keys))
    missing = [k for k in cut_keys if k not in keep]
    if missing or unknown:
        raise CutError('KEEP_JSON layers do not match the model: missing %s, unknown %s (MoE layers: %s)'
                       % (missing[:8], unknown[:8], cut_keys[:4] + (['...'] if len(cut_keys) > 4 else [])))
    Ks = {len(keep[k]) for k in cut_keys}
    if len(Ks) != 1:
        raise CutError('all layers must keep the same number of experts (one num_experts in config): %s' % sorted(Ks))
    K = Ks.pop()
    for k in cut_keys:
        bad = [x for x in keep[k] if not 0 <= x < E]
        if bad:
            raise CutError('layer %s: ids out of range 0..%d: %s (are these original ids?)' % (k, E - 1, bad[:5]))
    if model['topk'] is None:
        raise CutError('config.json: no top-k key (%s)' % ', '.join(TOPK_KEYS))
    if not model['topk'] < K:
        raise CutError('top-k %d must be < K %d' % (model['topk'], K))
    if K > E:
        raise CutError('K %d > num_experts %d' % (K, E))
    n2o = {k: order_keep(keep[k]) for k in cut_keys}
    inv = {k: build_inverse(n2o[k]) for k in cut_keys}
    # per-shard actions in source data order
    shards = []
    for s in model['shards']:
        tens, meta, ds = model['headers'][s]
        acts = []
        for name in sorted(tens, key=lambda n: tens[n]['start']):
            t, c = tens[name], model['kinds'][name]
            nb = t['end'] - t['start']
            if drop_mtp and is_mtp_name(name):
                acts.append(('drop', name, None, None, None, nb))
            elif c[0] == 'expert':
                j = inv[c[1]].get(c[2])
                if j is None:
                    acts.append(('drop', name, None, None, None, nb))
                else:
                    acts.append(('copy', expert_name(name, j), t['dtype'], t['shape'], name, nb))
            elif c[0] == 'router':
                rows = n2o[c[1]]
                row = nb // E
                acts.append(('router', name, t['dtype'], [K] + t['shape'][1:], name, row * K, rows, row))
            else:
                acts.append(('copy', name, t['dtype'], t['shape'], name, nb))
        shards.append((s, meta, acts))
    return {'K': K, 'n2o': n2o, 'cut_keys': cut_keys, 'drop_mtp': drop_mtp, 'mtp_keys': mtp_keys,
            'shards': shards}


def new_config(model, K):
    cfg = json.loads(json.dumps(model['cfg']))
    tc = text_cfg(cfg)
    changed = []
    for k in EXPERT_COUNT_KEYS:
        if tc.get(k) == model['E']:
            tc[k] = K
            changed.append(('text_config.' if tc is not cfg else '') + k)
    return cfg, changed


def other_files(src_dir):
    copy, skipped = [], []
    for n in sorted(os.listdir(src_dir)):
        p = os.path.join(src_dir, n)
        if n.startswith('.') or n in (INDEX, CONFIG, MANIFEST):
            continue
        if os.path.isdir(p):
            skipped.append(n + '/')
            continue
        if n.endswith('.safetensors'):
            continue
        if n.lower().endswith(WEIGHT_FILE_EXT):
            raise CutError('%s: other weight file format in SRC; refusing to guess which weights vLLM loads' % n)
        copy.append(n)
    return copy, skipped


# ---------------------------------------------------------------- cut

def human(n):
    for u in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if n < 1024 or u == 'TiB':
            return '%.1f %s' % (n, u) if u != 'B' else '%d B' % n
        n /= 1024.0


def cut(src_dir, keep_path, dst_dir, dry_run=False, mtp_policy=None, log=print):
    src = Source(src_dir)
    if not src.local and not dry_run:
        raise CutError('hf: sources are for --dry-run only; download the model first')
    model = load_model(src)
    scan_quant_configs(src, model['cfg'])
    keep = load_keep(keep_path)
    plan = make_plan(model, keep, mtp_policy)
    K = plan['K']
    cfg, changed = new_config(model, K)
    copies, skipped = other_files(src_dir) if src.local else ([], [])
    in_bytes = sum(max([t['end'] for t in model['headers'][s][0].values()] + [model['headers'][s][2]])
                   for s in model['shards'])
    out_bytes = 0
    out_shards = []
    for s, meta, acts in plan['shards']:
        entries = [(a[1], a[2], a[3], a[5]) for a in acts if a[0] != 'drop']
        if entries:
            hb, size = header_bytes(entries, meta)
            out_bytes += len(hb) + size
            out_shards.append(s)
    n_drop = sum(1 for _, _, acts in plan['shards'] for a in acts if a[0] == 'drop')
    log('model: %s experts, top-k %s, %d MoE layers (%s), %d shards, %s' % (
        model['E'], model['topk'], len(model['layers']), ', '.join(plan['cut_keys'][:3]) + ', ...',
        len(model['shards']), human(in_bytes)))
    log('cut: K=%d per layer, %d tensors dropped, MTP: %s' % (
        K, n_drop, 'dropped' if plan['drop_mtp'] else ('cut' if plan['mtp_keys'] else 'none')))
    log('config: %s -> %d' % (', '.join(changed), K))
    log('output: %d shards, %s (%.1f%% of input); copy as-is: %s' % (
        len(out_shards), human(out_bytes), 100.0 * out_bytes / max(in_bytes, 1), ', '.join(copies) or '-'))
    if skipped:
        log('not copied (directories): %s' % ', '.join(skipped))
    if dry_run:
        log('dry run: nothing written')
        return {'K': K, 'out_bytes': out_bytes, 'in_bytes': in_bytes, 'out_shards': out_shards}
    if os.path.exists(dst_dir) and os.listdir(dst_dir):
        raise CutError('%s exists and is not empty; refusing to overwrite' % dst_dir)
    os.makedirs(dst_dir, exist_ok=True)
    free = shutil.disk_usage(dst_dir).free
    if free < out_bytes * 1.01 + (64 << 20):
        raise CutError('not enough space in %s: need %s, free %s' % (dst_dir, human(out_bytes), human(free)))
    src_sha, out_sha, weight_map = {}, {}, {}
    total = 0
    for i, (s, meta, acts) in enumerate(plan['shards']):
        h_in = hashlib.sha256()
        entries = [(a[1], a[2], a[3], a[5]) for a in acts if a[0] != 'drop']
        fout = None
        h_out = hashlib.sha256()
        if entries:
            hb, size = header_bytes(entries, meta)
            fout = open(os.path.join(dst_dir, s + PARTIAL), 'wb')
            fout.write(hb)
            h_out.update(hb)
            total += size
            for e in entries:
                weight_map[e[0]] = s
        with open(src.path(s), 'rb') as fin:
            pos = 0

            def take(n):
                nonlocal pos
                b = fin.read(n)
                if len(b) != n:
                    raise CutError('%s: short read' % s)
                h_in.update(b)
                pos += n
                return b

            tens = model['headers'][s][0]
            for a in acts:
                t = tens[a[1] if a[0] == 'drop' else a[4]]
                while pos < t['start']:                       # header / padding gaps
                    take(min(CHUNK, t['start'] - pos))
                if a[0] == 'router':
                    data = take(t['end'] - t['start'])
                    rowb = a[7]
                    out = b''.join(data[o * rowb:(o + 1) * rowb] for o in a[6])
                    fout.write(out)
                    h_out.update(out)
                else:
                    left = t['end'] - t['start']
                    while left:
                        b = take(min(CHUNK, left))
                        left -= len(b)
                        if a[0] == 'copy':
                            fout.write(b)
                            h_out.update(b)
            while True:
                b = fin.read(CHUNK)
                if not b:
                    break
                h_in.update(b)
        src_sha[s] = {'sha256': h_in.hexdigest(), 'size': os.path.getsize(src.path(s))}
        if fout:
            fout.close()
            out_sha[s] = {'sha256': h_out.hexdigest(), 'size': os.path.getsize(os.path.join(dst_dir, s + PARTIAL))}
        log('[%d/%d] %s -> %s' % (i + 1, len(plan['shards']), s, 'written' if fout else 'empty, not written'))
    index = {'metadata': dict((model['index'] or {}).get('metadata') or {}), 'weight_map': {}}
    index['metadata']['total_size'] = total
    index['weight_map'] = {n: weight_map[n] for n in sorted(weight_map)}
    for s in out_sha:                       # shards complete: give them their final names
        os.replace(os.path.join(dst_dir, s + PARTIAL), os.path.join(dst_dir, s))
    with open(os.path.join(dst_dir, INDEX), 'w') as f:
        json.dump(index, f, indent=2)
        f.write('\n')
    with open(os.path.join(dst_dir, CONFIG), 'w') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write('\n')
    for n in copies:
        shutil.copyfile(src.path(n), os.path.join(dst_dir, n))
    for n in (CONFIG, INDEX) + tuple(copies):
        if src.exists(n):
            src_sha[n] = {'sha256': sha256_file(src.path(n)), 'size': os.path.getsize(src.path(n))}
    with open(keep_path, 'rb') as f:
        keep_sha = hashlib.sha256(f.read()).hexdigest()
    manifest = {
        'tool': 'cnm scripts/hf-cut-experts.py', 'tool_version': TOOL_VERSION,
        'created_utc': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'src_dir': os.path.abspath(src_dir), 'keep_json': os.path.abspath(keep_path), 'keep_json_sha256': keep_sha,
        'num_experts_src': model['E'], 'num_experts_dst': K, 'top_k': model['topk'],
        'config_keys_changed': changed, 'mtp': 'dropped' if plan['drop_mtp'] else ('cut' if plan['mtp_keys'] else 'none'),
        'dropped_prefixes': ['mtp.'] if plan['drop_mtp'] else [],
        'copied_files': copies,
        'source_files': src_sha, 'output_files': out_sha,
        'mapping_note': 'layers[L].new_to_original[j] = original expert id stored as new expert j',
        'layers': {k: {'new_to_original': plan['n2o'][k]} for k in plan['cut_keys']},
    }
    with open(os.path.join(dst_dir, MANIFEST), 'w') as f:
        json.dump(manifest, f, indent=1)
        f.write('\n')
    log('done: %s (%s); now run --verify' % (dst_dir, human(out_bytes)))
    return {'K': K, 'out_bytes': out_bytes, 'in_bytes': in_bytes, 'out_shards': out_shards}


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        while True:
            b = f.read(CHUNK)
            if not b:
                return h.hexdigest()
            h.update(b)


# ---------------------------------------------------------------- verify

def verify(src_dir, dst_dir, keep_path=None, hashes=False, log=print, max_report=20):
    """Re-check a finished cut by content. Returns a list of failures (empty = OK)."""
    fails = []

    def fail(msg):
        fails.append(msg)
        if len(fails) <= max_report:
            log('FAIL ' + msg)

    src, dst = Source(src_dir), Source(dst_dir)
    model = load_model(src)
    man = dst.read_json(MANIFEST)
    E = model['E']
    lay = {k: v['new_to_original'] for k, v in man['layers'].items()}
    Ks = {len(v) for v in lay.values()}
    K = man['num_experts_dst']
    if Ks != {K}:
        fail('manifest: layer sizes %s != num_experts_dst %d' % (sorted(Ks), K))
    for k, v in lay.items():
        if v != sorted(set(v)):
            fail('manifest layer %s: new_to_original not strictly ascending (order must be original id order)' % k)
        if any(not 0 <= x < E for x in v):
            fail('manifest layer %s: ids out of range' % k)
    drop_mtp = 'mtp.' in man.get('dropped_prefixes', [])
    expect_keys = sorted(k for k in model['layers'] if not (drop_mtp and k.startswith('mtp.')))
    if sorted(lay) != expect_keys:
        fail('manifest layers %s != source MoE layers %s' % (sorted(lay)[:5], expect_keys[:5]))
    if keep_path:
        keep = load_keep(keep_path)
        if {k: sorted(v) for k, v in keep.items()} != {k: sorted(v) for k, v in lay.items()}:
            fail('manifest kept ids differ from %s' % keep_path)
    if model['topk'] is not None and not model['topk'] < K:
        fail('top-k %s not < K %d' % (model['topk'], K))
    # config
    dcfg = dst.read_json(CONFIG)
    exp_cfg, _ = new_config(model, K)
    if text_cfg(dcfg).get('num_experts') != K:
        fail('config.json num_experts %s != K %d' % (text_cfg(dcfg).get('num_experts'), K))
    elif dcfg != exp_cfg:
        diff = [k for k in set(text_cfg(dcfg)) | set(text_cfg(exp_cfg)) if text_cfg(dcfg).get(k) != text_cfg(exp_cfg).get(k)]
        fail('config.json differs from source config with num_experts=%d (keys: %s)' % (K, sorted(diff)[:8] or 'top level'))
    # index vs shard headers
    didx = dst.read_json(INDEX)
    wm = didx['weight_map']
    dwhere, dtens = {}, {}
    for s in sorted(set(wm.values())):
        if not dst.exists(s):
            fail('index names missing shard %s' % s)
            continue
        t, _, _ = read_header(dst, s)
        for n, v in t.items():
            if n in dwhere:
                fail('tensor %s in two output shards' % n)
            dwhere[n], dtens[n] = s, v
    for n in set(wm) | set(dwhere):
        if wm.get(n) != dwhere.get(n):
            fail('index/shard mismatch for %s: index %s, found in %s' % (n, wm.get(n), dwhere.get(n)))
    stray = [n for n in os.listdir(dst_dir) if n.endswith('.safetensors') and n not in set(wm.values())]
    if stray:
        fail('unreferenced .safetensors in DST: %s' % stray)
    partial = [n for n in os.listdir(dst_dir) if n.endswith(PARTIAL)]
    if partial:
        fail('incomplete cut: %s left in DST (interrupted? delete DST and cut again)' % partial)
    total = sum(v['end'] - v['start'] for v in dtens.values())
    if didx.get('metadata', {}).get('total_size') != total:
        fail('index total_size %s != sum of tensor bytes %d' % (didx.get('metadata', {}).get('total_size'), total))
    # expected tensors
    inv = {k: {o: j for j, o in enumerate(v)} for k, v in lay.items()}
    expected = {}   # dst name -> (src name, kind)
    for n, c in model['kinds'].items():
        if drop_mtp and is_mtp_name(n):
            continue
        if c[0] == 'expert':
            j = inv.get(c[1], {}).get(c[2])
            if j is not None:
                expected[expert_name(n, j)] = (n, 'copy')
        elif c[0] == 'router':
            expected[n] = (n, 'router')
        else:
            expected[n] = (n, 'copy')
    for n in sorted(set(expected) - set(dtens)):
        fail('missing in DST: %s' % n)
    for n in sorted(set(dtens) - set(expected)):
        fail('unexpected in DST: %s' % n)
    # content
    checked = {'copy': 0, 'router': 0}
    handles = {}

    def fh(base, s):
        key = (base, s)
        if key not in handles:
            handles[key] = open(os.path.join(base, s), 'rb')
        return handles[key]

    try:
        for n in sorted(set(expected) & set(dtens), key=lambda n: (dwhere[n], dtens[n]['start'])):
            sn, kind = expected[n]
            ss = model['where'][sn]
            st, dt = model['headers'][ss][0][sn], dtens[n]
            if st['dtype'] != dt['dtype']:
                fail('%s: dtype %s != source %s' % (n, dt['dtype'], st['dtype']))
                continue
            fs, fd = fh(src_dir, ss), fh(dst_dir, dwhere[n])
            if kind == 'router':
                c = model['kinds'][sn]
                rows = lay[c[1]]
                if dt['shape'] != [len(rows)] + st['shape'][1:]:
                    fail('%s: shape %s, expected %s' % (n, dt['shape'], [len(rows)] + st['shape'][1:]))
                    continue
                fs.seek(st['start'])
                sb = fs.read(st['end'] - st['start'])
                fd.seek(dt['start'])
                db = fd.read(dt['end'] - dt['start'])
                rb = len(sb) // E
                want = b''.join(sb[o * rb:(o + 1) * rb] for o in rows)
                if db != want:
                    bad = [j for j in range(len(rows)) if db[j * rb:(j + 1) * rb] != want[j * rb:(j + 1) * rb]]
                    fail('%s: router rows differ from source rows %s (first bad new rows %s)' % (n, c[1], bad[:5]))
                else:
                    checked['router'] += 1
                continue
            if dt['shape'] != st['shape']:
                fail('%s: shape %s != source %s' % (n, dt['shape'], st['shape']))
                continue
            left = st['end'] - st['start']
            if dt['end'] - dt['start'] != left:
                fail('%s: size differs' % n)
                continue
            fs.seek(st['start'])
            fd.seek(dt['start'])
            ok = True
            while left:
                k = min(CHUNK, left)
                if fs.read(k) != fd.read(k):
                    ok = False
                    break
                left -= k
            if not ok:
                fail('%s: bytes differ from source %s' % (n, sn))
            else:
                checked['copy'] += 1
    finally:
        for f in handles.values():
            f.close()
    for n in man.get('copied_files', []):
        if not os.path.isfile(os.path.join(dst_dir, n)) or sha256_file(os.path.join(dst_dir, n)) != sha256_file(os.path.join(src_dir, n)):
            fail('copied file %s differs or is missing' % n)
    if hashes:
        for s, v in man.get('output_files', {}).items():
            if sha256_file(os.path.join(dst_dir, s)) != v['sha256']:
                fail('output %s sha256 differs from manifest' % s)
        for s, v in man.get('source_files', {}).items():
            p = os.path.join(src_dir, s)
            if not os.path.isfile(p) or sha256_file(p) != v['sha256']:
                fail('source %s sha256 differs from manifest (different SRC build?)' % s)
    log('checked: %d tensors byte-equal, %d routers row-equal, %d layers, K=%d, mtp=%s' % (
        checked['copy'], checked['router'], len(lay), K, man.get('mtp')))
    log('VERIFY OK' if not fails else 'VERIFY FAILED: %d problem(s)' % len(fails))
    return fails


# ---------------------------------------------------------------- router comparison

def to_floats(b, dtype):
    if dtype == 'BF16':
        a = array('H')
        a.frombytes(b)
        if sys.byteorder != 'little':
            a.byteswap()
        f = array('f')
        f.frombytes(array('I', [x << 16 for x in a]).tobytes())
        return f
    if dtype in ('F32', 'F16'):
        a = array('f') if dtype == 'F32' else None
        if a is None:
            return array('f', struct.unpack('<%de' % (len(b) // 2), b))
        a.frombytes(b)
        return a
    raise CutError('router dtype %s: cannot compare numerically' % dtype)


def cos(a, b):
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def compare_router(src_spec, ref_spec, log=print, cos_ok=0.98):
    """Is SRC's router in the same expert order as REF's? Returns 'identical' | 'finetuned' | 'permuted'."""
    a, r = Source(src_spec), Source(ref_spec)
    ma = load_model(a)
    rcfg = r.read_json(CONFIG)
    rwm = r.read_json(INDEX)['weight_map']
    E = ma['E']
    if text_cfg(rcfg).get('num_experts') != E:
        raise CutError('REF num_experts %s != SRC %d' % (text_cfg(rcfg).get('num_experts'), E))
    rheaders = {}
    verdict = 'identical'
    skipped, compared = [], 0
    for key in sorted(ma['layers'], key=lambda k: (k.startswith('mtp.'), int(k.split('.')[-1]))):
        n = ma['layers'][key]['router']['weight']
        if n not in rwm:
            log('layer %s: %s not in REF, skipped' % (key, n))
            skipped.append(key)
            continue
        compared += 1
        s = ma['where'][n]
        t = ma['headers'][s][0][n]
        ab = a.read_at(s, t['start'], t['end'] - t['start'])
        rs = rwm[n]
        if rs not in rheaders:
            rheaders[rs] = read_header(r, rs)[0]
        rt = rheaders[rs][n]
        if rt['shape'] != t['shape'] or rt['dtype'] != t['dtype']:
            raise CutError('layer %s router: REF %s%s != SRC %s%s' % (key, rt['dtype'], rt['shape'], t['dtype'], t['shape']))
        rb = r.read_at(rs, rt['start'], rt['end'] - rt['start'])
        row = len(ab) // E
        same = [ab[i * row:(i + 1) * row] == rb[i * row:(i + 1) * row] for i in range(E)]
        if all(same):
            log('layer %s: %d/%d router rows byte-identical to REF' % (key, E, E))
            continue
        fa, fr = to_floats(ab, t['dtype']), to_floats(rb, t['dtype'])
        H = len(fa) // E
        A = [fa[i * H:(i + 1) * H] for i in range(E)]
        R = [fr[i * H:(i + 1) * H] for i in range(E)]
        cs = [1.0 if same[i] else cos(A[i], R[i]) for i in range(E)]
        low = [i for i in range(E) if not cs[i] >= cos_ok]   # NaN counts as low
        moved = []
        for i in low:
            best = max(range(E), key=lambda j: cos(A[i], R[j]))
            if best != i:
                moved.append((i, best))
        srt = sorted(cs)
        log('layer %s: %d identical rows, same-index cos min %.4f median %.4f, %d rows < %.2f, %d best-match elsewhere %s'
            % (key, sum(same), srt[0], srt[E // 2], len(low), cos_ok, len(moved), moved[:5]))
        if moved:
            verdict = 'permuted'
        elif verdict == 'identical':
            verdict = 'finetuned'
    if skipped or not compared:
        raise CutError('router comparison incomplete: compared %d layer(s), %d not found in REF under the same '
                       'tensor name (%s); a different name prefix in REF? No verdict.'
                       % (compared, len(skipped), skipped[:5]))
    log('ROUTER %s' % verdict.upper())
    return verdict


# ---------------------------------------------------------------- CLI

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('args', nargs='*', help='SRC KEEP_JSON DST  |  with --verify: SRC DST  |  with --compare-router: SRC REF')
    p.add_argument('--dry-run', action='store_true', help='read headers, validate, print the plan; write nothing')
    p.add_argument('--mtp', choices=['drop'], help='drop the MTP module instead of cutting it (no MTP keep lists)')
    p.add_argument('--verify', action='store_true', help='re-check a finished cut SRC DST by content')
    p.add_argument('--keep', help='with --verify: also check the manifest against this KEEP_JSON')
    p.add_argument('--hashes', action='store_true', help='with --verify: also re-hash source/output files against the manifest')
    p.add_argument('--compare-router', action='store_true', help='compare SRC router rows to REF (dir or hf:ORG/REPO[@REV])')
    a = p.parse_args(argv)
    try:
        if a.verify:
            if len(a.args) != 2:
                p.error('--verify needs SRC DST')
            return 1 if verify(a.args[0], a.args[1], a.keep, a.hashes) else 0
        if a.compare_router:
            if len(a.args) != 2:
                p.error('--compare-router needs SRC REF')
            return 1 if compare_router(a.args[0], a.args[1]) == 'permuted' else 0
        if len(a.args) != 3:
            p.error('need SRC KEEP_JSON DST')
        cut(a.args[0], a.args[1], a.args[2], a.dry_run, a.mtp)
        return 0
    except CutError as e:
        print('REFUSED: %s' % e, file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError) as e:   # missing files, network, malformed JSON/manifest
        print('REFUSED: %s: %s' % (type(e).__name__, e), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
