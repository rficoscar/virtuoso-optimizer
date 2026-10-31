# -*- coding: utf-8 -*-
from __future__ import print_function

import json
import math
import os
import sqlite3
import time
import uuid

__all__ = [
    "SCHEMA_VERSION", "RECORD_SCHEMA",
    "quantize_electrical", "grid_key_of", "fingerprint_match", "distance_of",
    "delta_vs_request_of", "delivered_of", "validate_grid_spec",
    "EmLibrary", "EmBridge",
    "clip_to_region", "stage_report", "stage_keys_of", "DEVICE_STAGES",
]

SCHEMA_VERSION = 1
RECORD_SCHEMA = "em_record/1"
REQUEST_SCHEMA = "em_request/1"

_ANG_KEYS = ("theta", "theta_e", "theta_o")
_DELIVERED_KEYS = {
    "ind": ("L0",), "ct": ("L0",),
    "tl": ("Z0",), "xfmr": ("Lp", "Ls", "k"),
    "cpl": ("Ze", "Zo", "theta_e", "theta_o"),
}

DEFAULT_GRID_SPEC = {"step_rel": 0.01, "step_abs_deg": 2.0}
DEFAULT_TOL = {"L0": 0.01, "Q@f0": 0.15, "SRF": 0.25, "theta_tol_deg": 2.0}
DEFAULT_SRF_MARGIN = 1.25
DEFAULT_Q_MIN = 5.0

class _F(object):
    DISTANCE = "distance"
    ACTUAL = "actual_fingerprint"
    DELIVERED = "delivered"
    DELTA = "delta_vs_request"
    REQUEST = "request_fingerprint"
    GRID_KEY = "grid_key"
    RECORD_ID = "record_id"
    SNP_SHA1 = "snp_sha1"
    SNP_PATH = "snp_path"
    DEVICE = "device_type"
    TECH = "tech"
    CFG_HASH = "emx_cfg_hash"
    USABLE = "usable"
    SOURCE = "source"
    STATUS = "status"
    WARNS = "warns"
    SCHEMA = "schema"
    SCHEMA_VER = "schema_version"
    GEOM = "geom"
    GEOM_HASH = "geom_hash"
    EM_META = "em_meta"
    GRID_SPEC = "grid_spec"

def _sha1_file(path):
    import hashlib
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()

def _ang_key(key):
    return key in _ANG_KEYS

def quantize_electrical(value, grid_spec, key=None):
    if value is None:
        raise ValueError("quantize_electrical: value is None")
    spec = grid_spec or DEFAULT_GRID_SPEC
    v = float(value)
    if _ang_key(key):
        step = float(spec.get("step_abs_deg", 2.0))
    else:
        step = float(spec.get("step_rel", 0.01)) * abs(v)
        if step <= 0.0:
            step = float(spec.get("step_rel", 0.01))
    if step <= 0.0:
        return v
    return round(v / step) * step

def validate_grid_spec(grid_spec, tolerances=None):
    spec = grid_spec or DEFAULT_GRID_SPEC
    tols = tolerances or DEFAULT_TOL
    rel = float(spec.get("step_rel", 0.01))
    ang = float(spec.get("step_abs_deg", 2.0))
    for k, tv in tols.items():
        if _ang_key(k):
            continue
        try:
            t = float(tv)
        except (TypeError, ValueError):
            continue
        if t > 0 and rel > 2.0 * t:
            raise ValueError("grid_spec.step_rel=%g > 2*tol[%s]=%g（过疏，C-B4）" % (rel, k, 2 * t))
    tt = tols.get("theta_tol_deg", tols.get("theta"))
    if tt is not None:
        try:
            tt = float(tt)
        except (TypeError, ValueError):
            tt = None
        if tt and tt > 0 and ang > 2.0 * tt:
            raise ValueError("grid_spec.step_abs_deg=%g > 2*theta_tol_deg=%g（过疏）" % (ang, 2 * tt))
    return True

def grid_key_of(device_type, target, grid_spec, tech=None, cfg_hash=None):
    spec = grid_spec or DEFAULT_GRID_SPEC
    dev = (device_type or "ind").lower()
    parts = []
    for k in _DELIVERED_KEYS.get(dev, ("L0",)):
        v = (target or {}).get(k)
        if v is None or isinstance(v, bool):
            continue
        if not isinstance(v, (int, float)):
            parts.append("%s=%s" % (k, v))
            continue
        parts.append("%s=%.10g" % (k, quantize_electrical(v, spec, key=k)))
    return "%s:%s" % (dev, ",".join(parts))

def _as_rel_tol(tol, key):
    if _ang_key(key):
        if tol is None:
            return DEFAULT_TOL.get("theta_tol_deg", 2.0)
        if isinstance(tol, str) and tol.strip().endswith("%"):
            return float(tol.strip()[:-1]) / 100.0 * 90.0
        return float(tol)
    if tol is None:
        return DEFAULT_TOL.get(key, DEFAULT_TOL.get("Q@f0", 0.01))
    if isinstance(tol, str):
        t = tol.strip()
        if t.endswith("%"):
            return float(t[:-1]) / 100.0
        return float(t)
    return float(tol)

_HZ_WORDS = (
    ("gigahertz", 1e9), ("gig", 1e9), ("ghz", 1e9),
    ("megahertz", 1e6), ("meg", 1e6), ("mhz", 1e6),
    ("kilo", 1e3), ("khz", 1e3), ("hz", 1.0),
)
_HZ_LETTERS = (
    ("T", 1e12), ("G", 1e9), ("k", 1e3), ("K", 1e3), ("M", 1e6),
    ("m", 1e-3), ("u", 1e-6), ("n", 1e-9), ("p", 1e-12),
    ("f", 1e-15), ("a", 1e-18),
)

def _as_hz(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    try:
        return float(s)
    except ValueError:
        pass
    low = s.lower()
    for suf, mult in _HZ_WORDS:
        if low.endswith(suf) and len(low) > len(suf):
            try:
                return float(s[:len(s) - len(suf)]) * mult
            except ValueError:
                continue
    for suf, mult in _HZ_LETTERS:
        if s.endswith(suf) and len(s) > len(suf):
            try:
                return float(s[:-len(suf)]) * mult
            except ValueError:
                continue
    raise ValueError("无法解析频率 %r（支持 28G/2.4Gig/2.4GHz/500meg/28e9/1.5M）" % v)

def _q_at_f0(fp, f0):
    if not fp:
        return None
    if fp.get("Q@f0") is not None:
        return fp.get("Q@f0")
    q = fp.get("Q")
    if isinstance(q, dict) and q:
        f0v = None
        if f0 is not None:
            try:
                f0v = _as_hz(f0)
            except (TypeError, ValueError):
                f0v = None
        if f0v is not None:
            try:
                pts = sorted(((float(k), v) for k, v in q.items()
                              if v is not None), key=lambda p: p[0])
            except (TypeError, ValueError):
                pts = []
            if pts:
                if f0v <= pts[0][0]:
                    return float(pts[0][1])
                if f0v >= pts[-1][0]:
                    return float(pts[-1][1])
                for i in range(1, len(pts)):
                    if pts[i][0] >= f0v:
                        f1, v1 = pts[i - 1]
                        f2, v2 = pts[i]
                        if f2 == f1:
                            return float(v2)
                        return float(v1) + ((f0v - f1) / (f2 - f1) * (float(v2) - float(v1)))
        try:
            k0 = sorted(q.keys(), key=lambda s: float(s))[0]
            return float(q[k0])
        except (TypeError, ValueError, KeyError):
            return None
    if isinstance(q, (int, float)):
        return float(q)
    qc = fp.get("Qcurve") or []
    if not qc or f0 is None:
        return None
    try:
        f0 = _as_hz(f0)
    except (TypeError, ValueError):
        return None
    try:
        pts = sorted(qc, key=lambda p: float(p[0]))
    except Exception:
        return None
    if f0 <= pts[0][0]:
        return pts[0][1]
    if f0 >= pts[-1][0]:
        return pts[-1][1]
    for i in range(1, len(pts)):
        if float(pts[i][0]) >= f0:
            f1, v1 = float(pts[i - 1][0]), pts[i - 1][1]
            f2, v2 = float(pts[i][0]), pts[i][1]
            if f2 == f1:
                return v2
            if v1 is None or v2 is None:
                return v1 or v2
            return v1 + (f0 - f1) / (f2 - f1) * (v2 - v1)
    return pts[-1][1]

def _metric_val(fp, key, f0=None):
    if not fp:
        return None
    if key == "Q@f0":
        return _q_at_f0(fp, f0)
    return fp.get(key)

def distance_of(actual_fp, target_fp, tolerances=None, f0=None):
    tolerances = tolerances or {}
    target_fp = target_fp or {}
    actual_fp = actual_fp or {}
    f0 = f0 if f0 is not None else target_fp.get("f0")
    per = {}
    dmax = 0.0
    any_m = False
    for key, tv in target_fp.items():
        if key.startswith("_") or key in ("f0", "band", "kind", "ports", "band_hi"):
            continue
        if tv is None or isinstance(tv, bool):
            continue
        try:
            tv = float(tv)
        except (TypeError, ValueError):
            continue
        av = _metric_val(actual_fp, key, f0)
        if av is None:
            per[key] = {"delta": None, "normalized": 99.0, "in_tol": False}
            dmax = max(dmax, 99.0)
            any_m = True
            continue
        delta = abs(float(av) - tv)
        if _ang_key(key):
            norm = delta / max(abs(_as_rel_tol(tolerances.get(key), key)), 1e-12)
        else:
            tol_rel = _as_rel_tol(tolerances.get(key), key)
            if key == "SRF":
                per[key] = {"delta": delta, "normalized": 0.0, "in_tol": True}
                any_m = True
                continue
            norm = delta / max(tol_rel * max(abs(tv), 1e-30), 1e-30)
        per[key] = {"delta": delta, "normalized": norm, "in_tol": norm <= 1.0}
        dmax = max(dmax, norm)
        any_m = True
    if not any_m:
        return float("inf"), {}
    return float(dmax), per

DEVICE_STAGES = {
    "xfmr": (("Lp",), ("Lp", "Ls"), ("Lp", "Ls", "k")),
    "cpl":  (("Ze",), ("Ze", "Zo"), ("Ze", "Zo", "theta_e", "theta_o")),
    "ind":  (("L0",),),
    "ct":   (("L0",),),
    "tl":   (("Z0",), ("Z0", "theta")),
}

def stage_keys_of(device_type, stage=None):
    seq = DEVICE_STAGES.get((device_type or "ind").lower(), DEVICE_STAGES["ind"])
    if stage is None:
        return seq[-1]
    idx = max(0, min(int(stage), len(seq) - 1))
    return seq[idx]

def stage_report(actual_fp, target_fp, tol, f0=None, device_type=None):
    dev = (device_type or (target_fp or {}).get("kind") or "ind").lower()
    seq = DEVICE_STAGES.get(dev, DEVICE_STAGES["ind"])
    f0 = f0 if f0 is not None else (target_fp or {}).get("f0")
    _d, per_map = distance_of(actual_fp or {}, target_fp or {}, tol or {}, f0=f0)
    in_tol = {}
    for keys in seq:
        for k in keys:
            if k in per_map:
                in_tol[k] = bool(per_map[k].get("in_tol"))
    hit_stage = -1
    for i, keys in enumerate(seq):
        if all(in_tol.get(k, True) for k in keys):
            hit_stage = i
    return {"stage": hit_stage,
            "keys": seq[hit_stage] if hit_stage >= 0 else (),
            "in_tol": in_tol,
            "ok": hit_stage == len(seq) - 1,
            "n_stages": len(seq)}

def fingerprint_match(target_fp, actual_fp, tol, f0=None, stage=None, device_type=None):
    if not target_fp or not actual_fp:
        return False, float("inf")
    tg = target_fp
    ac = actual_fp
    if tg.get("kind") and ac.get("kind") and tg["kind"] != ac["kind"]:
        return False, float("inf")
    if tg.get("ports") and ac.get("ports") and list(tg["ports"]) != list(ac["ports"]):
        return False, float("inf")
    tols = tol or {}
    f0 = f0 if f0 is not None else tg.get("f0")
    srf_a = ac.get("SRF")
    band_hi = tg.get("band_hi", tols.get("band_hi"))
    margin = float(tols.get("srf_margin", DEFAULT_SRF_MARGIN))
    if band_hi is not None:
        if srf_a is None or float(srf_a) < float(band_hi) * margin:
            return False, float("inf")
    elif tg.get("SRF") is not None and tols.get("SRF") is not None:
        if srf_a is None or float(srf_a) < float(tg["SRF"]):
            return False, float("inf")
    q_min = tols.get("Q_min", tols.get("q_min", DEFAULT_Q_MIN))
    q_a = _q_at_f0(ac, f0)
    if q_min is not None and (q_a is None or float(q_a) < float(q_min)):
        return False, float("inf")
    dev = (device_type or tg.get("kind") or "ind").lower()
    if stage is not None:
        keep = set(stage_keys_of(dev, stage))
        tg = dict((k, v) for k, v in tg.items()
                  if k in keep or k in ("kind", "ports", "f0", "band_hi",
                                        "SRF", "Q@f0"))
    d, _per = distance_of(ac, tg, tols, f0=f0)
    if d == float("inf"):
        return False, d
    return d <= 1.0, float(d)

def delta_vs_request_of(actual_fp, target_fp, f0=None):
    out = {}
    target_fp = target_fp or {}
    actual_fp = actual_fp or {}
    f0 = f0 if f0 is not None else target_fp.get("f0")
    for key, tv in target_fp.items():
        if key.startswith("_") or key in ("f0", "band", "kind", "ports", "band_hi"):
            continue
        if tv is None or isinstance(tv, bool):
            continue
        try:
            tv = float(tv)
        except (TypeError, ValueError):
            continue
        av = _metric_val(actual_fp, key, f0)
        if av is None:
            out[key] = None
            continue
        base = abs(tv) if abs(tv) > 0 else 1.0
        out[key] = (float(av) - tv) / base
    return out

def delivered_of(device_type, actual_fp):
    keys = _DELIVERED_KEYS.get((device_type or "ind").lower(), ("L0",))
    fp = actual_fp or {}
    if len(keys) == 1:
        return fp.get(keys[0])
    return dict((k, fp.get(k)) for k in keys)

_SQL_DDL = """
CREATE TABLE IF NOT EXISTS em_record (
  record_id      TEXT PRIMARY KEY,
  schema_version INTEGER NOT NULL DEFAULT 1,
  grid_key       TEXT NOT NULL,
  device_type    TEXT NOT NULL,
  tech           TEXT,
  emx_cfg_hash   TEXT,
  geom_json      TEXT,
  geom_hash      TEXT,
  snp_path       TEXT,
  snp_sha1       TEXT,
  actual_json    TEXT NOT NULL,
  request_json   TEXT,
  distance       REAL,
  usable         INTEGER NOT NULL DEFAULT 1,
  source         TEXT,
  warns_json     TEXT,
  grid_spec_json TEXT,
  em_meta_json   TEXT,
  created_utc    TEXT
);
CREATE INDEX IF NOT EXISTS idx_em_key ON em_record (grid_key, distance);
CREATE INDEX IF NOT EXISTS idx_em_dev ON em_record (device_type, tech, usable);
CREATE INDEX IF NOT EXISTS idx_em_sha ON em_record (snp_sha1);
"""

def clip_to_region(target_fp, region, device_type=None):
    if not target_fp or not region:
        return dict(target_fp or {}), []
    out = dict(target_fp)
    events = []
    for k, bounds in region.items():
        if k not in out:
            continue
        v = out[k]
        if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        try:
            lo, hi = float(bounds[0]), float(bounds[1])
        except (TypeError, ValueError, IndexError):
            continue
        vv = float(v)
        if vv < lo:
            out[k] = lo
            events.append("%s %.6g->%.6g (lo)" % (k, vv, lo))
        elif vv > hi:
            out[k] = hi
            events.append("%s %.6g->%.6g (hi)" % (k, vv, hi))
    return out, events

def _empty_result(status, request, warns, record=None, message=None):
    out = {_F.STATUS: status, _F.REQUEST: request, _F.ACTUAL: None,
           _F.DELIVERED: None, _F.DELTA: {}, _F.DISTANCE: None,
           _F.WARNS: warns, "record": record}
    if message is not None:
        out["message"] = message
    return out

class EmLibrary(object):
    def __init__(self, path=None):
        root = path or os.environ.get("LIBRF_EM_LIB") or os.path.join(os.getcwd(), "work", "em_lib")
        root = os.path.abspath(os.path.expanduser(str(root)))
        self._json_mode = False
        if root.endswith(".sqlite3") or root.endswith(".db"):
            self.db_path, self.root = root, os.path.dirname(root)
        elif root.endswith(".json") or root.endswith(".jsonl"):
            self.db_path = root
            self.root = os.path.dirname(root) or "."
            self._json_mode = True
        else:
            self.root = root
            self.db_path = os.path.join(root, "em_lib.sqlite3")
        self.snp_dir = os.path.join(self.root, "snp")
        self._records = []
        self._conn = None
        if self._json_mode:
            self._load_jsonl()
        else:
            self._open_sqlite()
    def _open_sqlite(self):
        d = os.path.dirname(self.db_path)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        if not os.path.isdir(self.snp_dir):
            os.makedirs(self.snp_dir)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SQL_DDL)
        self._conn.commit()

    def _load_jsonl(self):
        self._records = []
        if not os.path.isfile(self.db_path):
            return
        try:
            with open(self.db_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        self._records.append(json.loads(line))
                    except ValueError:
                        continue
        except IOError:
            pass

    def _append_jsonl(self, rec):
        d = os.path.dirname(self.db_path)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with open(self.db_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")

    def close(self):
        if self._conn is not None:
            try:
                self._conn.commit()
                self._conn.close()
            except Exception:
                pass
            self._conn = None
    def put(self, record):
        if not record or not isinstance(record, dict):
            return None
        rec = dict(record)
        rec.setdefault(_F.SCHEMA_VER, SCHEMA_VERSION)
        rec.setdefault(_F.SCHEMA, RECORD_SCHEMA)
        rec.setdefault(_F.USABLE, True)
        rid = rec.get(_F.RECORD_ID) or uuid.uuid4().hex
        rec[_F.RECORD_ID] = rid
        if rec.get(_F.DISTANCE) is None:
            d, _per = distance_of(rec.get(_F.ACTUAL) or {},
                                  rec.get("target") or
                                  rec.get(_F.REQUEST) or {},
                                  rec.get("tolerances"))
            rec[_F.DISTANCE] = None if d == float("inf") else d
        old = self._find_idempotent(rid, rec.get(_F.SNP_SHA1))
        if old:
            return old
        if not self._json_mode and self._conn is not None:
            self._insert_sqlite(rec)
        else:
            self._records.append(rec)
            self._append_jsonl(rec)
        return rid

    def _find_idempotent(self, rid, sha):
        if self._json_mode:
            for r in self._records:
                if rid and r.get(_F.RECORD_ID) == rid:
                    return r.get(_F.RECORD_ID)
                if sha and r.get(_F.SNP_SHA1) == sha:
                    return r.get(_F.RECORD_ID)
            return None
        cur = self._conn.cursor()
        if rid:
            cur.execute("SELECT record_id FROM em_record WHERE record_id=?", (rid,))
            row = cur.fetchone()
            if row:
                return row[0]
        if sha:
            cur.execute(
                "SELECT record_id FROM em_record WHERE snp_sha1=? "
                "ORDER BY distance ASC LIMIT 1", (sha,))
            row = cur.fetchone()
            if row:
                return row[0]
        return None

    def _insert_sqlite(self, rec):
        cur = self._conn.cursor()
        cur.execute(
            "INSERT INTO em_record (record_id, schema_version, grid_key,"
            " device_type, tech, emx_cfg_hash, geom_json, geom_hash,"
            " snp_path, snp_sha1, actual_json, request_json, distance,"
            " usable, source, warns_json, grid_spec_json, em_meta_json,"
            " created_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rec.get(_F.RECORD_ID), int(rec.get(_F.SCHEMA_VER, 1)),
             rec.get(_F.GRID_KEY) or "",
             rec.get(_F.DEVICE) or "ind",
             rec.get(_F.TECH), rec.get(_F.CFG_HASH),
             json.dumps(rec.get(_F.GEOM) or {}, sort_keys=True),
             rec.get(_F.GEOM_HASH),
             rec.get(_F.SNP_PATH), rec.get(_F.SNP_SHA1),
             json.dumps(rec.get(_F.ACTUAL) or {}, sort_keys=True),
             json.dumps(rec.get(_F.REQUEST) or
                        rec.get("target") or {}, sort_keys=True),
             rec.get(_F.DISTANCE),
             1 if rec.get(_F.USABLE, True) else 0,
             rec.get(_F.SOURCE),
             json.dumps(rec.get(_F.WARNS) or [], ensure_ascii=False),
             json.dumps(rec.get(_F.GRID_SPEC) or {}, sort_keys=True),
             json.dumps(rec.get(_F.EM_META) or {}, ensure_ascii=False),
             rec.get("created_utc") or
             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        self._conn.commit()
    def get_by_key(self, grid_key):
        if not grid_key:
            return None
        if self._json_mode:
            best, best_d = None, None
            for r in self._records:
                if r.get(_F.GRID_KEY) != grid_key:
                    continue
                if not r.get(_F.USABLE, True):
                    continue
                d = r.get(_F.DISTANCE)
                dv = float(d) if isinstance(d, (int, float)) else float("inf")
                if best is None or dv < best_d:
                    best, best_d = r, dv
            return best
        cur = self._conn.cursor()
        cur.execute(
            "SELECT * FROM em_record WHERE grid_key=? AND usable=1 "
            "ORDER BY distance ASC LIMIT 1", (grid_key,))
        row = cur.fetchone()
        return self._row_to_rec(row) if row else None

    def _row_to_rec(self, row):
        if row is None:
            return None
        d = dict(row)
        for jf, key in (("geom_json", _F.GEOM), ("actual_json", _F.ACTUAL),
                        ("request_json", _F.REQUEST),
                        ("warns_json", _F.WARNS),
                        ("grid_spec_json", _F.GRID_SPEC),
                        ("em_meta_json", _F.EM_META)):
            raw = d.pop(jf, None)
            if raw:
                try:
                    d[key] = json.loads(raw)
                except ValueError:
                    d[key] = raw
        d[_F.USABLE] = bool(d.get("usable", 1))
        return d
    def nearest(self, device_type, target_fingerprint, k=5, tech=None,
                emx_cfg_hash=None, tolerances=None, f0=None):
        target_fingerprint = target_fingerprint or {}
        tols = tolerances or DEFAULT_TOL
        f0 = f0 if f0 is not None else target_fingerprint.get("f0")
        out = []
        for rec in self._iter_records(device_type, tech, emx_cfg_hash):
            ac = rec.get(_F.ACTUAL) or {}
            ok, d = fingerprint_match(target_fingerprint, ac, tols, f0=f0)
            if d == float("inf"):
                continue
            item = dict(rec)
            item[_F.DISTANCE] = d
            out.append(item)
        out.sort(key=lambda r: r.get(_F.DISTANCE, 99.0))
        return out[: max(1, int(k))]

    def _iter_records(self, device_type=None, tech=None, cfg_hash=None):
        if self._json_mode:
            for r in self._records:
                if not r.get(_F.USABLE, True):
                    continue
                if device_type and r.get(_F.DEVICE) not in (None, device_type):
                    continue
                if tech and r.get(_F.TECH) and r.get(_F.TECH) != tech:
                    continue
                if cfg_hash and r.get(_F.CFG_HASH) and r.get(_F.CFG_HASH) != cfg_hash:
                    continue
                yield r
            return
        cur = self._conn.cursor()
        q = "SELECT * FROM em_record WHERE usable=1"
        args = []
        if device_type:
            q += " AND device_type=?"
            args.append(device_type)
        if tech:
            q += " AND (tech IS NULL OR tech=?)"
            args.append(tech)
        if cfg_hash:
            q += " AND (emx_cfg_hash IS NULL OR emx_cfg_hash=?)"
            args.append(cfg_hash)
        cur.execute(q, args)
        for row in cur.fetchall():
            rec = self._row_to_rec(row)
            if rec:
                yield rec
    def query(self, device_type, target_fingerprint, tolerances=None,
              tech=None, emx_cfg_hash=None, constraints=None, f0=None,
              grid_spec=None):
        try:
            target_fingerprint = dict(target_fingerprint or {})
            if not target_fingerprint:
                raise ValueError("query: empty target_fingerprint")
            tols = dict(tolerances or DEFAULT_TOL)
            f0 = _as_hz(f0 if f0 is not None else target_fingerprint.get("f0"))
            gspec = grid_spec or DEFAULT_GRID_SPEC
            validate_grid_spec(gspec, tols)
            if constraints:
                if "SRF_min" in constraints:
                    target_fingerprint.setdefault(
                        "band_hi", float(constraints["SRF_min"]) /
                        float(tols.get("srf_margin", DEFAULT_SRF_MARGIN)))
                if "Q_min" in constraints:
                    tols.setdefault("Q_min", float(constraints["Q_min"]))
            clip_events = []
            region = self.achievable_region(device_type)
            if region:
                target_fingerprint, clip_events = clip_to_region(
                    target_fingerprint, region, device_type=device_type)
            gkey = grid_key_of(device_type, target_fingerprint, gspec)
            rec = self.get_by_key(gkey)
            if rec is not None:
                ok, d = fingerprint_match(target_fingerprint, rec.get(_F.ACTUAL) or {}, tols, f0=f0)
                if ok:
                    return self._qresult("hit_grid", rec, d, target_fingerprint,
                                         tols, gkey, gspec, f0,
                                         clip_events=clip_events)
            hits = self.nearest(device_type, target_fingerprint, k=1,
                                tech=tech, emx_cfg_hash=emx_cfg_hash,
                                tolerances=tols, f0=f0)
            if hits:
                rec2 = hits[0]
                d2 = rec2.get(_F.DISTANCE, 99.0)
                status = "hit_nearest" if d2 <= 1.0 else "best_effort"
                return self._qresult(status, rec2, d2, target_fingerprint,
                                     tols, rec2.get(_F.GRID_KEY) or gkey,
                                     rec2.get(_F.GRID_SPEC) or gspec, f0,
                                     clip_events=clip_events)
            return self._qresult("miss", None, None, target_fingerprint, tols,
                                 gkey, gspec, f0, clip_events=clip_events)
        except Exception as exc:
            out = _empty_result("error", dict(target_fingerprint or {}),
                                ["query error: %s" % exc], message=str(exc))
            out.update({_F.GRID_KEY: None, _F.SNP_PATH: None, _F.SOURCE: "error"})
            return out

    def _qresult(self, status, rec, distance, request_fp, tols, gkey, gspec,
                 f0, clip_events=None, stage=None):
        ac = (rec or {}).get(_F.ACTUAL)
        delta = delta_vs_request_of(ac, request_fp, f0=f0) if ac else {}
        warns = list((rec or {}).get(_F.WARNS) or [])
        if ac and distance is not None and distance > 1.0:
            warns.append("best_effort distance=%.4f>1" % float(distance))
        for ev in (clip_events or []):
            warns.append("clipped: %s" % ev)
        dev = (rec or {}).get(_F.DEVICE) or (request_fp or {}).get("kind")
        srep = None
        if ac:
            srep = stage_report(ac, request_fp, tols, f0=f0, device_type=dev)
        return {
            _F.STATUS: status,
            _F.REQUEST: dict(request_fp or {}),
            _F.ACTUAL: ac,
            _F.DELIVERED: delivered_of((rec or {}).get(_F.DEVICE), ac),
            _F.DELTA: delta,
            _F.DISTANCE: distance,
            _F.GRID_KEY: gkey,
            _F.GRID_SPEC: gspec,
            _F.SNP_PATH: (rec or {}).get(_F.SNP_PATH),
            _F.SOURCE: (rec or {}).get(_F.SOURCE) if rec else "em_opt",
            _F.WARNS: warns,
            "stage_in_tol": srep,
            "clipped": bool(clip_events),
            "record": rec,
        }
    def achievable_region(self, device_type):
        dev = (device_type or "ind").lower()
        keys = _DELIVERED_KEYS.get(dev, ("L0",))
        vals = dict((k, []) for k in keys)
        n = 0
        for rec in self._iter_records(device_type):
            ac = rec.get(_F.ACTUAL) or {}
            hit = False
            for k in keys:
                v = ac.get(k)
                if isinstance(v, (int, float)) and math.isfinite(float(v)):
                    vals[k].append(float(v))
                    hit = True
            if hit:
                n += 1
        if n < 5:
            return None
        out = {}
        for k, vs in vals.items():
            if vs:
                out[k] = (min(vs), max(vs))
        return out or None
    def register_snp(self, src_path):
        if not src_path or not os.path.isfile(src_path):
            return None
        sha = _sha1_file(src_path)
        ext = os.path.splitext(src_path)[1] or ".sNp"
        if not os.path.isdir(self.snp_dir):
            os.makedirs(self.snp_dir)
        dst = os.path.join(self.snp_dir, "%s%s" % (sha[:16], ext))
        if not os.path.isfile(dst):
            import shutil
            shutil.copyfile(src_path, dst)
        return sha, dst

class EmBridge(object):
    def __init__(self, cfg, delegate=None, em_optimizer=None, library=None):
        self.cfg = cfg or {}
        eb = self.cfg.get("em_bridge") or {}
        self.eb = eb
        self.delegate = delegate
        self.em_optimizer = em_optimizer
        lib_path = eb.get("library") or os.environ.get("LIBRF_EM_LIB") or \
            os.path.join(os.getcwd(), "work", "em_lib")
        self.library = library or EmLibrary(lib_path)
        self.fallback = eb.get("fallback", "actual")
        self.mode = eb.get("mode", "track")
        self._last_geom = {}
    def resolve(self, request):
        req = request or {}
        try:
            dev = req.get(_F.DEVICE) or req.get("type") or "ind"
            target = req.get("target") or req.get(_F.REQUEST) or {}
            tols = req.get("tolerances") or req.get("tol") or DEFAULT_TOL
            tech = req.get(_F.TECH)
            cfg_hash = req.get(_F.CFG_HASH)
            gspec = req.get(_F.GRID_SPEC) or DEFAULT_GRID_SPEC
            f0 = _as_hz(req.get("f_op") or req.get("f0") or target.get("f0"))
            qr = self.library.query(dev, target, tolerances=tols, tech=tech,
                                    emx_cfg_hash=cfg_hash,
                                    constraints=req.get("constraints"),
                                    f0=f0, grid_spec=gspec)
            if qr.get(_F.STATUS) in ("hit_grid", "hit_nearest"):
                self._track_geom(dev, qr)
                return qr
            if qr.get(_F.STATUS) == "error":
                return qr
            if qr.get(_F.STATUS) == "best_effort" and self.fallback == "actual":
                self._track_geom(dev, qr)
                return qr
            if self.em_optimizer is None:
                return qr
            return self._solve(req, dev, target, tols, tech, cfg_hash, gspec, f0)
        except Exception as exc:
            return _empty_result("error", req.get("target") or {},
                                 ["resolve error: %s" % exc],
                                 message=str(exc))

    def _solve(self, req, dev, target, tols, tech, cfg_hash, gspec, f0):
        prior = req.get("geom_prior") or {}
        if self.mode == "track" and not prior and dev in self._last_geom:
            prior = self._last_geom[dev]
        sreq = dict(req)
        sreq.setdefault("device_type", dev)
        if sreq.get("f_op") is None and f0 is not None:
            sreq["f_op"] = f0
        sreq["geom_prior"] = prior
        sreq["budget"] = dict(req.get("budget") or {})
        try:
            entry = self.em_optimizer.solve(sreq)
        except Exception as exc:
            return _empty_result("infra_fail", target, ["em_opt: %s" % exc], message=str(exc))
        if not entry:
            return _empty_result("miss", target, ["em_opt empty"])
        rec = dict(entry)
        if rec.get(_F.STATUS) in ("error", "miss") and not rec.get(_F.ACTUAL):
            return _empty_result(rec.get(_F.STATUS), target,
                                 list(rec.get(_F.WARNS) or []),
                                 message=rec.get("reason") or rec.get(_F.STATUS))
        rec.setdefault(_F.DEVICE, dev)
        rec.setdefault(_F.TECH, tech)
        rec.setdefault(_F.CFG_HASH, cfg_hash)
        rec.setdefault(_F.GRID_SPEC, gspec)
        rec.setdefault(_F.GRID_KEY, grid_key_of(dev, target, gspec))
        rec[_F.REQUEST] = dict(target)
        rec.setdefault(_F.USABLE, True)
        rec.setdefault(_F.SOURCE, "optimization")
        snp_src = rec.get(_F.SNP_PATH) or rec.get("snp")
        if snp_src:
            try:
                reg = self.library.register_snp(snp_src)
            except Exception as exc:
                reg = None
                rec.setdefault(_F.WARNS, []).append("snp: %s" % exc)
            if reg:
                rec[_F.SNP_SHA1], rec[_F.SNP_PATH] = reg
            else:
                rec[_F.SNP_PATH] = snp_src
        try:
            rid = self.library.put(rec)
            rec[_F.RECORD_ID] = rid
        except Exception as exc:
            rec.setdefault(_F.WARNS, []).append("put: %s" % exc)
        ac = rec.get(_F.ACTUAL) or {}
        d, _per = distance_of(ac, target, tols, f0=f0)
        status = "hit_nearest" if d <= 1.0 else "best_effort"
        if rec.get(_F.USABLE) is False:
            status = "infra_fail"
        self._track_geom(dev, {"record": rec})
        return {
            _F.STATUS: status,
            _F.REQUEST: dict(target),
            _F.ACTUAL: ac,
            _F.DELIVERED: delivered_of(dev, ac),
            _F.DELTA: delta_vs_request_of(ac, target, f0=f0),
            _F.DISTANCE: (None if d == float("inf") else d),
            _F.GRID_KEY: rec.get(_F.GRID_KEY),
            _F.GRID_SPEC: gspec,
            _F.SNP_PATH: rec.get(_F.SNP_PATH),
            _F.SOURCE: rec.get(_F.SOURCE),
            _F.WARNS: rec.get(_F.WARNS) or [],
            "record": rec,
        }

    def _track_geom(self, dev, qr):
        g = ((qr or {}).get("record") or {}).get(_F.GEOM)
        if g:
            self._last_geom[dev] = dict(g)
    def run(self, values, iter_dir):
        if self.delegate is None:
            raise RuntimeError("EmBridge.run: delegate 未注入")
        em_actual = {}
        values_actual = dict(values or {})
        em_opt_cfg = self.eb.get("em_opt") or {}
        em_budget = em_opt_cfg.get("budget") or em_opt_cfg
        for dev_cfg in (self.eb.get("devices") or []):
            var = dev_cfg.get("target_var") or dev_cfg.get("var_prefix")
            if not var or var not in (values or {}):
                continue
            metric = dev_cfg.get("metric") or "L0"
            target = {metric: values[var]}
            try:
                f_op = _as_hz(dev_cfg.get("f_op") or dev_cfg.get("f0"))
            except (TypeError, ValueError) as exc:
                em_actual[var] = {
                    "requested": values[var], "target": target,
                    _F.STATUS: "error", _F.DISTANCE: None,
                    _F.SNP_PATH: None, "snp": None, "record": None,
                    "source": "error", "weak_hit": False, "clipped": False,
                    "warns": ["f_op 解析失败: %s" % exc],
                }
                continue
            req = {
                "schema": REQUEST_SCHEMA,
                _F.DEVICE: dev_cfg.get(_F.DEVICE, "ind"),
                "target": target,
                "tolerances": dev_cfg.get("tolerances") or dev_cfg.get("tol")
                or DEFAULT_TOL,
                "f_op": f_op,
                _F.TECH: dev_cfg.get(_F.TECH),
                _F.CFG_HASH: dev_cfg.get(_F.CFG_HASH),
                _F.GRID_SPEC: dev_cfg.get(_F.GRID_SPEC) or DEFAULT_GRID_SPEC,
                "constraints": dev_cfg.get("constraints"),
                "budget": em_budget,
            }
            qr = self.resolve(req)
            delivered = qr.get(_F.DELIVERED)
            if isinstance(delivered, (int, float)) and math.isfinite(float(delivered)):
                values_actual[var] = float(delivered)
            elif isinstance(delivered, dict) and metric in delivered:
                dv = delivered[metric]
                if isinstance(dv, (int, float)) and math.isfinite(float(dv)):
                    values_actual[var] = float(dv)
            em_actual[var] = {
                "requested": target.get(metric),
                "target": dict(target),
                _F.STATUS: qr.get(_F.STATUS),
                _F.GRID_KEY: qr.get(_F.GRID_KEY),
                _F.DELIVERED: delivered,
                _F.ACTUAL: qr.get(_F.ACTUAL),
                _F.DELTA: qr.get(_F.DELTA),
                _F.DISTANCE: qr.get(_F.DISTANCE),
                _F.SNP_PATH: qr.get(_F.SNP_PATH),
                "snp": qr.get(_F.SNP_PATH),
                "record": qr.get("record"),
                "source": qr.get(_F.SOURCE),
                "weak_hit": bool(qr.get(_F.DISTANCE) is not None and
                                 float(qr.get(_F.DISTANCE)) > 1.0),
                "clipped": bool(qr.get("clipped")),
            }
        result = self.delegate.run(values_actual, iter_dir)
        try:
            result.em_actual = em_actual
        except Exception:
            pass
        return result

    def terminate(self):
        if self.em_optimizer is not None and hasattr(self.em_optimizer, "terminate"):
            try:
                self.em_optimizer.terminate()
            except Exception:
                pass
        self.library.close()
