"""Build the deterministic Lambda deployment package shared by all three functions.

Same inputs -> byte-identical zip -> identical source_code_hash, so a second `tofu plan`
stays empty. Wheels are resolved for the Lambda platform, not the build host.

    uv run python scripts/build_lambda.py --python-version 3.13 --arch x86_64 --out build/lambda.zip
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# uv --python-platform values (V-013).
PLATFORMS = {"x86_64": "x86_64-manylinux_2_17", "arm64": "aarch64-manylinux_2_17"}
# Provided by the Lambda Python runtime together with boto3 (V-029); bundling a second copy
# would shadow it. urllib3/dateutil/six arrive only as botocore's own dependencies.
RUNTIME_PROVIDED = (
    "boto3",
    "botocore",
    "s3transfer",
    "urllib3",
    "dateutil",
    "python_dateutil",
    "six",
)
# Left behind by `uv pip install --target`; not importable code.
INSTALLER_ARTEFACTS = (".lock", "bin")
FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def run(*argv: str) -> None:
    print("+", " ".join(argv), flush=True)
    subprocess.run(argv, check=True, cwd=ROOT)


def is_runtime_provided(entry: Path) -> bool:
    # Matches the package directory and its dist-info, e.g. botocore/ and botocore-1.35.0.dist-info/
    name = entry.name.lower().replace("-", "_").removesuffix(".py")
    if entry.name in INSTALLER_ARTEFACTS:
        return True
    return any(name == pkg or name.startswith(f"{pkg}_") for pkg in RUNTIME_PROVIDED)


def strip_runtime_provided(staging: Path) -> None:
    for entry in staging.iterdir():
        if not is_runtime_provided(entry):
            continue
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def write_zip(staging: Path, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(path for path in staging.rglob("*") if path.is_file())
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(staging).as_posix(), FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--python-version", default="3.13", choices=["3.12", "3.13"])
    parser.add_argument("--arch", default="x86_64", choices=sorted(PLATFORMS))
    parser.add_argument("--out", default="build/lambda.zip", type=Path)
    args = parser.parse_args()

    build = ROOT / "build"
    staging = build / "lambda"
    requirements = build / "requirements.txt"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)

    # Flags: V-012/V-013 in docs/VERIFICATION_LOG.md.
    run(
        "uv", "export", "--frozen", "--no-dev", "--no-hashes", "--no-emit-project",
        "--format", "requirements-txt", "--output-file", str(requirements),
    )  # fmt: skip
    run(
        "uv", "pip", "install", "--requirements", str(requirements), "--target", str(staging),
        "--python-platform", PLATFORMS[args.arch], "--python-version", args.python_version,
        "--only-binary", ":all:", "--no-compile",
    )  # fmt: skip
    strip_runtime_provided(staging)
    shutil.copytree(
        ROOT / "src" / "tixlane",
        staging / "tixlane",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )

    out = args.out if args.out.is_absolute() else ROOT / args.out
    write_zip(staging, out)
    print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size // 1024} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
