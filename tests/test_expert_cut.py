#!/usr/bin/env python3
"""CPU tests for bin/expert-cut.py on a tiny synthetic safetensors model.

Same naming as nvidia/Qwen3.8-Flash-Next-NVFP4 (4 layers x 16 experts, NVFP4-style
weight / weight_scale / weight_scale_2 / input_scale per expert, an MTP block with
weight_scale_inv, shared expert, PLE-like and attention tensors, experts of one layer
split across shards, router in another shard). Every tensor has distinct random bytes,
so any wrong mapping shows up as a byte mismatch. Run: python3 tests/test_expert_cut.py
"""
import contextlib
import warnings
warnings.simplefilter("ignore", ResourceWarning)
import importlib.util
import io
import json
import os
import random
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'bin' / 'expert-cut.py'
spec = importlib.util.spec_from_file_location('expert_cut', SCRIPT)
hc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hc)

L, E, H, TOPK = 4, 16, 8, 4
P = 'model.language_model.'
RNG = random.Random(1234)


def rb(n):
    return bytes(RNG.getrandbits(8) for _ in range(n))


def bf16(n):
    return b''.join(struct.pack('<f', RNG.gauss(0, 1))[2:] for _ in range(n))


# independent minimal reader (does not use the script's parser)
def read_st(path):
    with open(path, 'rb') as f:
        n = struct.unpack('<Q', f.read(8))[0]
        h = json.loads(f.read(n))
        data = f.read()
    h.pop('__metadata__', None)
    return {k: (v['dtype'], v['shape'], data[v['data_offsets'][0]:v['data_offsets'][1]]) for k, v in h.items()}


def read_dir(d):
    idx = json.load(open(os.path.join(d, hc.INDEX)))
    out = {}
    for s in sorted(set(idx['weight_map'].values())):
        for k, v in read_st(os.path.join(d, s)).items():
            out[k] = v + (s,)
    return idx, out


def expert_tensors(pfx, e, mtp=False):
    t = []
    for proj, shp in (('gate_proj', [6, 4]), ('up_proj', [6, 4]), ('down_proj', [8, 3])):
        n = '%sexperts.%d.%s.' % (pfx, e, proj)
        if mtp:
            t.append((n + 'weight', 'F8_E4M3', shp, rb(shp[0] * shp[1])))
            t.append((n + 'weight_scale_inv', 'F32', [1, 1], rb(4)))
        else:
            t.append((n + 'weight', 'U8', shp, rb(shp[0] * shp[1])))
            t.append((n + 'weight_scale', 'F8_E4M3', [shp[0], 1], rb(shp[0])))
            t.append((n + 'weight_scale_2', 'F32', [], rb(4)))
            t.append((n + 'input_scale', 'F32', [], rb(4)))
    return t


def make_model(d, fused=False, mtp=True, quant_extra=None, drop_one_scale=False, bad_leaf=False):
    os.makedirs(d)
    shards = {'model-00001-of-00003.safetensors': [], 'model-00002-of-00003.safetensors': [],
              'model-00003-of-00003.safetensors': []}
    s1, s2, s3 = list(shards)
    shards[s1].append((P + 'embed_tokens.weight', 'BF16', [32, H], rb(32 * H * 2)))
    for li in range(L):
        mp = '%slayers.%d.mlp.' % (P, li)
        sh = s1 if li < 2 else s2
        shards[sh].append(('%slayers.%d.self_attn.k_proj.weight' % (P, li), 'BF16', [E, H], rb(E * H * 2)))
        rsh = s3 if li == 1 else sh                       # router of layer 1 in another shard
        shards[rsh].append((mp + 'gate.weight', 'BF16', [E, H], bf16(E * H)))
        shards[sh].append((mp + 'shared_expert.gate_proj.weight', 'BF16', [6, H], rb(6 * H * 2)))
        shards[sh].append((mp + 'shared_expert_gate.weight', 'BF16', [1, H], rb(H * 2)))
        if fused and li == 2:
            shards[sh].append((mp + 'experts.gate_up_proj', 'BF16', [E, 12, H], rb(E * 12 * H * 2)))
            shards[sh].append((mp + 'experts.down_proj', 'BF16', [E, H, 6], rb(E * 6 * H * 2)))
            continue
        for e in range(E):
            t = expert_tensors(mp, e)
            if li == 1 and e >= E // 2:                    # layer 1 experts split over shards
                shards[s2].extend(t)
            else:
                shards[sh].extend(t)
    if drop_one_scale:
        shards[s1] = [t for t in shards[s1] if t[0] != P + 'layers.0.mlp.experts.5.down_proj.weight_scale_2']
    if bad_leaf:
        shards[s1].append((P + 'layers.0.mlp.experts.5.down_proj.weight_mystery', 'U8', [2], rb(2)))
    shards[s2].append((P + 'layers.1.ple.ple_embedding.ngram_embedding.shard_0.weight', 'F8_E4M3', [E, H], rb(E * H)))
    if mtp:
        mp = 'mtp.layers.0.mlp.'
        shards[s3].append(('mtp.fc_hidden.weight', 'BF16', [H, H], rb(H * H * 2)))
        shards[s3].append((mp + 'gate.weight', 'BF16', [E, H], bf16(E * H)))
        shards[s3].append((mp + 'shared_expert.up_proj.weight', 'BF16', [6, H], rb(6 * H * 2)))
        for e in range(E):
            shards[s3].extend(expert_tensors(mp, e, mtp=True))
    shards[s3].append(('lm_head.weight', 'BF16', [32, H], rb(32 * H * 2)))
    wm = {}
    total = 0
    for s, ts in shards.items():
        RNG.shuffle(ts)                                   # data order != name order
        hc.write_safetensors(os.path.join(d, s), ts, {'format': 'pt'})
        for t in ts:
            wm[t[0]] = s
            total += len(t[3])
    json.dump({'metadata': {'total_size': total}, 'weight_map': wm}, open(os.path.join(d, hc.INDEX), 'w'))
    q = {'quant_method': 'modelopt', 'ignore': [P + 'layers.0.mlp.gate'],
         'quantized_layers': {P + 'layers.0.mlp.experts': {'quant_algo': 'NVFP4'}}}
    if quant_extra:
        q['ignore'].append(quant_extra)
    cfg = {'architectures': ['Qwen4ExpForConditionalGeneration'], 'model_type': 'qwen4_exp',
           'quantization_config': q,
           'text_config': {'num_experts': E, 'num_experts_per_tok': TOPK, 'num_hidden_layers': L,
                           'mtp_num_hidden_layers': 1, 'hidden_size': H}}
    json.dump(cfg, open(os.path.join(d, 'config.json'), 'w'), indent=2)
    for n, c in (('tokenizer.json', '{"v": 1}'), ('chat_template.jinja', '{{ x }}\n'),
                 ('generation_config.json', '{"top_k": 20}')):
        open(os.path.join(d, n), 'w').write(c)


KEEP = {str(li): sorted(RNG.sample(range(E), 6)) for li in range(L)}
KEEP['mtp.0'] = [0, 3, 4, 9, 10, 15]


def quiet():
    return contextlib.redirect_stdout(io.StringIO())


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='hfcut-')
        self.src = os.path.join(self.tmp, 'src')
        self.dst = os.path.join(self.tmp, 'dst')
        make_model(self.src)
        self.keep = self.write_keep(KEEP)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write_keep(self, keep, name='keep.json'):
        p = os.path.join(self.tmp, name)
        json.dump(keep, open(p, 'w'))
        return p

    def cut(self, keep=None, mtp=None):
        with quiet():
            hc.cut(self.src, keep or self.keep, self.dst, mtp_policy=mtp)

    def verify(self, keep=None):
        with quiet():
            return hc.verify(self.src, self.dst, keep, hashes=True)

    def rewrite_dst(self, edit):
        """edit(name -> (dtype, shape, bytes, shard)) dict in place, then rewrite DST shards + index."""
        idx, t = read_dir(self.dst)
        edit(t)
        by = {}
        for n, (dt, sh, b, s) in t.items():
            by.setdefault(s, []).append((n, dt, sh, b))
        for s in set(idx['weight_map'].values()):
            os.remove(os.path.join(self.dst, s))
        for s, ts in by.items():
            hc.write_safetensors(os.path.join(self.dst, s), ts, {'format': 'pt'})
        idx['weight_map'] = {n: v[3] for n, v in t.items()}
        idx['metadata']['total_size'] = sum(len(v[2]) for v in t.values())
        json.dump(idx, open(os.path.join(self.dst, hc.INDEX), 'w'))


class TestCut(Base):
    def test_content(self):
        self.cut()
        self.assertEqual(self.verify(self.keep), [])
        _, s = read_dir(self.src)
        idx, d = read_dir(self.dst)
        K = 6
        for key, kept in KEEP.items():
            pfx = 'mtp.layers.0.mlp.' if key.startswith('mtp') else '%slayers.%s.mlp.' % (P, key)
            sg, dg = s[pfx + 'gate.weight'], d[pfx + 'gate.weight']
            row = H * 2
            self.assertEqual(dg[1], [K, H])
            self.assertEqual(dg[0], 'BF16')
            for j, o in enumerate(sorted(kept)):          # router rows: kept, in ascending original order
                self.assertEqual(dg[2][j * row:(j + 1) * row], sg[2][o * row:(o + 1) * row])
            for j, o in enumerate(sorted(kept)):          # experts byte-equal under the mapping
                for n in [x for x in s if x.startswith('%sexperts.%d.' % (pfx, o))]:
                    m = n.replace('experts.%d.' % o, 'experts.%d.' % j)
                    self.assertEqual(d[m][:3], s[n][:3], m)
            nd = [x for x in d if x.startswith(pfx + 'experts.')]
            ns = [x for x in s if x.startswith(pfx + 'experts.0.')]
            self.assertEqual(len(nd), K * len(ns))
        for n, v in s.items():                            # non-expert tensors byte-equal
            if '.experts.' not in n and not n.endswith('mlp.gate.weight'):
                self.assertEqual(d[n][:3], v[:3], n)
        cfg = json.load(open(os.path.join(self.dst, 'config.json')))
        scfg = json.load(open(os.path.join(self.src, 'config.json')))
        self.assertEqual(cfg['text_config']['num_experts'], K)
        scfg['text_config']['num_experts'] = K
        self.assertEqual(cfg, scfg)
        self.assertEqual(idx['metadata']['total_size'], sum(len(v[2]) for v in d.values()))
        self.assertEqual(set(idx['weight_map']), set(d))
        for n in ('tokenizer.json', 'chat_template.jinja', 'generation_config.json'):
            self.assertEqual(open(os.path.join(self.src, n), 'rb').read(), open(os.path.join(self.dst, n), 'rb').read())
        man = json.load(open(os.path.join(self.dst, hc.MANIFEST)))
        self.assertEqual(man['layers']['1']['new_to_original'], sorted(KEEP['1']))
        self.assertEqual(man['source_files']['model-00001-of-00003.safetensors']['sha256'],
                         hc.sha256_file(os.path.join(self.src, 'model-00001-of-00003.safetensors')))

    def test_verify_reads_old_manifest_name(self):        # cuts made before the rename keep verifying
        keep = {k: v for k, v in KEEP.items() if not k.startswith('mtp')}
        self.cut(self.write_keep(keep, 'k3.json'), mtp='drop')
        os.rename(os.path.join(self.dst, hc.MANIFEST), os.path.join(self.dst, hc.OLD_MANIFEST))
        self.assertEqual(self.verify(), [])

    def test_mtp_drop(self):
        keep = {k: v for k, v in KEEP.items() if not k.startswith('mtp')}
        self.cut(self.write_keep(keep, 'k2.json'), mtp='drop')
        _, d = read_dir(self.dst)
        self.assertFalse([n for n in d if n.startswith('mtp.')])
        self.assertEqual(self.verify(), [])

    def test_mtp_policy_required(self):
        keep = {k: v for k, v in KEEP.items() if not k.startswith('mtp')}
        with self.assertRaisesRegex(hc.CutError, 'MTP'):
            self.cut(self.write_keep(keep, 'k2.json'))

    def test_topk_not_below_k(self):
        keep = {k: v[:TOPK] for k, v in KEEP.items()}
        with self.assertRaisesRegex(hc.CutError, 'top-k'):
            self.cut(self.write_keep(keep, 'k3.json'))

    def test_keep_layers_must_match(self):
        keep = dict(KEEP)
        del keep['2']
        with self.assertRaisesRegex(hc.CutError, 'missing'):
            self.cut(self.write_keep(keep, 'k4.json'))
        keep = dict(KEEP, **{'2': KEEP['2'][:-1] + [E]})
        with self.assertRaisesRegex(hc.CutError, 'out of range'):
            self.cut(self.write_keep(keep, 'k5.json'))

    def test_refuses_already_cut_source(self):            # original ids against a source that no longer has them
        self.cut()
        self.assertTrue(any(max(v) >= len(v) for v in KEEP.values()))
        with self.assertRaisesRegex(hc.CutError, 'out of range'):
            with quiet():
                hc.cut(self.dst, self.keep, os.path.join(self.tmp, 'dst2'))

    def test_dry_run_writes_nothing(self):
        with quiet():
            r = hc.cut(self.src, self.keep, self.dst, dry_run=True)
        self.assertFalse(os.path.exists(self.dst))
        self.assertEqual(r['K'], 6)

    def test_refuses_low_disk(self):
        orig = hc.shutil.disk_usage
        hc.shutil.disk_usage = lambda p: orig(p)._replace(free=1000)
        try:
            with self.assertRaisesRegex(hc.CutError, 'not enough space'):
                self.cut()
        finally:
            hc.shutil.disk_usage = orig
        self.assertFalse([n for n in os.listdir(self.dst) if n.endswith('.safetensors')])

    def test_no_partial_left_and_partial_detected(self):
        self.cut()
        self.assertFalse([n for n in os.listdir(self.dst) if n.endswith(hc.PARTIAL)])
        open(os.path.join(self.dst, 'model-00002-of-00003.safetensors' + hc.PARTIAL), 'wb').write(b'x')
        self.assertTrue(any('incomplete cut' in f for f in self.verify()))

    def test_cli_errors_are_refusals(self):
        os.makedirs(self.dst)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            rc = hc.main(['--verify', self.src, self.dst])        # no manifest
        self.assertEqual(rc, 2)
        self.assertIn('REFUSED', err.getvalue())

    def test_refuses_nonempty_dst(self):
        os.makedirs(self.dst)
        open(os.path.join(self.dst, 'x'), 'w').write('x')
        with self.assertRaisesRegex(hc.CutError, 'not empty'):
            self.cut()


class TestUnknownLayouts(Base):
    def remake(self, **kw):
        shutil.rmtree(self.src)
        make_model(self.src, **kw)

    def test_fused_3d(self):
        self.remake(fused=True)
        with self.assertRaisesRegex(hc.CutError, 'fused'):
            self.cut()

    def test_unknown_leaf(self):
        self.remake(bad_leaf=True)
        with self.assertRaisesRegex(hc.CutError, 'unknown projection/leaf'):
            self.cut()

    def test_source_missing_scale(self):
        self.remake(drop_one_scale=True)
        with self.assertRaisesRegex(hc.CutError, 'missing scale'):
            self.cut()

    def test_expert_indexed_quant_config(self):
        self.remake(quant_extra=P + 'layers.0.mlp.experts.7.down_proj')
        with self.assertRaisesRegex(hc.CutError, 'names an individual expert'):
            self.cut()


class TestMutants(Base):
    """Each mutant must make --verify fail (proof that the gate can fail)."""

    def assertVerifyFails(self, pattern):
        fails = self.verify(self.keep)
        self.assertTrue(fails, 'verify passed a broken cut')
        self.assertTrue(any(pattern in f for f in fails), fails[:5])

    def test_wrong_order(self):                           # cutter keeps the keep list in reverse order
        orig = hc.order_keep
        hc.order_keep = lambda ids: sorted(ids, reverse=True)
        try:
            self.cut()
        finally:
            hc.order_keep = orig
        self.assertVerifyFails('not strictly ascending')

    def test_swapped_experts_in_dst(self):                # manifest fine, two experts' bytes swapped
        self.cut()

        def edit(t):
            a, b = P + 'layers.3.mlp.experts.1.', P + 'layers.3.mlp.experts.2.'
            for n in [x for x in t if x.startswith(a)]:
                m = n.replace(a, b)
                t[n], t[m] = t[m], t[n]
        self.rewrite_dst(edit)
        self.assertVerifyFails('bytes differ')

    def test_off_by_one_mapping(self):                    # new j filled from original keep[j] + 1
        orig = hc.build_inverse
        hc.build_inverse = lambda n2o: {o + 1: j for j, o in enumerate(n2o)}
        try:
            self.cut()
        finally:
            hc.build_inverse = orig
        self.assertVerifyFails('bytes differ')

    def test_router_rows_permuted(self):
        self.cut()

        def edit(t):
            n = P + 'layers.0.mlp.gate.weight'
            dt, sh, b, s = t[n]
            r = H * 2
            t[n] = (dt, sh, b[r:2 * r] + b[:r] + b[2 * r:], s)
        self.rewrite_dst(edit)
        self.assertVerifyFails('router rows differ')

    def test_dropped_scale_tensor(self):
        self.cut()
        self.rewrite_dst(lambda t: t.pop(P + 'layers.2.mlp.experts.4.gate_proj.weight_scale_2'))
        self.assertVerifyFails('missing in DST')

    def test_extra_expert_in_dst(self):
        self.cut()

        def edit(t):
            n = P + 'layers.2.mlp.experts.0.up_proj.weight'
            t[n.replace('experts.0.', 'experts.6.')] = t[n]
        self.rewrite_dst(edit)
        self.assertVerifyFails('unexpected in DST')

    def test_config_not_updated(self):
        self.cut()
        p = os.path.join(self.dst, 'config.json')
        c = json.load(open(p))
        c['text_config']['num_experts'] = E
        json.dump(c, open(p, 'w'))
        self.assertVerifyFails('config.json')

    def test_index_wrong_shard(self):
        self.cut()
        p = os.path.join(self.dst, hc.INDEX)
        idx = json.load(open(p))
        n = P + 'embed_tokens.weight'
        idx['weight_map'][n] = 'model-00003-of-00003.safetensors'
        json.dump(idx, open(p, 'w'))
        self.assertVerifyFails('index/shard mismatch')

    def test_non_expert_byte_flip(self):
        self.cut()

        def edit(t):
            n = 'lm_head.weight'
            dt, sh, b, s = t[n]
            t[n] = (dt, sh, bytes([b[0] ^ 1]) + b[1:], s)
        self.rewrite_dst(edit)
        self.assertVerifyFails('bytes differ')

    def test_keep_mismatch(self):
        self.cut()
        other = dict(KEEP, **{'0': sorted(set(range(E)) - set(KEEP['0']))[:6]})
        with quiet():
            fails = hc.verify(self.src, self.dst, self.write_keep(other, 'o.json'))
        self.assertTrue(any('differ from' in f for f in fails))


class TestCompareRouter(Base):
    def ref_with(self, change):
        ref = os.path.join(self.tmp, 'ref')
        shutil.copytree(self.src, ref)
        idx = json.load(open(os.path.join(ref, hc.INDEX)))
        s = idx['weight_map'][P + 'layers.0.mlp.gate.weight']
        t = read_st(os.path.join(ref, s))
        n = P + 'layers.0.mlp.gate.weight'
        t[n] = (t[n][0], t[n][1], change(t[n][2]))
        hc.write_safetensors(os.path.join(ref, s), [(k, v[0], v[1], v[2]) for k, v in t.items()])
        return ref

    def run_cmp(self, ref):
        with quiet():
            return hc.compare_router(self.src, ref)

    def test_identical(self):
        self.assertEqual(self.run_cmp(self.src), 'identical')

    def test_permuted(self):
        r = H * 2
        self.assertEqual(self.run_cmp(self.ref_with(lambda b: b[r:2 * r] + b[:r] + b[2 * r:])), 'permuted')

    def test_prefix_mismatch_is_no_verdict(self):
        # REF stores routers under another prefix AND has permuted rows: must not pass as IDENTICAL
        r = H * 2
        ref = self.ref_with(lambda b: b[r:2 * r] + b[:r] + b[2 * r:])
        idx = json.load(open(os.path.join(ref, hc.INDEX)))
        for s in set(idx['weight_map'].values()):
            t = read_st(os.path.join(ref, s))
            hc.write_safetensors(os.path.join(ref, s), [(k.replace(P, 'model.'), v[0], v[1], v[2]) for k, v in t.items()])
        idx['weight_map'] = {k.replace(P, 'model.'): v for k, v in idx['weight_map'].items()}
        json.dump(idx, open(os.path.join(ref, hc.INDEX), 'w'))
        with self.assertRaisesRegex(hc.CutError, 'incomplete'):
            self.run_cmp(ref)
        with contextlib.redirect_stderr(io.StringIO()), quiet():
            self.assertEqual(hc.main(['--compare-router', self.src, ref]), 2)

    def test_finetuned(self):
        # flip the lowest mantissa bit of every BF16 value: tiny change, same order
        self.assertEqual(self.run_cmp(self.ref_with(lambda b: bytes(x ^ 1 if i % 2 == 0 else x for i, x in enumerate(b)))),
                         'finetuned')


if __name__ == '__main__':
    unittest.main(verbosity=1)
