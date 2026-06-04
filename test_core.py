#!/usr/bin/env python3
"""
test_core.py — dependency-free tests for the L5X UDT optimizer core.

Run:  python test_core.py        (exit code 0 = all passed, 1 = a failure)

These are NOT a web-app harness. They call the exact same functions the web app
calls (optimize_and_regenerate_udt / optimize_full_program_l5x) with known inputs
and assert the invariants that matter most for a tool that rewrites engineering
source:

  * No input member is ever silently dropped from the output.
  * A UDT whose members are ALL unresolved is reported N/A (no file), never a
    partial file missing a member.
  * Nested UDTs are embedded so a single-UDT export is self-contained.
  * Output is well-formed XML and ordering is deterministic.
  * Full-program optimisation preserves every member and everything outside
    <DataTypes>.

The member-dropping regression that prompted this file is guarded by
test_partial_unknown_member_is_preserved().
"""

import os
import re
import sys
import glob
import xml.etree.ElementTree as ET

import logging
logging.disable(logging.CRITICAL)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from L5XOpt_UDT import (                       # noqa: E402
    extract_all_udt_definitions,
    optimize_and_regenerate_udt,
    optimize_full_program_l5x,
    _topological_sort,
    _estimate_udt_size,
)

# ── helpers ───────────────────────────────────────────────────────────────────

def _sample(pattern):
    hits = glob.glob(os.path.join(HERE, "Sample l5x files", pattern)) \
        or glob.glob(os.path.join(HERE, "**", pattern), recursive=True)
    return hits[0] if hits else None


def _read(path):
    with open(path, encoding="utf-8-sig") as f:
        return f.read()


def _visible_members(l5x_text, udt_name):
    """Visible member names of one DataType (excludes hidden ZZZZ backing fields)."""
    m = re.search(rf'<DataType[^>]*Name="{re.escape(udt_name)}".*?</DataType>', l5x_text, re.S)
    if not m:
        return None
    return [n for n in re.findall(r'<Member Name="([^"]+)"', m.group(0))
            if not n.startswith("ZZZZ")]


def _optimize_single(l5x_text):
    """Mirror the web app's single-UDT path: parse, size, optimise + embed."""
    p = extract_all_udt_definitions(l5x_text)
    if "error" in p:
        return p
    au = p["udts"]; aoi = p.get("aoi_registry", {}); tgt = p["target"]
    reg = {}
    for n in _topological_sort(au):
        reg[n] = _estimate_udt_size(au[n], reg, aoi)
    return optimize_and_regenerate_udt(
        au[tgt], all_udts=au, udt_size_registry=reg, aoi_registry=aoi,
        aoi_context_xml=p.get("aoi_context_xml"), embed_nested_context=True,
    )


def _single_export(name, members_xml, context_xml=""):
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<RSLogix5000Content SchemaRevision="1.0" SoftwareRevision="32.04" '
        f'TargetName="{name}" TargetType="DataType" ContainsContext="true" '
        f'ExportDate="x" ExportOptions="">\n'
        f'<Controller Use="Context" Name="C"><DataTypes Use="Context">\n'
        f'<DataType Use="Target" Name="{name}" Family="NoFamily" Class="User">'
        f'<Members>{members_xml}</Members></DataType>\n{context_xml}\n'
        f'</DataTypes></Controller></RSLogix5000Content>'
    )


def _member(name, dtype, dim=0, radix="Decimal"):
    return (f'<Member Name="{name}" DataType="{dtype}" Dimension="{dim}" '
            f'Radix="{radix}" Hidden="false" ExternalAccess="Read/Write"/>')


# ── tests ──────────────────────────────────────────────────────────────────────

def test_partial_unknown_member_is_preserved():
    """REGRESSION GUARD: an unresolved-type member among resolvable ones must
    NOT be dropped from the output."""
    l5x = _single_export("T", "".join([
        _member("a", "DINT"),
        _member("mystery", "raC_Tec_Lib", radix="NullType"),  # type not in file
        _member("b", "REAL", radix="Float"),
    ]))
    res = _optimize_single(l5x)
    assert res.get("success"), f"expected success, got {res.get('error')}"
    out = _visible_members(res["udt_text"], "T")
    assert out is not None, "target DataType missing from output"
    assert set(out) == {"a", "mystery", "b"}, \
        f"member dropped/added — input a,mystery,b -> output {out}"


def test_all_unknown_is_na_not_partial_file():
    """A UDT whose members are ALL unresolved must be reported N/A (no file),
    never a partial output."""
    l5x = _single_export("AllBad", "".join([
        _member("x", "raC_Tec_Foo", radix="NullType"),
        _member("y", "raC_Tec_Bar", radix="NullType"),
    ]))
    res = _optimize_single(l5x)
    assert res.get("success") is False, "all-unknown UDT should not succeed"
    assert res.get("optimization_needed") is None, "all-unknown should be N/A"


def test_native_members_never_dropped():
    """Every native/array member survives optimisation, and BOOLs still pack."""
    l5x = _single_export("Mix", "".join([
        _member("d1", "DINT"), _member("b1", "BOOL"), _member("r1", "REAL", radix="Float"),
        _member("i1", "INT"), _member("arr", "BOOL", dim=32), _member("b2", "BOOL"),
        _member("s1", "SINT"), _member("t1", "TIMER", radix="NullType"),
    ]))
    res = _optimize_single(l5x)
    assert res.get("success"), res.get("error")
    out = set(_visible_members(res["udt_text"], "Mix"))
    assert out == {"d1", "b1", "r1", "i1", "arr", "b2", "s1", "t1"}, f"members changed: {out}"


def test_output_is_wellformed_and_deterministic():
    l5x = _single_export("Det", "".join([
        _member("z", "DINT"), _member("a", "DINT"), _member("m", "REAL", radix="Float"),
    ]))
    r1 = _optimize_single(l5x); r2 = _optimize_single(l5x)
    assert r1.get("success") and r2.get("success")
    ET.fromstring(r1["udt_text"])                      # must parse
    assert r1["udt_text"] == r2["udt_text"], "optimisation is not deterministic"


def test_nested_sample_is_self_contained():
    path = _sample("*Nested*.L5X") or _sample("*nested*.L5X")
    if not path:
        print("    (skip) no nested sample file found")
        return
    res = _optimize_single(_read(path))
    assert res.get("success"), res.get("error")
    root = ET.fromstring(res["udt_text"])
    names = [d.get("Name") for d in root.findall(".//DataType")]
    assert len(names) >= 2, f"nested output not self-contained, only: {names}"


def test_samples_lose_no_members():
    """For each shipped single/nested sample, every input member appears in output."""
    for path in [_sample("*Single*.L5X"), _sample("*Nested*.L5X")]:
        if not path:
            continue
        src = _read(path)
        p = extract_all_udt_definitions(src)
        if "error" in p:
            continue
        tgt = p["target"]
        before = {m["name"] for m in p["udts"][tgt]["members"]}
        res = _optimize_single(src)
        assert res.get("success"), f"{os.path.basename(path)}: {res.get('error')}"
        after = set(_visible_members(res["udt_text"], tgt))
        missing = before - after
        assert not missing, f"{os.path.basename(path)}: dropped members {missing}"


def test_full_program_preserves_members_and_structure():
    path = _sample("*Full*Program*.L5X") or _sample("*Program*.L5X")
    if not path:
        print("    (skip) no full-program sample file found")
        return
    src = _read(path)
    res = optimize_full_program_l5x(src)
    assert res.get("success"), res.get("error")
    before = ET.fromstring(src)
    after  = ET.fromstring(res["l5x_text"])

    def udt_members(root):
        out = {}
        for dt in root.findall(".//DataType"):
            if dt.get("Class") != "User":
                continue
            out[dt.get("Name")] = {
                mm.get("Name") for mm in dt.findall("./Members/Member")
                if not (mm.get("Name") or "").startswith("ZZZZ")
            }
        return out

    b, a = udt_members(before), udt_members(after)
    for name, mem in b.items():
        lost = mem - a.get(name, set())
        assert not lost, f"full-program: '{name}' lost members {lost}"

    def counts(r):
        return (len(r.findall(".//Program")), len(r.findall(".//AddOnInstructionDefinition")),
                len(r.findall(".//Module")), len(r.findall(".//Tag")))
    assert counts(before) == counts(after), \
        f"non-DataType content changed: {counts(before)} -> {counts(after)}"


# ── runner ──────────────────────────────────────────────────────────────────────

def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    print(f"Running {len(tests)} test(s)...\n")
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}\n        {e}")
        except Exception as e:                       # noqa: BLE001
            failed += 1
            print(f"  ERROR {t.__name__}\n        {type(e).__name__}: {e}")
        else:
            passed += 1
            print(f"  ok    {t.__name__}")
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
