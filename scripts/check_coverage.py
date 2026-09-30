#!/usr/bin/env python3
"""Проверка subtasks.md против артефактов change. Только чтение.

Использование:
    python check_coverage.py <change-id | путь-к-change> [--file subtasks.md]
                             [--root <корень репо>] [--max-subtasks 10]

Проверяет:
  ошибки:
    - подзадач не больше лимита (по умолчанию 10);
    - подзадача оформлена блоком «### N.M Название» с полями Размер (S/M/L),
      Источник, Зависит от, Описание, Что планируется сделать и Критерии приёмки
      (последние два нумерованными списками); Затрагиваемые компоненты необязательны;
    - id подзадач уникальны;
    - «Зависит от» содержит только id существующих подзадач, циклов нет;
    - ссылки spec:, design:, tasks:, proposal: указывают на существующие
      Requirement, разделы и пункты; ссылки разделены «;»;
    - каждый Requirement (ADDED / MODIFIED / REMOVED) покрыт хотя бы одной подзадачей,
      RENAMED покрыт подзадачей или строкой матрицы «без работ»;
    - выполненные пункты tasks.md ([x]) только в разделе «Уже выполнено»,
      а в этом разделе только выполненные пункты;
    - артефакты, изменённые после parse_change.py --save-baseline, перечислены
      в разделе «Внесённые правки в артефакты»;
  предупреждения:
    - подзадач размера S больше трети (скилл предпочитает M и L);
    - пункт tasks.md не отражён ни в одной подзадаче или не имеет номера;
    - Requirement отсутствует в матрице покрытия, в матрице ссылка на несуществующую подзадачу;
    - базовые хэши не сохранены (проверка правок пропущена).

Код выхода: 0 нет ошибок, 1 есть ошибки, 2 нет change или subtasks.md.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import parse_change as pc  # noqa: E402

DEFAULT_MAX_SUBTASKS = 10

SUB_RE = re.compile(r"^###\s+(\d+(?:\.\d+)+)\.?\s+(.+?)\s*$")
OLD_SUB_RE = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s+\d+(?:\.\d+)+\s")
FIELD_RE = re.compile(r"^\*\*([^*]+?)\*\*\s*:?\s*(.*)$")
FIELD_MAP = {
    "размер": "size",
    "источник": "source",
    "зависит от": "deps",
    "затрагиваемые компоненты": "components",
    "описание": "desc",
    "что планируется сделать": "steps",
    "критерии приёмки": "accept",
    "критерии приемки": "accept",
}
LIST_FIELDS = {"steps": "Что планируется сделать", "accept": "Критерии приёмки"}
REQUIRED_TEXT = {"source": "Источник", "desc": "Описание"}
LIST_ITEM_RE = re.compile(r"^\s*\d+[.)]\s+(.+)$")
DONE_SECTION_RE = r"уже выполнено"
ID_RE = re.compile(r"^\d+(?:\.\d+)+$")
KNOWN_PREFIX_RE = re.compile(r"\b(spec|design|tasks|proposal):", re.I)
SPEC_REF_RE = re.compile(r"^spec:\s*([^/]+?)\s*/\s*Requirement:\s*(.+)$", re.I)
SECTION_REF_RE = re.compile(r"^(design|proposal):\s*§?\s*(.+)$", re.I)
TASK_REF_RE = re.compile(r"^tasks:\s*(.+)$", re.I)
TASK_ID_RE = re.compile(r"^\d+(?:\.\d+)*$")
EMPTY_VALUES = {"", "—", "-", "–", "нет"}
NO_WORK_RE = re.compile(r"без работ", re.I)


def norm(s):
    return re.sub(r"\s+", " ", s).strip().strip("`*").strip().lower()


def parse_subtasks(text):
    """Подзадачи вида «### N.M Название» с полями **Поле:** и нумерованными списками."""
    subs, cur, last, legacy = [], None, None, []
    for n, line in enumerate(text.splitlines(), 1):
        if OLD_SUB_RE.match(line):
            legacy.append(n)
        if re.match(r"^#{1,6}\s", line):
            m = SUB_RE.match(line)
            cur = None
            if m:
                cur = {"id": m.group(1), "title": m.group(2), "line": n, "fields": {}, "lists": {}}
                subs.append(cur)
            last = None
            continue
        if cur is None:
            continue
        fm = FIELD_RE.match(line)
        if fm:
            key = FIELD_MAP.get(norm(fm.group(1).rstrip(":")))
            if key:
                last = key
                cur["fields"][key] = fm.group(2).strip()
                if key in LIST_FIELDS:
                    cur["lists"][key] = []
                continue
        if not last or not line.strip():
            continue
        if last in LIST_FIELDS:
            im = LIST_ITEM_RE.match(line)
            if im:
                cur["lists"][last].append(im.group(1).strip())
            elif cur["lists"][last] and line.startswith((" ", "\t")):
                cur["lists"][last][-1] += " " + line.strip()
            else:
                cur["fields"][last] = (cur["fields"][last] + " " + line.strip()).strip()
        else:
            cur["fields"][last] = (cur["fields"][last] + " " + line.strip()).strip()
    return subs, legacy


def parse_done(text):
    """Строки раздела «Уже выполнено»: [(line_text, [task ids], [(cap, req)])]."""
    out = []
    for line in section_text(text, DONE_SECTION_RE).splitlines():
        if not re.match(r"^\s*[-*]\s+", line):
            continue
        tids = re.findall(r"tasks:\s*(\d+(?:\.\d+)*)", line, re.I)
        specs = [
            (norm(c), norm(r))
            for c, r in re.findall(r"spec:\s*([^/;()]+?)\s*/\s*Requirement:\s*([^;()]+)", line, re.I)
        ]
        out.append((line.strip(), tids, specs))
    return out


def section_text(text, title_re):
    """Текст раздела второго уровня, заголовок которого подходит под title_re."""
    out, inside = [], False
    for line in text.splitlines():
        if re.match(r"^##\s", line):
            inside = bool(re.search(title_re, line, re.I))
            continue
        if inside:
            out.append(line)
    return "\n".join(out)


def parse_matrix(text):
    rows = []
    for line in section_text(text, r"матрица покрытия").splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or set("".join(cells)) <= set("-: "):
            continue
        if norm(cells[0]) == "capability":
            continue
        rows.append({"cap": cells[0], "req": cells[1], "delta": cells[2], "subs": cells[3]})
    return rows


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


def check(change_dir, sub_path, max_subtasks=DEFAULT_MAX_SUBTASKS):
    """Вернуть (errors, warnings, stats)."""
    parsed = pc.parse_change(change_dir)
    text = sub_path.read_text(encoding="utf-8")
    subs, legacy = parse_subtasks(text)
    errors, warnings = [], []

    if legacy:
        errors.append(
            f"Подзадачи в старом формате «- [ ] N.M …» (строки {', '.join(map(str, legacy[:5]))}): "
            "оформите по templates/subtask-item.md (### N.M Название и поля)"
        )
    done_rows = parse_done(text)
    if not subs and not done_rows:
        errors.append("В файле не найдено ни одной подзадачи вида «### N.M Название»")

    # Реестры артефактов
    required = {}
    for spec in parsed["specs"]:
        for r in spec["requirements"]:
            required[(norm(spec["capability"]), norm(r["name"]))] = (
                f"{spec['capability']}/{r['name']} ({r['delta']})"
            )
    renamed = {}
    for spec in parsed["specs"]:
        for rn in spec["renamed"]:
            if rn.get("to"):
                renamed[(norm(spec["capability"]), norm(rn["to"]))] = (
                    f"{spec['capability']}/{rn['from']} → {rn['to']} (RENAMED)"
                )
    tasks = parsed["tasks"]
    task_done = {t["id"]: t["done"] for t in (tasks or []) if t["id"]}
    design_heads = (
        {norm(h["title"]) for h in parsed["design"]["headings"]} if parsed["design"] else None
    )
    proposal_heads = (
        {norm(k) for k in parsed["proposal"]["sections"]} if parsed["proposal"] else None
    )

    # Лимит и размеры
    if len(subs) > max_subtasks:
        errors.append(
            f"Подзадач {len(subs)}, лимит {max_subtasks}: укрупните подзадачи "
            f"(см. reference.md, 2.2–2.3) или согласуйте с пользователем другой лимит"
        )

    ids, seen = set(), set()
    covered, tasks_referenced = set(), set()
    graph = {}
    sizes = {"S": 0, "M": 0, "L": 0}
    for s in subs:
        label = f"{s['id']} (строка {s['line']})"
        if s["id"] in seen:
            errors.append(f"{label}: повторяющийся id")
        seen.add(s["id"])
        ids.add(s["id"])
        f = s["fields"]

        for key, name in REQUIRED_TEXT.items():
            if norm(f.get(key, "")) in EMPTY_VALUES:
                errors.append(f"{label}: пустое поле «{name}»")
        for key, name in LIST_FIELDS.items():
            if key not in f:
                errors.append(f"{label}: нет раздела «{name}»")
            elif not s["lists"].get(key):
                errors.append(f"{label}: «{name}» нужно оформить нумерованным списком (1. …, 2. …)")
        if "components" in f and norm(f["components"]) in EMPTY_VALUES:
            warnings.append(f"{label}: «Затрагиваемые компоненты» пустое — если артефакты ничего не называют, уберите строку")
        if "deps" not in f:
            errors.append(f"{label}: нет поля «Зависит от» (если зависимостей нет, укажите «—»)")
        size = f.get("size", "").strip().upper()
        if size in sizes:
            sizes[size] += 1
        else:
            errors.append(f"{label}: «Размер» должен быть S, M или L (сейчас: «{f.get('size', '')}»)")

        # Источник: ссылки через «;»
        src = f.get("source", "")
        if norm(src) not in EMPTY_VALUES and not KNOWN_PREFIX_RE.search(src):
            errors.append(f"{label}: в «Источник» нет ссылок вида spec:/design:/tasks:/proposal:")
        sub_task_refs = []
        for ref in (r.strip().rstrip(".") for r in src.split(";")):
            if not ref:
                continue
            if len(KNOWN_PREFIX_RE.findall(ref)) > 1:
                errors.append(f"{label}: ссылки «{ref}» нужно разделить через «;»")
                continue
            m = SPEC_REF_RE.match(ref)
            if m:
                key = (norm(m.group(1)), norm(m.group(2)))
                if key in required:
                    covered.add(key)
                elif key in renamed:
                    covered.add(key)
                else:
                    errors.append(f"{label}: ссылка на несуществующее требование {ref}")
                continue
            m = SECTION_REF_RE.match(ref)
            if m:
                kind, sec = m.group(1).lower(), m.group(2)
                heads = design_heads if kind == "design" else proposal_heads
                if heads is None:
                    errors.append(f"{label}: ссылка {ref}, но {kind}.md нет")
                elif norm(sec) not in heads:
                    errors.append(f"{label}: ссылка на несуществующий раздел {kind}.md: «{sec.strip()}»")
                continue
            m = TASK_REF_RE.match(ref)
            if m:
                for tid in re.split(r"\s*,\s*", m.group(1)):
                    tid = tid.strip().strip(".")
                    if not TASK_ID_RE.match(tid):
                        errors.append(f"{label}: не удалось разобрать ссылку tasks:{tid}")
                        continue
                    tasks_referenced.add(tid)
                    sub_task_refs.append(tid)
                    if tasks is not None and tid not in task_done:
                        errors.append(f"{label}: ссылка на несуществующий пункт tasks:{tid}")
                continue
            if KNOWN_PREFIX_RE.search(ref):
                errors.append(f"{label}: не удалось разобрать ссылку «{ref}»")

        # Выполненные пункты tasks.md не должны быть в подзадачах
        done_in_sub = [tid for tid in sub_task_refs if task_done.get(tid)]
        if done_in_sub:
            errors.append(
                f"{label}: пункты tasks.md {', '.join(done_in_sub)} уже выполнены ([x]); "
                "перенесите их в раздел «Уже выполнено», а не в подзадачу"
            )

        # Зависимости: только id через запятую
        deps_raw = f.get("deps", "")
        deps = []
        if "deps" in f and norm(deps_raw) not in EMPTY_VALUES:
            for tok in re.split(r"\s*[,;]\s*", deps_raw.strip()):
                if ID_RE.match(tok):
                    deps.append(tok)
                elif tok:
                    errors.append(f"{label}: в «Зависит от» не id подзадачи: «{tok}»")
        graph[s["id"]] = deps

    for sid, deps in graph.items():
        for d in deps:
            if d == sid:
                errors.append(f"{sid}: зависит от самой себя")
            elif d not in ids:
                errors.append(f"{sid}: зависит от несуществующей подзадачи {d}")
    cycle = find_cycle(graph)
    if cycle:
        errors.append("Цикл в зависимостях: " + " → ".join(cycle))

    # Раздел «Уже выполнено»
    for line, tids, specs in done_rows:
        if not tids:
            errors.append(f"«Уже выполнено»: нет ссылки tasks:<номер> в строке «{line}»")
        for tid in tids:
            tasks_referenced.add(tid)
            if tasks is not None and tid not in task_done:
                errors.append(f"«Уже выполнено»: несуществующий пункт tasks:{tid}")
            elif task_done.get(tid) is False:
                errors.append(f"«Уже выполнено»: пункт tasks:{tid} в tasks.md не отмечен [x]")
        for key in specs:
            if key in required or key in renamed:
                covered.add(key)
            else:
                errors.append(f"«Уже выполнено»: ссылка на несуществующее требование в строке «{line}»")

    if subs and sizes["S"] > 1 and sizes["S"] * 3 > len(subs):
        warnings.append(
            f"Подзадач размера S: {sizes['S']} из {len(subs)}. Скилл предпочитает M и L: "
            "объедините мелкие подзадачи со связанными (reference.md, 2.3)"
        )

    # Матрица покрытия
    matrix = parse_matrix(text)
    matrix_keys = {(norm(r["cap"]), norm(r["req"])) for r in matrix}
    no_work = {(norm(r["cap"]), norm(r["req"])) for r in matrix if NO_WORK_RE.search(r["subs"])}
    for r in matrix:
        for sid in re.findall(r"(?<![\w:.])\d+(?:\.\d+)+", r["subs"]):
            if sid not in ids:
                warnings.append(f"Матрица покрытия: {r['cap']}/{r['req']} ссылается на несуществующую подзадачу {sid}")

    # Покрытие требований
    uncovered = [label for key, label in required.items() if key not in covered]
    for label in uncovered:
        errors.append(f"Требование не покрыто ни одной подзадачей: {label}")
    for key, label in renamed.items():
        if key not in covered and key not in no_work:
            errors.append(f"Переименование не отражено: {label}; сошлитесь на новое имя в подзадаче или отметьте «без работ» в матрице")
    if matrix:
        for key, label in list(required.items()) + list(renamed.items()):
            if key not in matrix_keys:
                warnings.append(f"Нет в матрице покрытия: {label}")
    elif required:
        warnings.append("Матрица покрытия не найдена или пуста")

    # Покрытие пунктов tasks.md
    for t in tasks or []:
        if not t["id"]:
            warnings.append(f"Пункт tasks.md без номера (строка {t['line']}) «{t['text']}»: на него нельзя сослаться, проверьте покрытие вручную")
        elif t["id"] not in tasks_referenced:
            warnings.append(f"Пункт tasks.md {t['id']} «{t['text']}» не отражён ни в одной подзадаче")

    # Правки артефактов относительно базовых хэшей
    bp = pc.baseline_path(change_dir)
    if bp.is_file():
        base = json.loads(bp.read_text(encoding="utf-8"))["files"]
        now = pc.file_hashes(parsed)
        changed = sorted(k for k in set(base) | set(now) if base.get(k) != now.get(k))
        edits = section_text(text, r"внесённые правки|внесенные правки").lower()
        for name in changed:
            parts = Path(name).parts
            mentioned = name.lower() in edits or (
                len(parts) > 2 and parts[-2].lower() in edits and "spec" in edits
            )
            if not mentioned:
                errors.append(
                    f"Артефакт {name} изменён после инвентаризации, но не указан в разделе "
                    "«Внесённые правки в артефакты» (правки допустимы только с подтверждения пользователя)"
                )
    else:
        warnings.append("Базовые хэши не сохранены (parse_change.py --save-baseline): проверка правок артефактов пропущена")

    stats = {
        "subtasks": len(subs),
        "sizes": sizes,
        "required": len(required),
        "covered": len(required) - len(uncovered),
        "change_id": parsed["change_id"],
        "done": sum(len(r[1]) for r in done_rows),
    }
    return errors, warnings, stats


def main():
    ap = argparse.ArgumentParser(description="Проверка subtasks.md против артефактов change")
    ap.add_argument("change", help="id change или путь к каталогу")
    ap.add_argument("--file", default="subtasks.md", help="имя файла подзадач (по умолчанию subtasks.md)")
    ap.add_argument("--root", default=".", help="корень репозитория (по умолчанию текущий каталог)")
    ap.add_argument(
        "--max-subtasks",
        type=int,
        default=DEFAULT_MAX_SUBTASKS,
        help=f"максимум подзадач (по умолчанию {DEFAULT_MAX_SUBTASKS})",
    )
    args = ap.parse_args()
    pc.ROOT = Path(args.root)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    change_dir = pc.resolve_change(args.change)
    if change_dir is None:
        print(f"Change не найден: {args.change} (запустите из корня репозитория или укажите --root)", file=sys.stderr)
        return 2
    sub_path = change_dir / args.file
    if not sub_path.is_file():
        print(f"Файл не найден: {sub_path}", file=sys.stderr)
        return 2

    errors, warnings, st = check(change_dir, sub_path, args.max_subtasks)

    sz = st["sizes"]
    print(f"Change: {st['change_id']}  Файл: {sub_path.name}")
    print(
        f"Подзадач: {st['subtasks']} из {args.max_subtasks} (S: {sz['S']}, M: {sz['M']}, L: {sz['L']}); "
        f"уже выполнено пунктов tasks.md: {st['done']}; требований: {st['required']}, покрыто: {st['covered']}"
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
