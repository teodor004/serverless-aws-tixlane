#!/usr/bin/env python3
"""Claude Code PreToolUse guard for the TixLane repository.

Blocks (exit code 2, reason on stderr):
  * tofu/terraform apply, destroy, import and state surgery outside infra/envs/local
  * aws CLI calls that are not read-only, unless --endpoint-url points at the local emulator
  * other deploy tools (cdk, sam, serverless, pulumi) and scripts/e2e.py --allow-real-aws
  * git commits that skip hooks, stage secret-like files, or fail a gitleaks scan
  * `git add --force`, and file writes that look like credentials or real account IDs

It is a second line of defence behind the deny rules in .claude/settings.json and the
GitHub environment approval (ADR-0007). Self-test: python3 .claude/hooks/guard.py --self-test
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

LOCAL_ENV_DIR = PurePosixPath("infra/envs/local")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "emulator", "floci", "ministack"}
TOFU_MUTATIONS = {"apply", "destroy", "import", "taint", "untaint", "force-unlock"}
TOFU_STATE_MUTATIONS = {"rm", "mv", "push", "replace-provider"}
AWS_READ_ONLY = re.compile(
    r"^(describe|get|list|head|batch-get|lookup|search|scan|query|filter|validate|test|wait"
    r"|simulate|estimate|preview|help)(-|$)"
)
AWS_READ_ONLY_COMMANDS = {("s3", "ls"), ("sts", "get-caller-identity"), ("configure", "list")}
AWS_BOOLEAN_FLAGS = {"--no-cli-pager", "--debug", "--no-paginate", "--no-verify-ssl"}
DEPLOY_TOOLS = {"cdk", "sam", "serverless", "sls", "pulumi", "copilot", "eb"}
DEPLOY_VERBS = {"deploy", "up", "destroy", "remove"}
WRAPPERS = {"sudo", "env", "time", "command", "exec", "nohup", "uv", "uvx", "run", "poetry"}
SECRET_FILE = re.compile(
    r"(^|/)(\.env|.*\.tfvars|.*\.tfvars\.json|.*\.tfstate(\..*)?|.*\.pem|.*\.key|id_rsa.*"
    r"|credentials|\.aws/.*)$"
)
SECRET_FILE_ALLOWED = re.compile(r"(\.example|\.env\.example)$")
SECRET_CONTENT = [
    (re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"), "AWS access key ID"),
    (re.compile(r"aws_secret_access_key\s*[=:]\s*['\"]?[A-Za-z0-9/+]{40}"), "AWS secret key"),
    (re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"), "private key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), "GitHub token"),
]
ACCOUNT_IN_ARN = re.compile(r"arn:aws[a-z-]*:[a-z0-9-]+:[a-z0-9-]*:(\d{12}):")
ENV_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
PLACEHOLDER_ACCOUNTS = {"000000000000", "123456789012", "111122223333", "444455556666"}


class BlockedError(Exception):
    pass


def is_secret_file(path: str) -> bool:
    return bool(SECRET_FILE.search(path)) and not SECRET_FILE_ALLOWED.search(path)


# --------------------------------------------------------------------------------------------
# Bash commands
# --------------------------------------------------------------------------------------------


def split_segments(command: str) -> list[list[str]]:
    """Split a shell line on control operators and tokenise each simple command."""
    segments: list[list[str]] = []
    for part in re.split(r"&&|\|\||[;|\n]|\$\(|`|\(|\)", command):
        part = part.strip()
        if not part:
            continue
        try:
            tokens = shlex.split(part, posix=True)
        except ValueError:
            tokens = part.split()
        while tokens and (ENV_ASSIGNMENT.fullmatch(tokens[0]) or tokens[0] in WRAPPERS):
            tokens = tokens[1:]
        if tokens:
            segments.append(tokens)
    return segments


def check_tofu(tokens: list[str], cwd: Path, project: Path) -> None:
    chdir = None
    args: list[str] = []
    for token in tokens[1:]:
        if token.startswith("-chdir="):
            chdir = token.split("=", 1)[1]
        elif not token.startswith("-"):
            args.append(token)
    if not args:
        return
    sub = args[0]
    state_surgery = sub == "state" and len(args) > 1 and args[1] in TOFU_STATE_MUTATIONS
    if sub not in TOFU_MUTATIONS and not state_surgery:
        return
    target = (cwd / chdir).resolve() if chdir else cwd.resolve()
    try:
        relative = PurePosixPath(target.relative_to(project.resolve()).as_posix())
    except ValueError:
        relative = None
    if relative != LOCAL_ENV_DIR:
        raise BlockedError(
            f"`{tokens[0]} {sub}` is only allowed in {LOCAL_ENV_DIR} (emulator). Real AWS changes "
            "go through the approval-gated GitHub workflows (CLAUDE.md, ADR-0007)."
        )


def endpoint_is_local(tokens: list[str]) -> bool:
    for i, arg in enumerate(tokens):
        value = None
        if arg == "--endpoint-url" and i + 1 < len(tokens):
            value = tokens[i + 1]
        elif arg.startswith("--endpoint-url="):
            value = arg.split("=", 1)[1]
        if value:
            host = re.sub(r"^[a-z]+://", "", value).split("/")[0].rsplit(":", 1)[0].strip("[]")
            return host in LOCAL_HOSTS
    return False


def check_aws(tokens: list[str]) -> None:
    positional: list[str] = []
    skip_next = False
    for token in tokens[1:]:
        if skip_next:
            skip_next = False
            continue
        if token.startswith("--"):
            skip_next = "=" not in token and token not in AWS_BOOLEAN_FLAGS
            continue
        positional.append(token)
    if len(positional) < 2:
        return
    service, operation = positional[0], positional[1]
    if (service, operation) in AWS_READ_ONLY_COMMANDS or AWS_READ_ONLY.match(operation):
        return
    if endpoint_is_local(tokens):
        return
    raise BlockedError(
        f"`aws {service} {operation}` may mutate real AWS. Only read-only calls, or calls with "
        "--endpoint-url pointing at the local emulator, are allowed (CLAUDE.md)."
    )


def staged_files(project: Path) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )
    return [line for line in result.stdout.splitlines() if line]


def run_gitleaks(project: Path) -> None:
    binary = shutil.which("gitleaks")
    if binary is None:
        raise BlockedError(
            "gitleaks is not installed; commits are blocked until it is (fail closed)."
        )
    result = subprocess.run(
        [binary, "git", "--pre-commit", "--staged", "--redact", "--config", ".gitleaks.toml"],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stdout + result.stderr)[-2000:]
        raise BlockedError("gitleaks found a potential secret in staged changes:\n" + detail)


def check_git(tokens: list[str], project: Path) -> None:
    if len(tokens) < 2:
        return
    sub = tokens[1]
    if sub == "add" and any(t in {"-f", "--force"} for t in tokens[2:]):
        raise BlockedError("`git add --force` would stage ignored (possibly secret) files.")
    if sub == "push" and any(t in {"-f", "--force", "--force-with-lease"} for t in tokens[2:]):
        raise BlockedError("Force-push is not allowed.")
    if sub != "commit":
        return
    if any(t in {"-n", "--no-verify"} for t in tokens[2:]):
        raise BlockedError("Commits must not skip the pre-commit hooks (--no-verify).")
    offending = [name for name in staged_files(project) if is_secret_file(name)]
    if offending:
        raise BlockedError("Refusing to commit secret-like files: " + ", ".join(offending))
    run_gitleaks(project)


def check_bash(command: str, cwd: Path, project: Path) -> None:
    for tokens in split_segments(command):
        name = os.path.basename(tokens[0])
        if name == "cd" and len(tokens) > 1:
            cwd = cwd / tokens[1]
        elif name in {"tofu", "terraform"}:
            check_tofu(tokens, cwd, project)
        elif name == "aws":
            check_aws(tokens)
        elif name in DEPLOY_TOOLS and len(tokens) > 1 and tokens[1] in DEPLOY_VERBS:
            raise BlockedError(f"`{name} {tokens[1]}` is not part of this project's deploy path.")
        elif name == "git":
            check_git(tokens, project)
        if "--allow-real-aws" in tokens and os.environ.get("GITHUB_ACTIONS") != "true":
            raise BlockedError(
                "--allow-real-aws is reserved for the approval-gated GitHub workflows."
            )


# --------------------------------------------------------------------------------------------
# File writes
# --------------------------------------------------------------------------------------------


def check_write(tool_input: dict[str, Any], project: Path) -> None:
    path = str(tool_input.get("file_path", ""))
    try:
        relative = Path(path).resolve().relative_to(project.resolve()).as_posix()
    except ValueError:
        relative = path.replace("\\", "/")
    if is_secret_file(relative):
        raise BlockedError(f"Refusing to write secret-like file {relative} (use a *.example file).")
    content = "\n".join(
        str(tool_input.get(key, "")) for key in ("content", "new_string", "new_source")
    )
    for edit in tool_input.get("edits", []) or []:
        content += "\n" + str(edit.get("new_string", ""))
    for pattern, label in SECRET_CONTENT:
        if pattern.search(content):
            raise BlockedError(f"Content looks like a {label}; secrets never go into files.")
    for account in ACCOUNT_IN_ARN.findall(content):
        if account not in PLACEHOLDER_ACCOUNTS:
            raise BlockedError(
                "Content contains an ARN with a real-looking account ID; use 123456789012 or a "
                "variable instead."
            )


# --------------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------------


def evaluate(payload: dict[str, Any]) -> str | None:
    default_project = Path(__file__).resolve().parents[2]
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or default_project)
    cwd = Path(payload.get("cwd") or project)
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    try:
        if tool == "Bash":
            check_bash(str(tool_input.get("command", "")), cwd, project)
        elif tool in {"Write", "Edit", "MultiEdit", "NotebookEdit"}:
            check_write(tool_input, project)
    except BlockedError as blocked:
        return str(blocked)
    return None


def self_test() -> int:
    project = Path(__file__).resolve().parents[2]
    p = str(project)

    def bash(command: str, cwd: str = p) -> dict[str, Any]:
        return {"tool_name": "Bash", "cwd": cwd, "tool_input": {"command": command}}

    def write(path: str, content: str) -> dict[str, Any]:
        tool_input = {"file_path": f"{p}/{path}", "content": content}
        return {"tool_name": "Write", "cwd": p, "tool_input": tool_input}

    local_sqs = "aws --endpoint-url http://localhost:4566 sqs send-message --queue-url q"
    aws_endpoint = "--endpoint-url https://dynamodb.eu-central-1.amazonaws.com"
    cases = [
        (bash("tofu -chdir=infra/envs/local apply -auto-approve"), False),
        (bash("tofu apply -auto-approve", f"{p}/infra/envs/local"), False),
        (bash("make local-apply"), False),
        (bash("tofu -chdir=infra/envs/local plan"), False),
        (bash("tofu -chdir=infra/envs/aws apply tfplan"), True),
        (bash("cd bootstrap && tofu apply"), True),
        (bash("cd infra/envs/local && tofu apply -auto-approve"), False),
        (bash("cd infra/envs/local; cd ../aws && tofu apply"), True),
        (bash("tofu -chdir=bootstrap destroy"), True),
        (bash("tofu -chdir=infra/envs/aws import aws_s3_bucket.x y"), True),
        (bash("tofu -chdir=infra/envs/aws state rm module.data"), True),
        (bash("tofu -chdir=infra/envs/aws plan"), False),
        (bash("aws sts get-caller-identity"), False),
        (bash("aws resourcegroupstaggingapi get-resources --region eu-central-1"), False),
        (bash("AWS_PAGER= aws s3 ls"), False),
        (bash(f"{local_sqs} --message-body x"), False),
        (bash("aws sqs send-message --queue-url q --message-body x"), True),
        (bash("aws --region eu-central-1 s3 rm s3://bucket --recursive"), True),
        (bash(f"aws dynamodb delete-table --table-name t {aws_endpoint}"), True),
        (bash("echo hi && aws iam create-user --user-name x"), True),
        (bash("uv run python scripts/e2e.py --outputs o.json --allow-real-aws smoke"), True),
        (bash("cdk deploy"), True),
        (bash("git add -f .env"), True),
        (bash("git commit --no-verify -m x"), True),
        (bash("git push --force origin main"), True),
        (bash("git status"), False),
        (write("infra/envs/aws/terraform.tfvars", "x = 1"), True),
        (write("infra/envs/aws/terraform.tfvars.example", "x = 1"), False),
        (write(".env", "FLOCI_IMAGE=x"), True),
        (write("src/x.py", "KEY = 'AKIA" + "ABCDEFGHIJKLMNOP'"), True),
        (write("docs/x.md", "arn:aws:iam::123456789012:role/x"), False),
        (write("docs/x.md", "arn:aws:iam::" + "987654321098:role/x"), True),
    ]
    failures = 0
    for payload, should_block in cases:
        reason = evaluate(payload)
        if (reason is not None) != should_block:
            failures += 1
            print(f"FAIL expected block={should_block}: {payload['tool_input']} -> {reason}")
    print(f"{len(cases) - failures}/{len(cases)} guard cases passed")
    return 1 if failures else 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("guard.py: unreadable hook input; blocking to fail closed", file=sys.stderr)
        return 2
    reason = evaluate(payload)
    if reason is None:
        return 0
    print(f"TixLane guard: {reason}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
