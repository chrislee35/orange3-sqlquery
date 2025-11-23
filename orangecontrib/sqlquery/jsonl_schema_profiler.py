"""
jsonl_schema_profiler.py

Stream a set of JSON Lines files and produce a schema/profile describing:
- presence frequency per field
- type distribution per field (string, number, boolean, array, hash, null, unknown)
- string stats and enum detection
- numeric stats (min, max, mean, mode)
- array stats (length distribution, element type distribution, nested profiling)
- nested hash profiling

Usage (CLI):
    python jsonl_schema_profiler.py input1.jsonl input2.jsonl -o report.json

API:
    from jsonl_schema_profiler import SchemaProfiler
    p = SchemaProfiler()
    p.process_file("file.jsonl")
    report = p.report()
"""

from __future__ import annotations
import sys
import os
import orjson
import math
import argparse
from collections import Counter, defaultdict
from typing import Any, Dict, Optional, Tuple, Iterable, Set, Generator
import pandas as pd
from fast_json_normalize import fast_json_normalize # this dropped the json normalization time by 21%

# ------------------------
# Utilities / running stats
# ------------------------

class RunningStat:
    """Online calculation of mean, variance (Welford)."""
    def __init__(self):
        self.n = 0
        self.mean = 0.0
        self.m2 = 0.0
        self.min = float("inf")
        self.max = float("-inf")

    def push(self, x: float):
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        self.m2 += delta * (x - self.mean)
        if x < self.min: self.min = x
        if x > self.max: self.max = x

    def summary(self) -> Dict[str, Optional[float]]:
        if self.n == 0:
            return {"count": 0, "mean": None, "min": None, "max": None, "variance": None}
        variance = self.m2 / (self.n - 1) if self.n > 1 else 0.0
        return {"count": self.n, "mean": self.mean, "min": self.min, "max": self.max, "variance": variance}

# ------------------------
# FieldStats
# ------------------------

class FieldStats:
    """Holds stats for a single field path."""
    def __init__(self,
                 cap_unique_strings: int = 1000,
                 cap_unique_numbers: int = 1000):
        self.present_count = 0  # number of records where field appears
        self.types = Counter()  # counts of types encountered, keys: 'string','number','boolean','array','hash','null','unknown'
        # string-specific
        self._string_values = Counter()
        self._unique_string_cap = cap_unique_strings
        self.total_string_length = 0
        self.string_samples = 0  # number of string values observed (for average length)
        # number-specific
        self.num_stat = RunningStat()
        self._num_freq = Counter()
        self._unique_num_cap = cap_unique_numbers
        # array-specific
        self.array_length_stat = RunningStat()
        self.array_elem_types = Counter()  # element type distribution across all arrays for this field
        # hash-specific (children fields)
        self.children: Dict[str, FieldStats] = {}  # subfield -> FieldStats

    def record_string(self, s: str):
        self.total_string_length += len(s)
        self.string_samples += 1
        if len(self._string_values) < self._unique_string_cap:
            self._string_values[s] += 1

    def record_number(self, v: float):
        self.num_stat.push(v)
        if len(self._num_freq) < self._unique_num_cap:
            # store raw numeric values for mode; careful with floats (may be many distinct)
            self._num_freq[v] += 1

    def record_array_length(self, L: int):
        self.array_length_stat.push(L)

    def add_child_if_missing(self, name: str) -> 'FieldStats':
        if name not in self.children:
            self.children[name] = FieldStats(self._unique_string_cap, self._unique_num_cap)
        return self.children[name]

    # export helpers
    def _top_n(self, counter: Counter, n: int = 10) -> list:
        return counter.most_common(n)

    def summarize(self, total_records: int, enum_short_len: int = 20, enum_unique_threshold: int = 50, enum_frequency_threshold: int = 0.25) -> Dict[str, Any]:
        """
        Create a JSON-serializable summary of this FieldStats.
        total_records: used to calculate presence fraction
        enum_short_len: max average length for strings to be considered enum-like
        enum_unique_threshold: max unique values for enum-like
        enum_frequency_threshold: fraction of total samples covered by top values to be considered enum-like
        """
        out: Dict[str, Any] = {}
        out["present_count"] = self.present_count
        out["presence_fraction"] = (self.present_count / total_records) if total_records > 0 else None
        out["types"] = dict(self.types)

        # strings
        if self.types.get("string", 0) > 0:
            unique_count = len(self._string_values)
            top = self._top_n(self._string_values, 20)
            avg_len = (self.total_string_length / self.string_samples) if self.string_samples > 0 else None
            top_cover = sum(c for (_, c) in top[:5]) / self.string_samples if self.string_samples > 0 else 0.0
            enum_like = (unique_count <= enum_unique_threshold and (avg_len is not None and avg_len <= enum_short_len) and top_cover >= enum_frequency_threshold)
            out["string"] = {
                "samples": self.string_samples,
                "unique_tracked": unique_count,
                "avg_length": avg_len,
                "top_values": top,
                "enum_like": enum_like
            }

        # numbers
        if self.types.get("number", 0) > 0:
            numsum = self.num_stat.summary()
            mode = self._num_freq.most_common(5)
            out["number"] = {
                "count": numsum["count"],
                "min": numsum["min"],
                "max": numsum["max"],
                "mean": numsum["mean"],
                "variance": numsum["variance"],
                "mode_candidates": mode
            }

        # boolean / null
        if self.types.get("boolean", 0) > 0:
            out["boolean"] = {"count": self.types["boolean"]}
        if self.types.get("null", 0) > 0:
            out["null"] = {"count": self.types["null"]}

        # arrays
        if self.types.get("array", 0) > 0:
            al = self.array_length_stat.summary()
            out["array"] = {
                "count": self.types["array"],
                "length": {
                    "count": al["count"],
                    "min": al["min"],
                    "max": al["max"],
                    "mean": al["mean"],
                    "variance": al["variance"],
                },
                "element_types": dict(self.array_elem_types)
            }
            # if there are child stats for array elements, include them under 'children' (they are keyed by '[]' and subsequent field names)
        # hashes / children
        if self.children:
            out["children"] = {k: v.summarize(total_records, enum_short_len, enum_unique_threshold, enum_frequency_threshold) for k, v in self.children.items()}

        return out

# ------------------------
# SchemaProfiler
# ------------------------

def classify_value(v: Any) -> str:
    """Classify Python value to one of: string, number, boolean, array, hash, null, unknown"""
    if v is None:
        return "null"
    if isinstance(v, bool):
        # bool is instance of int in Python; check before number
        return "boolean"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "hash"
    return "unknown"

class SchemaProfiler:
    """
    Main profiler class.
    Usage:
        p = SchemaProfiler()
        p.process_file("some.jsonl")
        p.process_file("another.jsonl")
        report = p.report()
    """
    def __init__(self,
                 cap_unique_strings: int = 1000,
                 cap_unique_numbers: int = 1000,
                 enum_short_len: int = 20,
                 enum_unique_threshold: int = 50,
                 enum_frequency_threshold: float = 0.25):
        self.fields: Dict[str, FieldStats] = {}
        self.total_records = 0
        self.cap_unique_strings = cap_unique_strings
        self.cap_unique_numbers = cap_unique_numbers
        # enum heuristics
        self.enum_short_len = enum_short_len
        self.enum_unique_threshold = enum_unique_threshold
        self.enum_frequency_threshold = enum_frequency_threshold
        self.dataframe: Optional[pd.DataFrame] = None

    def _get_field(self, path: str) -> FieldStats:
        if path not in self.fields:
            self.fields[path] = FieldStats(self.cap_unique_strings, self.cap_unique_numbers)
        return self.fields[path]

    def process_record(self, obj: Any):
        """
        Process one JSON object (a single line).
        Only top-level dict objects are fully inspected. If a non-dict is passed, it still counts as a record.
        """
        self.total_records += 1
        if isinstance(obj, dict):
            # start recursive walk
            self._walk_dict(obj, prefix="")
        else:
            # handle non-dict root (rare in JSONL)
            path = "<root>"
            fs = self._get_field(path)
            fs.present_count += 1
            t = classify_value(obj)
            fs.types[t] += 1
            if t == "string":
                fs.record_string(obj)
            elif t == "number":
                fs.record_number(float(obj))

    def _walk_dict(self, d: Dict[str, Any], prefix: str):
        """
        Walk keys in dict d. prefix is the current path string ('' for root).
        Field path format examples:
            - 'user'
            - 'user.name'
            - 'items[]'  (array itself)
            - 'items[].price' (field inside array elements that are objects)
        """
        for k, v in d.items():
            path = f"{prefix}.{k}" if prefix else k
            fs = self._get_field(path)
            fs.present_count += 1
            t = classify_value(v)
            fs.types[t] += 1

            if t == "string":
                # string stats
                # Attempt to coerce to real string
                try:
                    s = str(v)
                except Exception:
                    s = ""
                fs.record_string(s)

            elif t == "number":
                try:
                    num = float(v)
                except Exception:
                    # if cast fails, mark as unknown
                    fs.types["unknown"] += 1
                else:
                    fs.record_number(num)

            elif t == "boolean":
                # boolean counts are in types already
                pass

            elif t == "null":
                pass

            elif t == "array":
                # array-level stats
                arr: list = v
                fs.record_array_length(len(arr))
                # element type distribution
                for elem in arr:
                    et = classify_value(elem)
                    fs.array_elem_types[et] += 1
                # if array contains dicts, profile their internal fields under path + '[]'
                # we'll use 'path[]' as the path for elements, and nested fields will be 'path[].child'
                elem_path_base = path + "[]"
                # also consider arrays of primitives: we optionally profile the primitive values at elem_path_base as repeated values
                # if we see dict elements, recurse into each dict with elem_path_base as prefix
                for elem in arr:
                    if isinstance(elem, dict):
                        self._walk_into(elem, elem_path_base)
            elif t == "hash":
                # nested dict - dive into children with path as prefix
                self._walk_into(v, path)
            else:
                # unknown types ignored beyond counting
                pass

    def _walk_into(self, obj: Dict[str, Any], prefix: str):
        """Walk into a nested dict or array-element placeholder prefix"""
        if not isinstance(obj, dict):
            return
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else k
            fs = self._get_field(path)
            fs.present_count += 1
            t = classify_value(v)
            fs.types[t] += 1

            if t == "string":
                try:
                    s = str(v)
                except Exception:
                    s = ""
                fs.record_string(s)
            elif t == "number":
                try:
                    num = float(v)
                except Exception:
                    fs.types["unknown"] += 1
                else:
                    fs.record_number(num)
            elif t == "array":
                arr: list = v
                fs.record_array_length(len(arr))
                for elem in arr:
                    et = classify_value(elem)
                    fs.array_elem_types[et] += 1
                # if array elements are dicts, recurse further using same convention
                elem_path_base = path + "[]"
                for elem in arr:
                    if isinstance(elem, dict):
                        self._walk_into(elem, elem_path_base)
            elif t == "hash":
                self._walk_into(v, path)
            else:
                pass

    def make_sparse_df(self, df):
        sparse_df = pd.DataFrame()

        for col in df.columns:
            s = df[col]
            
            # Numeric
            if pd.api.types.is_numeric_dtype(s):
                fill = 0
                sparse_df[col] = pd.arrays.SparseArray(s.fillna(fill), fill_value=fill)
            
            # Boolean
            elif pd.api.types.is_bool_dtype(s):
                fill = False
                sparse_df[col] = pd.arrays.SparseArray(s.fillna(fill), fill_value=fill)
            
            # Strings / objects
            else:
                fill = None
                sparse_df[col] = pd.arrays.SparseArray(s.where(s.notna(), fill), fill_value=fill)

        return sparse_df

    def process_file(self, filename: str, encoding: str = "utf-8", errors: str = "strict") -> Generator[float, None, None]:
        """
        Stream a JSON Lines file (one JSON object per line).
        Non-JSON or empty lines are ignored with a warning printed to stderr.
        """
        filesize = os.path.getsize(filename)
        processed = 0
        # initialize a sparse dataframe
        df = pd.DataFrame()
        data = []
        import time
        timing = {
            'json': 0.0,
            'proc': 0.0,
            'norm': 0.0,
            'concat': 0.0,
            'sparse': 0.0
        }
        with open(filename, "r", encoding=encoding, errors=errors) as fh:
            for lineno, line in enumerate(fh, start=1):
                processed += len(line)
                yield processed / filesize
                line = line.strip()
                if not line:
                    continue
                try:
                    st = time.time()
                    obj = orjson.loads(line)
                    et = time.time()
                    timing['json'] += et - st
                    self.process_record(obj)
                    st = time.time()
                    timing['proc'] += st - et
                    data.append(obj)
                    if lineno % 50000 == 0:
                        st = time.time()
                        df = fast_json_normalize(data)
                        et = time.time()
                        timing['norm'] += et - st
                        sdf = self.make_sparse_df(df)
                        st = time.time()
                        timing['sparse'] += st - et
                        df = pd.concat([df, sdf], axis=0)
                        et = time.time()
                        timing['concat'] += et - st
                        data = []
                except Exception as e:
                    # Skip bad lines, but don't crash (print to stderr)
                    print(f"Warning: skip invalid JSON in {filename}:{lineno}: {e}", file=sys.stderr)
                    continue
            if len(data) > 0:
                df = pd.json_normalize(data)
                sdf = self.make_sparse_df(df)
                df = pd.concat([df, sdf], axis=0)
        print(timing)
        self.dataframe = df

    def process_files(self, filenames: Iterable[str]):
        for fn in filenames:
            [x for x in self.process_file(fn)]

    def report(self) -> Dict[str, Any]:
        """
        Build a JSON-serializable report for all fields.
        Note: missing count per field is computed as total_records - present_count (if positive).
        """
        out = {
            "total_records": self.total_records,
            "fields": {}
        }
        for path, fs in sorted(self.fields.items()):
            s = fs.summarize(self.total_records, self.enum_short_len, self.enum_unique_threshold, self.enum_frequency_threshold)
            # compute missing
            missing = max(0, self.total_records - fs.present_count)
            s["missing_count"] = missing
            out["fields"][path] = s
        return out

    def dump_report(self, outpath: Optional[str] = None, indent: Optional[int] = 2):
        rep = self.report()
        if outpath:
            with open(outpath, "w", encoding="utf-8") as fh:
                json.dump(rep, fh, indent=indent, ensure_ascii=False)
        else:
            print(json.dumps(rep, indent=indent, ensure_ascii=False))

# ------------------------
# CLI
# ------------------------

def main():
    parser = argparse.ArgumentParser(description="Profile JSON Lines files and produce a schema summary.")
    parser.add_argument("inputs", nargs="+", help="Input JSONL files (one JSON object per line).")
    parser.add_argument("-o", "--output", help="Output JSON file for the report. If omitted, prints to stdout.")
    parser.add_argument("--cap-strings", type=int, default=1000, help="Cap of unique string values to retain per field (default 1000).")
    parser.add_argument("--cap-numbers", type=int, default=1000, help="Cap of unique numeric values to retain per field (default 1000).")
    parser.add_argument("--enum-short-len", type=int, default=20, help="Max average string length to consider enum-like.")
    parser.add_argument("--enum-unique-threshold", type=int, default=50, help="Max unique values to consider enum-like.")
    parser.add_argument("--enum-top-cover", type=float, default=0.25, help="Top-k cover fraction (k=5) to help consider enum-like (0..1).")
    args = parser.parse_args()

    profiler = SchemaProfiler(cap_unique_strings=args.cap_strings,
                              cap_unique_numbers=args.cap_numbers,
                              enum_short_len=args.enum_short_len,
                              enum_unique_threshold=args.enum_unique_threshold,
                              enum_frequency_threshold=args.enum_top_cover)
    for fn in args.inputs:
        profiler.process_file(fn)
    profiler.dump_report(args.output)

if __name__ == "__main__":
    main()
