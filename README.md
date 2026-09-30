# change-subtasks

Скилл для агентов (Qwen Code, Claude Code и др.): выделяет из OpenSpec change укрупнённые подзадачи (не больше 10, преимущественно M и L) и записывает их в `openspec/changes/<change-id>/subtasks.md`.

Инструкции для агента: [SKILL.md](SKILL.md). Установка: скопируйте каталог в `.qwen/skills/change-subtasks/` (или `.claude/skills/change-subtasks/`) проекта либо в аналогичный каталог в домашней папке.

## Скрипты

- `scripts/parse_change.py <change> [--root DIR] [--save-baseline]`: разбор артефактов в JSON; `--list` показывает активные change.
- `scripts/check_coverage.py <change> [--root DIR] [--max-subtasks N]`: проверка `subtasks.md` (покрытие, лимит, ссылки, статусы, неподтверждённые правки артефактов).

## Тесты

```
python -m pytest tests
```
