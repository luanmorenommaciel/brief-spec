"""Done-when 2: every Mermaid block renders without errors in CI.

The offline ``--check`` uses a structural validator. This test runs the real Mermaid parser
(pinned, installed with npm into the pytest cache) over the Mermaid that ``brief-spec explain``
generates and over a fixture corpus, and asserts that both validators agree. It runs inside the
existing CI ``pytest`` step; outside CI it skips when Node or npm is unavailable.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from explain_repo import make_repo

from briefspec.explain import build_skeleton, mermaid_errors, render_frame_markdown

MERMAID = "mermaid@11.17.2"
JSDOM = "jsdom@26.1.0"

VALID = [
    'flowchart LR\n  subgraph s1["src/app/"]\n    a["core.py"]\n  end\n  a --> b["gate.py"]',
    "flowchart TD\n  A(round) --> B{decide}\n  B -->|yes| C[[sub]]\n  %% comment",
    "sequenceDiagram\n  participant H as Hook\n  H->>G: stop_gate()\n  G-->>H: reason",
    "classDiagram\n  class StopGate {\n    +should_block() bool\n  }",
    "stateDiagram-v2\n  [*] --> Blocked\n  Blocked --> Allowed\n  Allowed --> [*]",
]
INVALID = [
    "flowchart LR\n  A[unclosed --> B",
    "flowchart LR\n  A --> ",
    "flowchart XY\n  A --> B",
    "notADiagram\n  A --> B",
    'flowchart LR\n  A["open --> B',
    "flowchart LR\n  subgraph one\n  A --> B",
    "flowchart LR\n  end",
]

_PARSE = """\
import { JSDOM } from "jsdom";
const dom = new JSDOM("<!doctype html><html><body></body></html>");
globalThis.window = dom.window;
globalThis.document = dom.window.document;
const { default: mermaid } = await import("mermaid");
mermaid.initialize({ startOnLoad: false });
const sources = JSON.parse(await new Promise((resolve) => {
  let data = "";
  process.stdin.on("data", (chunk) => (data += chunk));
  process.stdin.on("end", () => resolve(data));
}));
const results = [];
for (const source of sources) {
  try {
    await mermaid.parse(source);
    results.push(null);
  } catch (error) {
    results.push(String(error && error.message ? error.message : error).split("\\n")[0]);
  }
}
process.stdout.write(JSON.stringify(results));
"""


def generated_blocks(root: Path) -> list[str]:
    text = render_frame_markdown(build_skeleton(make_repo(root)))
    blocks = re.findall(r"^```mermaid\n(.*?)\n```$", text, re.MULTILINE | re.DOTALL)
    assert blocks, "the fixture frame should include a Mermaid starter"
    return blocks


def test_offline_validator_accepts_generated_and_valid_blocks(
    tmp_path: Path, isolated_homes: dict[str, Path]
) -> None:
    for source in [*generated_blocks(tmp_path), *VALID]:
        assert mermaid_errors(source) == [], source
    for source in INVALID:
        assert mermaid_errors(source), source


def _unavailable(reason: str) -> None:
    if os.environ.get("CI"):
        pytest.fail(f"real Mermaid check must run in CI: {reason}")
    pytest.skip(reason)


@pytest.fixture(scope="module")
def mermaid_parser(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory):
    node, npm = shutil.which("node"), shutil.which("npm")
    if not node or not npm:
        _unavailable("node and npm are required for the real Mermaid parser")
    cache = getattr(request.config, "cache", None)
    root = (
        cache.mkdir(f"mermaid-{MERMAID}-{JSDOM}".replace("@", "-"))
        if cache is not None
        else tmp_path_factory.mktemp("mermaid")
    )
    if not (root / "node_modules" / "mermaid" / "package.json").exists():
        (root / "package.json").write_text('{"private": true}\n', encoding="utf-8")
        installed = subprocess.run(
            [npm, "install", "--no-audit", "--no-fund", "--loglevel=error", MERMAID, JSDOM],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if installed.returncode != 0:
            _unavailable(f"npm install failed: {installed.stderr.strip()[-400:]}")
    script = root / "parse.mjs"
    script.write_text(_PARSE, encoding="utf-8")

    def parse(sources: list[str]) -> list[str | None]:
        result = subprocess.run(
            [node, str(script)],
            cwd=root,
            input=json.dumps(sources),
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        return json.loads(result.stdout)

    return parse


def test_real_mermaid_parses_every_generated_and_valid_block(
    mermaid_parser, tmp_path: Path, isolated_homes: dict[str, Path]
) -> None:
    sources = [*generated_blocks(tmp_path), *VALID]
    failures = {
        source: error
        for source, error in zip(sources, mermaid_parser(sources), strict=True)
        if error
    }
    assert failures == {}


def test_real_mermaid_rejects_what_the_offline_check_rejects(mermaid_parser) -> None:
    results = mermaid_parser(INVALID)
    assert all(results), dict(zip(INVALID, results, strict=True))
