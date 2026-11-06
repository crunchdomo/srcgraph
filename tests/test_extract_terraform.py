from pathlib import Path
from srcgraph.extractors import EXTRACTORS
from srcgraph.extractors.terraform import extract


def test_symbols_and_refs():
    e = extract("main.tf", Path("tests/fixtures/tf/main.tf").read_bytes(), "main.tf")
    assert EXTRACTORS[".tf"] is extract
    assert e.units[0].lang == "terraform"
    assert {(s.name, s.kind) for s in e.symbols} == {
        ("var.name", "variable"), ("local.suffix", "variable"), ("aws_s3_bucket.logs", "resource"),
        ("aws_s3_bucket.orphan", "resource"), ("aws_s3_bucket_policy.p", "resource"),
        ("data.aws_iam_policy_document.doc", "resource"), ("module.net", "module_call"), ("output.vpc", "variable")}
    refs = {(r.src_id.split("::")[1], r.name) for r in e.refs}
    assert refs == {("aws_s3_bucket.logs", "var.name"), ("aws_s3_bucket.logs", "local.suffix"),
                    ("aws_s3_bucket_policy.p", "aws_s3_bucket.logs"),
                    ("aws_s3_bucket_policy.p", "data.aws_iam_policy_document.doc"), ("output.vpc", "module.net")}
    assert all(r.relation == "references" for r in e.refs)
    logs = next(s for s in e.symbols if s.name == "aws_s3_bucket.logs")
    assert logs.line == 5 and logs.id == "main.tf::aws_s3_bucket.logs"


def test_scan_resolves_terraform_refs(monkeypatch):
    from srcgraph.scan import scan
    monkeypatch.chdir("tests/fixtures/tf")
    e = {(d.label, d.src, d.dst) for d in scan(["."]).edges}
    assert ("REFERENCES", "main.tf::aws_s3_bucket_policy.p", "main.tf::aws_s3_bucket.logs") in e
    assert ("REFERENCES", "main.tf::output.vpc", "main.tf::module.net") in e


def test_terraform_syntax_tier():
    e = extract("main.tf", Path("tests/fixtures/tf/main.tf").read_bytes(), "main.tf")
    assert any(a.kind == "block" and a.norm == "" for a in e.ast)
    s = next(a for a in e.ast if a.kind == "string_lit" and a.text == '"aws_s3_bucket"')
    assert s.norm == "String"
    logs = next(x for x in e.symbols if x.name == "aws_s3_bucket.logs")
    link = next(d for d in e.edges if d.label == "DERIVED_FROM" and d.src == logs.id)
    assert link.dst.endswith(":block")
    suffix = next(x for x in e.symbols if x.name == "local.suffix")
    assert next(d for d in e.edges if d.label == "DERIVED_FROM" and d.src == suffix.id).dst.endswith(":attribute")
