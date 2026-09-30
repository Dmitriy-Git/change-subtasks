#!/usr/bin/env python3
"""Разбор артефактов OpenSpec change в JSON. Только чтение, ничего не пишет.

Использование:
    python parse_change.py <change-id | путь-к-change> [--root <корень репо>] [--save-baseline]
    python parse_change.py --list [--root <корень репо>]   # активные change

--save-baseline сохраняет sha256 артефактов во временный каталог системы
(не в репозиторий); check_coverage.py сверяет с ними, какие файлы менялись.

Выводит в stdout JSON: наличие файлов и их sha256, proposal, design, tasks,
specs (Requirements и Scenarios по дельтам) и предупреждения (warnings)
с кодами из questions.md.
"""
import argparse
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

DELTA_RE = re.compile(r"^##\s+(ADDED|MODIFIED|REMOVED|RENAMED)\s+Requirements\s*$", re.I)
FULL_RE = re.compile(r"^##\s+Requirements\s*$", re.I)
H2_RE = re.compile(r"^##\s")
REQ_RE = re.compile(r"^###\s+Requirement:\s*(.+?)\s*$")
SCEN_RE = re.compile(r"^####\s+Scenario:\s*(.+?)\s*$")
STEP_KW = {
    "GIVEN": "GIVEN", "WHEN": "WHEN", "THEN": "THEN", "AND": "AND", "BUT": "BUT",
    "ДАНО": "GIVEN", "КОГДА": "WHEN", "ТОГДА": "THEN", "И": "AND", "НО": "BUT",
}
# Шаг сценария: маркер списка (-, *, 1., 1)) необязателен, ключевое слово
# может быть выделено ** и заканчиваться двоеточием; русские ключевые слова тоже.
STEP_RE = re.compile(
    r"^\s*(?:[-*]|\d+[.)])?\s*\*{0,2}("
    + "|".join(STEP_KW)
    + r")\b\*{0,2}:?\*{0,2}\s*(.*)$",
    re.I,
)
BREAKING_RE = re.compile(r"(?<![-\w])BREAKING\b")
RENAME_RE = re.compile(
    r"^\s*[-*]\s*(FROM|TO)\s*:\s*`?(?:###\s*Requirement:\s*)?(.+?)`?\s*$", re.I
)
HEAD_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
TASK_RE = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s+(.*)$")
NUM_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.*)$")
OPEN_Q_RE = re.compile(r"open questions|открытые вопросы|нерешённые|нерешенные", re.I)
LIST_ITEM_RE = re.compile(r"^(\s*)(?:[-*]|\d+\.)\s+(?:\[[ xX]\]\s+)?(.+)$")

ROOT = Path(".")


def changes_base():
    return ROOT / "openspec" / "changes"


def baseline_path(change_dir):
    """Файл с базовыми хэшами во временном каталоге системы (вне репозитория)."""
    key = hashlib.sha256(str(Path(change_dir).resolve()).encode("utf-8")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / "change-subtasks" / f"{Path(change_dir).name}-{key}.json"


def file_hashes(parsed):
    return {k: v["sha256"] for k, v in parsed["files"].items() if v["exists"] and k != "subtasks.md"}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return path.read_text(encoding="utf-8")


def resolve_change(arg):
    """Вернуть Path каталога change по пути или id, иначе None."""
    p = Path(arg)
    if not p.is_absolute():
        p = ROOT / p
    if p.is_dir():
        return p
    q = changes_base() / arg
    if q.is_dir():
        return q
    return None


def list_changes():
    base = changes_base()
    if not base.is_dir():
        return None
    return sorted(
        d.name for d in base.iterdir() if d.is_dir() and d.name != "archive"
    )


def parse_specs(change_dir, warnings):
    specs_dir = change_dir / "specs"
    result = []
    if not specs_dir.is_dir():
        return result
    for path in sorted(specs_dir.glob("**/spec.md")):
        rel = path.relative_to(change_dir).as_posix()
        cap = path.parent.name if path.parent != specs_dir else "(root)"
        reqs, renamed = [], []
        delta, req, scen = None, None, None
        in_fence = False
        for n, line in enumerate(read(path).splitlines(), 1):
            if line.strip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            m = DELTA_RE.match(line)
            if m:
                delta, req, scen = m.group(1).upper(), None, None
                continue
            if FULL_RE.match(line):
                delta, req, scen = "FULL", None, None
                continue
            if H2_RE.match(line):
                delta, req, scen = None, None, None
                continue
            if delta == "RENAMED":
                rm = RENAME_RE.match(line)
                if rm:
                    if rm.group(1).upper() == "FROM":
                        renamed.append({"from": rm.group(2), "to": None})
                    elif renamed:
                        renamed[-1]["to"] = rm.group(2)
                continue
            m = REQ_RE.match(line)
            if m:
                req = {
                    "name": m.group(1),
                    "delta": delta or "UNKNOWN",
                    "line": n,
                    "text": [],
                    "scenarios": [],
                }
                reqs.append(req)
                scen = None
                continue
            m = SCEN_RE.match(line)
            if m and req is not None:
                scen = {"name": m.group(1), "line": n, "steps": []}
                req["scenarios"].append(scen)
                continue
            sm = STEP_RE.match(line)
            if scen is not None and sm:
                scen["steps"].append(
                    {"kw": STEP_KW[sm.group(1).upper()], "text": sm.group(2).strip()}
                )
                continue
            if (
                scen is not None
                and scen["steps"]
                and line.startswith((" ", "\t"))
                and line.strip()
            ):
                scen["steps"][-1]["text"] += " " + line.strip()
                continue
            if req is not None and scen is None and line.strip():
                req["text"].append(line.strip())

        for r in reqs:
            r["text"] = " ".join(r["text"])
            if r["delta"] in ("ADDED", "MODIFIED", "FULL") and not r["scenarios"]:
                warnings.append(
                    {
                        "code": "S-1",
                        "blocking": False,
                        "where": f"{rel}:{r['line']}",
                        "msg": f"У Requirement «{r['name']}» нет ни одного Scenario",
                    }
                )
            for s in r["scenarios"]:
                kws = {st["kw"] for st in s["steps"]}
                if "WHEN" not in kws or "THEN" not in kws:
                    warnings.append(
                        {
                            "code": "S-2",
                            "blocking": False,
                            "where": f"{rel}:{s['line']}",
                            "msg": f"Scenario «{s['name']}» без WHEN или THEN",
                        }
                    )
        result.append(
            {"capability": cap, "path": rel, "requirements": reqs, "renamed": renamed}
        )
    return result


def parse_design(text):
    heads, open_q = [], []
    oq_level, oq_indent = None, None
    for n, line in enumerate(text.splitlines(), 1):
        m = HEAD_RE.match(line)
        if m:
            level, title = len(m.group(1)), m.group(2)
            heads.append({"level": level, "title": title, "line": n})
            if oq_level is not None and level <= oq_level:
                oq_level = None
            if OPEN_Q_RE.search(title):
                oq_level, oq_indent = level, None
            continue
        if oq_level is not None:
            im = LIST_ITEM_RE.match(line)
            if im:
                indent = len(im.group(1).expandtabs(4))
                if oq_indent is None or indent <= oq_indent:
                    oq_indent = indent
                    open_q.append({"text": im.group(2).strip(), "line": n})
                elif open_q:
                    # Вложенный пункт уточняет предыдущий вопрос, а не новый вопрос.
                    open_q[-1]["text"] += " / " + im.group(2).strip()
    return {"headings": heads, "open_questions": open_q}


def parse_proposal(text):
    sections, cur, breaking = {}, None, []
    for n, line in enumerate(text.splitlines(), 1):
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            cur = m.group(1)
            sections[cur] = []
            continue
        if cur is not None and line.strip():
            sections[cur].append(line.rstrip())
        if BREAKING_RE.search(line):
            breaking.append({"line": n, "text": line.strip()})
    return {
        "sections": {k: "\n".join(v) for k, v in sections.items()},
        "breaking": breaking,
    }


def parse_tasks(text):
    items, group = [], None
    for n, line in enumerate(text.splitlines(), 1):
        h = re.match(r"^##\s+(.+?)\s*$", line)
        if h:
            group = h.group(1)
            continue
        m = TASK_RE.match(line)
        if m:
            body = m.group(2).strip()
            nm = NUM_RE.match(body)
            items.append(
                {
                    "id": nm.group(1) if nm else None,
                    "text": nm.group(2) if nm else body,
                    "done": m.group(1).lower() == "x",
                    "group": group,
                    "line": n,
                }
            )
    return items


def parse_change(change_dir):
    change_dir = Path(change_dir)
    warnings = []

    files = {}
    for name in ("proposal.md", "design.md", "tasks.md", "subtasks.md"):
        p = change_dir / name
        files[name] = {
            "exists": p.is_file(),
            "sha256": sha256(p) if p.is_file() else None,
        }
    for p in sorted((change_dir / "specs").glob("**/spec.md")):
        files[p.relative_to(change_dir).as_posix()] = {
            "exists": True,
            "sha256": sha256(p),
        }

    def warn(code, blocking, msg):
        warnings.append({"code": code, "blocking": blocking, "where": str(change_dir), "msg": msg})

    if not files["proposal.md"]["exists"]:
        warn("F-1", True, "Нет proposal.md")
    if not any(k.startswith("specs/") for k in files):
        warn("F-2", True, "Нет specs/**/spec.md")
    if not files["design.md"]["exists"]:
        warn("F-3", False, "Нет design.md")
    if not files["tasks.md"]["exists"]:
        warn("F-4", False, "Нет tasks.md")
    if files["subtasks.md"]["exists"]:
        warn("F-5", False, "subtasks.md уже существует")

    proposal = (
        parse_proposal(read(change_dir / "proposal.md"))
        if files["proposal.md"]["exists"]
        else None
    )
    design = (
        parse_design(read(change_dir / "design.md"))
        if files["design.md"]["exists"]
        else None
    )
    tasks = (
        parse_tasks(read(change_dir / "tasks.md"))
        if files["tasks.md"]["exists"]
        else None
    )
    specs = parse_specs(change_dir, warnings)

    return {
        "change_id": change_dir.name,
        "path": str(change_dir),
        "files": files,
        "proposal": proposal,
        "design": design,
        "tasks": tasks,
        "specs": specs,
        "warnings": warnings,
    }


def main():
    ap = argparse.ArgumentParser(description="Разбор артефактов OpenSpec change в JSON")
    ap.add_argument("change", nargs="?", help="id change или путь к каталогу")
    ap.add_argument("--list", action="store_true", help="показать активные change")
    ap.add_argument("--root", default=".", help="корень репозитория (по умолчанию текущий каталог)")
    ap.add_argument(
        "--save-baseline",
        action="store_true",
        help="сохранить хэши артефактов во временный каталог для проверки в check_coverage.py",
    )
    args = ap.parse_args()
    global ROOT
    ROOT = Path(args.root)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.list:
        names = list_changes()
        if names is None:
            print(f"Каталог не найден: {changes_base()} (запустите из корня репозитория или укажите --root)", file=sys.stderr)
            return 2
        if not names:
            print("Активных change нет", file=sys.stderr)
        for name in names:
            print(name)
        return 0
    if not args.change:
        ap.error("укажите change или используйте --list")
    change_dir = resolve_change(args.change)
    if change_dir is None:
        print(f"Change не найден: {args.change}", file=sys.stderr)
        return 2
    parsed = parse_change(change_dir)
    if args.save_baseline:
        bp = baseline_path(change_dir)
        bp.parent.mkdir(parents=True, exist_ok=True)
        bp.write_text(json.dumps({"path": str(Path(change_dir).resolve()), "files": file_hashes(parsed)}, ensure_ascii=False, indent=2), encoding="utf-8")
        parsed["baseline"] = str(bp)
    json.dump(parsed, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
