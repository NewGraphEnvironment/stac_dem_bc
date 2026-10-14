"""Every AWS write in update.yml runs on a credential fetched just before it (#60).

The OIDC session lasts one hour from the step that assumes it. The workflow
assumed it once, after setup, and run 38339265803 reached its S3 sync 49 min
later: `ExpiredToken` 11 min into the sync, 91,772 of 102,460 items uploaded,
collection.json never written.
"""

import os
import re

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
WF = os.path.join(ROOT, ".github", "workflows", "update.yml")
CREDS = "aws-actions/configure-aws-credentials"

# Signed AWS access: the CLI in any command position (`&& aws`, `.venv/bin/aws`),
# boto3, or GDAL's signed S3 handler. Not `s3://` alone, which comments use to
# name the bucket. Anonymous HTTPS reads need no credential and do not count.
AWS = re.compile(r"(^|[\s;&|(/`])aws\s+[a-z]|\bboto3\b|/vsis3/", re.M)
SCRIPT = re.compile(r"scripts/[\w./-]+\.(?:py|sh|R)\b")
IMPORT = re.compile(r"^\s*(?:from|import)\s+(\w+)", re.M)


def steps():
    with open(WF) as fh:
        wf = yaml.safe_load(fh)
    return wf["jobs"]["update"]["steps"]


def is_creds(step):
    return str(step.get("uses", "")).startswith(CREDS + "@")


def sources(run):
    """The scripts a step runs, plus the repo modules their Python imports."""
    todo = [os.path.join(ROOT, p) for p in SCRIPT.findall(run)]
    seen = set()
    while todo:
        path = todo.pop()
        if path in seen or not os.path.exists(path):
            continue
        seen.add(path)
        if path.endswith(".py"):
            with open(path) as fh:
                mods = IMPORT.findall(fh.read())
            todo += [os.path.join(ROOT, "scripts", m + ".py") for m in mods]
    return seen


def uses_aws(step):
    uses = str(step.get("uses", ""))
    if uses.startswith("aws-actions/") and not is_creds(step):
        return True
    run = step.get("run", "")
    if AWS.search(run):
        return True
    for path in sources(run):
        with open(path) as fh:
            if AWS.search(fh.read()):
                return True
    return False


def test_the_sync_is_found():
    # A rename of the sync step or its script would otherwise leave the test
    # below checking nothing.
    assert "Sync catalog to S3" in [s.get("name") for s in steps() if uses_aws(s)]


def test_every_aws_step_is_immediately_preceded_by_a_credentials_step():
    ss = steps()
    for i, s in enumerate(ss):
        if uses_aws(s):
            prev = ss[i - 1]
            assert is_creds(prev), f"{s.get('name')!r} follows {prev.get('name') or prev.get('uses')!r}"
            # Same condition, so the credential is fetched exactly when the
            # step that needs it will run.
            assert str(prev.get("if", "")) == str(s.get("if", "")), s.get("name")


def test_every_credentials_step_assumes_the_same_role_in_the_same_region():
    creds = [s["with"] for s in steps() if is_creds(s)]
    assert len(creds) >= 2, "expected the early probe and the pre-sync re-assume"
    roles = {(c["role-to-assume"], c["aws-region"]) for c in creds}
    assert len(roles) == 1, roles
