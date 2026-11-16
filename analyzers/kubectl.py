"""
analyzers/kubectl.py
Repairs kubectl commands proposed by the LLM before they are checked and run.

The LLM often copies "namespace/name" from the issue text into the command and
also adds "-n <namespace>", e.g.

    kubectl describe pod ai/ollama-0 -n ai

kubectl reads "ai/ollama-0" as "<type>/<name>" and fails. This module rewrites
it to the form kubectl expects:

    kubectl describe pod ollama-0 -n ai
"""

from __future__ import annotations
import shlex

# Resource types (and short names) the investigation commands use.
RESOURCE_TYPES = {
    "pod", "pods", "po",
    "deployment", "deployments", "deploy",
    "statefulset", "statefulsets", "sts",
    "daemonset", "daemonsets", "ds",
    "replicaset", "replicasets", "rs",
    "service", "services", "svc",
    "job", "jobs", "cronjob", "cronjobs", "cj",
    "configmap", "configmaps", "cm",
    "persistentvolumeclaim", "persistentvolumeclaims", "pvc",
    "ingress", "ingresses", "ing",
    "event", "events", "ev",
    "node", "nodes", "no",
    "namespace", "namespaces", "ns",
}

# Subcommands whose first positional argument is a pod name, not a type.
POD_FIRST_SUBCOMMANDS = {"logs"}


def _split_namespace_flags(argv: list[str]) -> tuple[list[str], str | None]:
    """Remove -n/--namespace flags; return (remaining argv, namespace)."""
    out: list[str] = []
    ns: str | None = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-n", "--namespace") and i + 1 < len(argv):
            ns = argv[i + 1]
            i += 2
            continue
        if a.startswith("--namespace="):
            ns = a.split("=", 1)[1]
        elif a.startswith("-n") and len(a) > 2 and not a.startswith("--"):
            ns = a[2:]
        else:
            out.append(a)
        i += 1
    return out, ns


def _fix_stage(argv: list[str]) -> list[str]:
    if len(argv) < 2 or argv[0] != "kubectl":
        return argv
    rest, ns = _split_namespace_flags(argv)

    sub = next((a for a in rest[1:] if not a.startswith("-")), "")
    fixed: list[str] = []
    changed = False
    positional_seen = 0
    for i, tok in enumerate(rest):
        if i == 0 or tok.startswith("-"):
            fixed.append(tok)
            continue
        positional_seen += 1
        prev = fixed[-1].lower() if fixed else ""
        if tok.count("/") == 1:
            left, right = tok.split("/")
            # "<type>/<name>" is valid kubectl syntax; leave it alone.
            looks_like_ns = left and right and left.lower() not in RESOURCE_TYPES
            after_type = prev in RESOURCE_TYPES
            pod_first = sub in POD_FIRST_SUBCOMMANDS and positional_seen == 2
            if looks_like_ns and (after_type or pod_first):
                fixed.append(right)
                ns = left
                changed = True
                continue
        fixed.append(tok)

    if not changed:
        return argv
    return fixed + ["-n", ns]


def normalize(cmd: str) -> str:
    """Return cmd with "namespace/name" references rewritten for kubectl."""
    if "kubectl" not in (cmd or ""):
        return cmd
    stages = cmd.split("|")
    out: list[str] = []
    changed = False
    for stage in stages:
        try:
            argv = shlex.split(stage)
        except ValueError:
            return cmd
        new = _fix_stage(argv)
        if new != argv:
            changed = True
            out.append(" " + shlex.join(new) + " ")
        else:
            out.append(stage)
    if not changed:
        return cmd
    return "|".join(out).strip()
