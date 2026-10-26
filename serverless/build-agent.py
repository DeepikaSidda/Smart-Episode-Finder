"""Build the agent Lambda deployment package.

strands-agents is installed with --no-deps because it declares `mcp`, which declares
`pywin32` on Windows, breaking pip's resolver when targeting Linux from a Windows
machine. Blocking `mcp` via sys.meta_path and importing the agent confirmed strands
only touches mcp inside strands.tools.mcp, which this agent does not use.
"""
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(ROOT, "build")
PKG = os.path.join(BUILD, "agentpkg")
ZIP = os.path.join(BUILD, "agent.zip")

PIP_COMMON = [
    sys.executable, "-m", "pip", "install",
    "--target", PKG,
    "--platform", "manylinux2014_x86_64",
    "--python-version", "3.12",
    "--implementation", "cp",
    "--only-binary=:all:",
    "-q",
]


def run(cmd, label):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"{label} FAILED:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
        sys.exit(1)
    print(f"  {label} ok")


def main():
    os.makedirs(BUILD, exist_ok=True)
    if os.path.isdir(PKG):
        shutil.rmtree(PKG)
    os.makedirs(PKG)

    print("installing dependencies...")
    run(PIP_COMMON + ["-r", os.path.join(ROOT, "requirements-agent.txt")], "deps")
    run(PIP_COMMON + ["--no-deps", "strands-agents==1.45.0"], "strands-agents")

    print("copying source...")
    shutil.copy(os.path.join(ROOT, "agent_handler.py"), PKG)
    for sub in ("agent", "core"):
        shutil.copytree(
            os.path.join(ROOT, sub),
            os.path.join(PKG, sub),
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.md"),
        )

    # Trim weight that Lambda never needs.
    removed = 0
    for dirpath, dirnames, filenames in os.walk(PKG, topdown=True):
        for d in list(dirnames):
            if d in ("__pycache__", "tests", "test") or d.endswith(".dist-info"):
                shutil.rmtree(os.path.join(dirpath, d), ignore_errors=True)
                dirnames.remove(d)
                removed += 1
    print(f"  trimmed {removed} directories")

    if os.path.exists(ZIP):
        os.remove(ZIP)
    print("zipping...")
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for dirpath, _, filenames in os.walk(PKG):
            for name in filenames:
                full = os.path.join(dirpath, name)
                # Forward slashes: Lambda cannot read backslash zip entries.
                arc = os.path.relpath(full, PKG).replace(os.sep, "/")
                zf.write(full, arc)

    unzipped = sum(
        os.path.getsize(os.path.join(r, f))
        for r, _, fs in os.walk(PKG) for f in fs
    )
    print()
    print(f"  agent.zip  : {os.path.getsize(ZIP) / 1024 / 1024:.1f} MB zipped")
    print(f"  unzipped   : {unzipped / 1024 / 1024:.1f} MB  (limit 250 MB)")

    with zipfile.ZipFile(ZIP) as zf:
        names = zf.namelist()
    assert not any("\\" in n for n in names), "backslash entries present"
    for required in ("agent_handler.py", "agent/podcast_agent.py", "core/redact.py"):
        assert required in names, f"missing {required}"
    print("  contents verified")


if __name__ == "__main__":
    main()
