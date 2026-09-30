#!/usr/bin/env python3
"""Проверка subtasks.md против артефактов change. Только чтение.

Использование:
    python check_coverage.py <change-id | путь-к-change> [--file subtasks.md]

Проверяет:
  ошибки:
    - у каждой подзадачи есть Источник, Зависит от, Приёмка, Размер (S/M/L);
    - id подзадач уникальны;
    - зависимости ссылаются на существующие подзадачи, циклов нет;
    - ссылки spec: указывают на существующие Requirement, tasks: на существующие пункты;
    - каждый Requirement (ADDED / MODIFIED / REMOVED) покрыт хотя бы одной подзадачей;
  предупреждения:
    - пункт tasks.md не отражён ни в одной подзадаче.

Код выхода: 0 нет ошибок, 1 есть ошибки, 2 нет change или subtasks.md.
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_change import parse_change, resolve_change  # noqa: E402

SUB_RE = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s+(\d+(?:\.\d+)+)\s+(.+?)\s*$")
FIELD_RE = re.compile(
    r"^\s+[-*]\s+(Источник|Зависит от|Приёмка|Приемка|Размер)\s*:\s*(.*)$"
)
FIELD_MAP = {
    "Источник": "source",
    "Зависит от": "deps",
    "Приёмка": "accept",
    "Приемка": "accept",
    "Размер": "size",
}
SPEC_REF_RE = re.compile(r"spec:\s*([^/;]+?)\s*/\s*Requirement:\s*([^;]+)", re.I)
TASK_REF_RE = re.compile(r"tasks:\s*(\d+(?:\.\d+)*(?:\s*,\s*\d+(?:\.\d+)*)*)", re.I)
KNOWN_PREFIX_RE = re.compile(r"\b(spec|design|tasks|proposal):", re.I)
EMPTY_VALUES = {"", "—", "-", "–", "нет"}


def norm(s):
    return re.sub(r"\s+", " ", s).strip().lower()


def parse_subtasks(text):
    subs, cur, last = [], None, None
    for n, line in enumerate(text.splitlines(), 1):
        if re.match(r"^#{1,6}\s", line):
            cur, last = None, None
            continue
        m = SUB_RE.match(line)
        if m:
            cur = {
                "id": m.group(2),
                "title": m.group(3),
                "done": m.group(1).lower() == "x",
                "line": n,
                "fields": {},
            }
            subs.append(cur)
            last = None
            continue
        if cur is None:
            continue
        fm = FIELD_RE.match(line)
        if fm:
            last = FIELD_MAP[fm.group(1)]
            prev = cur["fields"].get(last, "")
            cur["fields"][last] = (prev + " " + fm.group(2).strip()).strip() if prev else fm.group(2).strip()
            continue
        if last and line.strip() and line.startswith((" ", "\t")):
            cur["fields"][last] += " " + line.strip()
    return subs


def find_cycle(graph):
    color = {k: 0 for k in graph}
    stack = []

    def dfs(u):
        color[u] = 1
        stack.append(u)
        for v in graph[u]:
            if v not in graph:
                continue
            if color[v] == 1:
                return stack[stack.index(v):] + [v]
            if color[v] == 0:
                r = dfs(v)
                if r:
                    return r
        stack.pop()
        color[u] = 2
        return None

    for k in graph:
        if color[k] == 0:
            r = dfs(k)
            if r:
                return r
    return None


def main():
    ap = argparse.ArgumentParser(description="Проверка subtasks.md против артефактов change")
    ap.add_argument("change", help="id change или путь к каталогу")
    ap.add_argument("--file", default="subtasks.md", help="имя файла подзадач (по умолчанию subtasks.md)")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    change_dir = resolve_change(args.change)
    if change_dir is None:
        print(f"Change не найден: {args.change}", file=sys.stderr)
        return 2
    sub_path = change_dir / args.file
    if not sub_path.is_file():
        print(f"Файл не найден: {sub_path}", file=sys.stderr)
        return 2

    parsed = parse_change(change_dir)
    subs = parse_subtasks(sub_path.read_text(encoding="utf-8"))
    errors, warnings = [], []

    if not subs:
        errors.append("В файле не найдено ни одной подзадачи вида «- [ ] N.M Описание»")

    # Реестр требований и пунктов tasks.md
    required = {}
    for spec in parsed["specs"]:
        for r in spec["requirements"]:
            required[(norm(spec["capability"]), norm(r["name"]))] = (
                f"{spec['capability']}/{r['name']} ({r['delta']})"
            )
    task_ids = {t["id"] for t in (parsed["tasks"] or []) if t["id"]}

    # Проверки подзадач
    ids, seen = set(), set()
    covered, tasks_referenced = set(), set()
    graph = {}
    for s in subs:
        label = f"{s['id']} (строка {s['line']})"
        if s["id"] in seen:
            errors.append(f"{label}: повторяющийся id")
        seen.add(s["id"])
        ids.add(s["id"])
        f = s["fields"]

        for key, name in (("source", "Источник"), ("accept", "Приёмка")):
            if norm(f.get(key, "")) in EMPTY_VALUES:
                errors.append(f"{label}: пустое поле «{name}»")
        if "deps" not in f:
            errors.append(f"{label}: нет поля «Зависит от» (если зависимостей нет, укажите «—»)")
        if f.get("size", "").strip().upper() not in ("S", "M", "L"):
            errors.append(f"{label}: «Размер» должен быть S, M или L (сейчас: «{f.get('size', '')}»)")

        src = f.get("source", "")
        if norm(src) not in EMPTY_VALUES and not KNOWN_PREFIX_RE.search(src):
            errors.append(f"{label}: в «Источник» нет ссылок вида spec:/design:/tasks:/proposal:")
        for cap, name in SPEC_REF_RE.findall(src):
            key = (norm(cap), norm(name))
            if key in required:
                covered.add(key)
            else:
                errors.append(f"{label}: ссылка на несуществующее требование spec:{cap}/Requirement:{name.strip()}")
        for group in TASK_REF_RE.findall(src):
            for tid in re.split(r"\s*,\s*", group):
                tid = tid.strip(".")
                tasks_referenced.add(tid)
                if parsed["tasks"] is not None and tid not in task_ids:
                    errors.append(f"{label}: ссылка на несуществующий пункт tasks:{tid}")

        deps_raw = f.get("deps", "")
        deps = []
        if "deps" in f and norm(deps_raw) not in EMPTY_VALUES:
            deps = re.findall(r"\d+(?:\.\d+)+", deps_raw)
            if not deps:
                errors.append(f"{label}: не удалось разобрать «Зависит от»: «{deps_raw}»")
        graph[s["id"]] = deps

    for sid, deps in graph.items():
        for d in deps:
            if d not in ids:
                errors.append(f"{sid}: зависит от несуществующей подзадачи {d}")
            if d == sid:
                errors.append(f"{sid}: зависит от самой себя")
    cycle = find_cycle(graph)
    if cycle:
        errors.append("Цикл в зависимостях: " + " → ".join(cycle))

    # Покрытие требований
    uncovered = [label for key, label in required.items() if key not in covered]
    for label in uncovered:
        errors.append(f"Требование не покрыто ни одной подзадачей: {label}")

    # Покрытие пунктов tasks.md
    if parsed["tasks"]:
        for t in parsed["tasks"]:
            if t["id"] and t["id"] not in tasks_referenced:
                warnings.append(f"Пункт tasks.md {t['id']} «{t['text']}» не отражён ни в одной подзадаче")

    # Отчёт
    print(f"Change: {parsed['change_id']}  Файл: {sub_path.name}")
    print(
        f"Подзадач: {len(subs)}; требований: {len(required)}, покрыто: {len(required) - len(uncovered)}"
    )
    if errors:
        print(f"\nОшибки ({len(errors)}):")
        for e in errors:
            print(f"  - {e}")
    if warnings:
        print(f"\nПредупреждения ({len(warnings)}):")
        for w in warnings:
            print(f"  - {w}")
    if not errors and not warnings:
        print("\nПроблем не найдено.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
