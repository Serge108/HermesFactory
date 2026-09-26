# Эксплуатация Hermes Factory

Эта инструкция рассчитана на первый запуск без знания внутреннего устройства Factory. Все команды выполняются в PowerShell из каталога `F:\HermesFactory`.

## 1. Что чем управляет

Hermes Factory не запускает модели самостоятельно и не хранит отдельную очередь.

- `rig.yaml` описывает проект, роли, профили и правила.
- mission YAML описывает конкретную работу.
- Hermes Factory проверяет YAML и создаёт граф задач.
- Hermes Kanban хранит задачи, зависимости, запуски, комментарии и артефакты.
- Hermes gateway запускает назначенные профили.
- Hermes dashboard и `kanban watch` показывают работу.
- `hermes-factory status` проверяет здоровье и доказательства выполнения.
- `hermes-factory report` рассчитывает статистику.
- `hermes-factory reconcile` создаёт recovery-задачу для тайм-аута и применяет проверенный план замены вне capability fence исполнителя.
- `hermes-factory stall-taxonomy` показывает стабильные классы остановок и контракты восстановления.

Короткая схема:

```text
rig.yaml + mission.yaml
        |
        v
hermes-factory mission apply
        |
        v
Hermes Kanban board
        |
        v
implementation -> review -> local commit -> coordinator acceptance
```

## 2. Подготовить Factory

Откройте PowerShell:

```powershell
Set-Location F:\HermesFactory
uv sync --extra dev
uv run hermes-factory version
```

Ожидаемый результат:

```text
hermes-factory 0.1.0
```

Проверьте Hermes:

```powershell
hermes --version
hermes doctor
```

Если `uv`, `hermes` или Python не находятся, сначала исправьте установку Hermes. Factory без рабочего Hermes не выполняет задачи.

## 3. Подготовить профили исполнителей

Посмотрите существующие профили:

```powershell
hermes profile list
```

В примере используются три профиля:

| Профиль | Роль |
|---|---|
| `default` | координатор и человек, принимающий результат |
| `builder` | реализация |
| `reviewer` | независимая проверка |

Если `builder` и `reviewer` ещё не существуют, создайте их из `default`:

```powershell
hermes profile create builder `
  --clone-from default `
  --description "Implements small scoped tasks, runs tests, and records proof."

hermes profile create reviewer `
  --clone-from default `
  --description "Independently reviews completed work, reruns proof commands, and reports defects."
```

Повторно проверьте:

```powershell
hermes profile list
hermes profile show builder
hermes profile show reviewer
```

Профиль должен иметь рабочую модель и авторизацию. При проблемах запустите:

```powershell
hermes -p builder doctor
hermes -p reviewer doctor
```

## 4. Создать рабочий rig

Скопируйте пример:

```powershell
Copy-Item .hermes\rig.example.yaml .hermes\rig.yaml
notepad .hermes\rig.yaml
```

Минимальный рабочий вариант:

```yaml
version: 1
project:
  slug: hermes-factory
  name: Hermes Factory
  cwd: F:\HermesFactory
board:
  slug: hermes-factory
  name: Hermes Factory
seats:
  coordinator:
    profile: default
    role: planning_and_acceptance
    skills: []
  builder:
    profile: builder
    role: implementation
    skills: []
  reviewer:
    profile: reviewer
    role: independent_review
    skills: []
workflows:
  delivery:
    implementation_seat: builder
    review_seat: reviewer
    acceptance_seat: coordinator
policies:
  require_proof: true
  human_acceptance: true
  max_retries: 2
  max_runtime: 30m
  completion_contract: local-only
  decision_authority: coordinator
  git_commit_policy: reviewed-slices
```

Что обычно нужно менять:

- `project.slug`: короткий идентификатор проекта.
- `project.name`: отображаемое имя.
- `project.cwd`: абсолютный путь к репозиторию.
- `board.slug`: уникальный идентификатор Kanban-доски.
- `seats.*.profile`: реальные имена профилей Hermes.
- `max_retries`: число автоматических повторов.
- `max_runtime`: максимальная длительность одного запуска.
- `decision_authority: coordinator`: координатор принимает решения внутри уже принятой миссии и не ждёт человека. Значение `human` сохраняет настоящий операторский gate.
- `git_commit_policy: reviewed-slices`: после независимого review создаётся отдельная задача локального commit. `push`, `reset`, `rebase` и публикация секретов запрещены.

Чтобы каждая карточка текущих и будущих миссий автоматически подписывалась на Telegram, добавьте в rig:

```yaml
notifications:
  task_subscriptions:
    - platform: telegram
      chat_id: "123456789"
      user_id: "123456789"
      chat_type: dm
      notifier_profile: default
      delivery_mode: notify
```

`mission apply` идемпотентно вызывает `notify-subscribe` для каждой созданной или повторно найденной карточки. `notify` отправляет пассивные сообщения о terminal-событиях; `notify+wake` дополнительно пробуждает профиль назначения.

Путь с пробелами допустим. В YAML его можно записать так:

```yaml
cwd: C:\Users\user\Documents\My Project
```

## 5. Проверить и применить rig

Сначала только проверка:

```powershell
uv run hermes-factory validate --rig .hermes\rig.yaml
```

Если команда сообщает ошибку, не переходите дальше. Исправьте конкретное поле в YAML.

После успешной проверки создайте или повторно используйте Kanban-доску:

```powershell
uv run hermes-factory apply --rig .hermes\rig.yaml
```

Проверьте доску:

```powershell
hermes kanban boards list --json --all
hermes kanban --board hermes-factory list
```

Повторный `apply` безопасен. Factory использует существующую доску, а не создаёт копию.

## 6. Создать mission

Скопируйте пример:

```powershell
Copy-Item .hermes\missions\example.yaml .hermes\missions\my-first-mission.yaml
notepad .hermes\missions\my-first-mission.yaml
```

Пример миссии для небольшой доработки:

```yaml
version: 1
id: add-json-export
title: Add JSON export
intent: Add JSON export without changing existing text output.
workflow: delivery
human_acceptance: true
acceptance:
  - The CLI accepts the --json flag.
  - Existing text output remains unchanged.
  - The full test suite passes.
slices:
  - id: implement
    title: Implement JSON export
    goal: Add the smallest code change that produces valid JSON output.
    depends_on: []
    acceptance:
      - A failing test is added before production code.
      - The focused test passes.
      - The full test suite passes.
    proof:
      commands:
        - uv run pytest -q
      artifacts: []
      changed_files_required: true
```

В строке `title` не должно быть лишнего отступа. Правильный вариант:

```yaml
title: Add JSON export
```

Основные поля:

- `id`: уникальный идентификатор миссии.
- `intent`: зачем выполняется работа и где проходит граница scope.
- `workflow`: имя workflow из `rig.yaml`.
- `acceptance`: условия завершения всей миссии.
- `slices`: отдельные части работы.
- `depends_on`: зависимости между slices.
- `proof.commands`: команды, которые агент обязан выполнить и записать.
- `proof.artifacts`: имена файлов, которые агент обязан приложить к карточке.
- `changed_files_required`: требуется ли список изменённых файлов.

Имена артефактов должны быть простыми именами файлов:

```yaml
artifacts:
  - report.json
  - screenshot.png
```

Нельзя указывать каталоги:

```yaml
artifacts:
  - reports/report.json
```

Hermes хранит attachments по имени файла, поэтому Factory отклонит путь с каталогом.

## 7. Посмотреть план до создания задач

```powershell
uv run hermes-factory mission plan `
  .hermes\missions\my-first-mission.yaml `
  --rig .hermes\rig.yaml
```

JSON-вариант:

```powershell
uv run hermes-factory mission plan `
  .hermes\missions\my-first-mission.yaml `
  --rig .hermes\rig.yaml `
  --json
```

Проверьте:

- количество implementation-карточек;
- наличие отдельной review-карточки для каждого slice;
- профиль каждой карточки;
- зависимости между карточками;
- наличие финальной acceptance-карточки;
- обязательные команды и артефакты.

`mission plan` ничего не записывает в Kanban.

## 8. Создать задачи миссии

```powershell
uv run hermes-factory mission apply `
  .hermes\missions\my-first-mission.yaml `
  --rig .hermes\rig.yaml `
  --json
```

Команда вернёт идентификаторы созданных задач. Сохраните вывод, если хотите быстро открывать карточки вручную.

Проверьте доску:

```powershell
hermes kanban --board hermes-factory list
```

Ожидаемая структура для одного slice:

```text
implementation task
        |
        v
review task
        |
        v
local commit task
        |
        v
coordinator acceptance task
```

Повторный `mission apply` не должен создавать дубликаты. Factory использует стабильные idempotency keys.

## 9. Проверить диспетчеризацию без запуска

```powershell
hermes kanban --board hermes-factory dispatch --dry-run --json
```

Проверьте, что:

- implementation-карточка готова к запуску;
- карточка назначена профилю `builder`;
- review ещё ждёт завершения implementation;
- acceptance заблокирована до человеческого решения.

Если `dry-run` ничего не предлагает, посмотрите статусы:

```powershell
hermes kanban --board hermes-factory list --sort status
hermes kanban --board hermes-factory diagnostics
```

## 10. Запустить выполнение

Один проход диспетчера:

```powershell
hermes kanban --board hermes-factory dispatch --max 2 --json
```

Если gateway уже запущен с Kanban dispatcher, он может подхватывать следующие готовые карточки автоматически. Команда `dispatch` полезна для явного ручного запуска и диагностики.

Проверить gateway:

```powershell
hermes gateway status
```

## 11. Наблюдать за работой

### Вариант A. Веб-интерфейс

```powershell
hermes dashboard --no-open
```

Откройте адрес, который напечатает команда. На Kanban-доске видны карточки, зависимости, комментарии, запуски, блокировки и handoff.

### Вариант B. Поток событий в терминале

Откройте второе окно PowerShell:

```powershell
Set-Location F:\HermesFactory
hermes kanban --board hermes-factory watch
```

Только важные события:

```powershell
hermes kanban --board hermes-factory watch `
  --kinds completed,blocked,gave_up,crashed,timed_out
```

Только builder:

```powershell
hermes kanban --board hermes-factory watch --assignee builder
```

### Вариант C. Одна карточка

```powershell
hermes kanban --board hermes-factory show <task-id>
hermes kanban --board hermes-factory tail <task-id>
hermes kanban --board hermes-factory runs <task-id> --json
```

`tail` показывает новые события конкретной карточки.

## 12. Понять нормальный lifecycle

### Тайм-аут и декомпозиция

При `max_retries: 1` первый неуспешный запуск переводит карточку в `blocked`. Hermes отвечает за остановку процесса и событие `gave_up`; Factory отвечает за следующую процессную реакцию:

```powershell
uv run hermes-factory reconcile `
  --rig .hermes\rig.yaml `
  --dispatch `
  --json
```

Команда:

1. находит только заблокированные карточки с `created_by: hermes-factory`;
2. подтверждает в журнале событие `gave_up` с причиной `timed_out`;
3. создаёт или повторно находит одну high-priority карточку координатора;
4. использует стабильный idempotency key, поэтому повторный запуск не создаёт дубликат;
5. читает завершённые recovery-карточки со структурированным планом;
6. проверяет replacement-граф, снимает только заявленные stale edges, архивирует и удаляет superseded-карточки, затем перечитывает граф;
7. при `--dispatch` запускает готовую recovery-карточку или освобождённую downstream-работу через штатный Hermes dispatcher.

Координатор должен изучить журнал и незавершённый diff, декомпозировать только оставшийся объём, создать последовательные пары implementation/review, добавить replacement edges и завершить recovery-карточку с точными `replacement_task_ids`, `rewires`, `superseded_task_ids` и пустым `limitations`. Он не вызывает `unlink`, `archive` или `purge`: эти команды запрещены delegated child context и выполняются доверенным no-agent reconciler после полной проверки плана.

Ручные продуктовые блокировки, acceptance-карточки и карточки самого reconciler команда не декомпозирует.

### Что reconciler проверяет перед первой разрушительной операцией

Каждый запуск заново читает авторитетные `show --json` и `list --archived --json` записи и отклоняет план, если:

- replacement-задача создана не координаторским профилем, не имеет ровно одного события `created` с `creator_task_id` этой recovery-карточки или отсутствует в её завершающем манифесте `verified_cards`;
- replacement-задача уже архивна;
- цепочка implementation/review, дочерние задачи финального review или набор объявленных `rewires` не совпадают точно;
- метаданные плана и `verified_cards` относятся к разным запускам: метаданные берутся из последнего успешного run, а `verified_cards` — из единственного события `completed` с тем же `run_id`;
- уже снятая внешняя связь не подтверждена собственным событием Hermes `unlinked` для этого ребра со временем не раньше завершения recovery — независимо от того, существует ли старый родитель ещё;
- отсутствующий в доске superseded id не отключает проверку связности: каждый оставшийся superseded-узел обязан через свои живые parent-связи восходить к recovery-target, иначе план отклоняется до первой мутации.
- сама recovery-карточка или живой recovery-target не имеют ровно одного события `created` с пустым `creator_task_id`, то есть были созданы внутри worker-сессии;
- живой recovery-target не несёт собственного события Hermes `gave_up` с `trigger_outcome: timed_out`: право на разрушение заново выводится из таймаута, а не из тела карточки;
- recovery-target отсутствует в доске, а какие-то superseded-карточки ещё живы: без живой цели precondition перепроверить нельзя, поэтому план отклоняется.

`created_by` сам по себе не является границей доверия: `hermes kanban create --created-by` — свободный флаг CLI. Доверие даёт связка авторитетного `created`-события (`creator_task_id` заполняется Hermes только для dispatcher-owned worker context) с id этой recovery-карточки и её манифестом `verified_cards`, а не имя профиля в поле. По той же причине recovery-карточка и её цель проверяются на авторство: карточка, созданная внутри worker-сессии, не может запускать разрушение, а цель обязана быть Factory-карточкой с записью таймаута.

Та же проверка авторства применяется ко всем superseded-карточкам: разрушаемый набор не может выбираться по свободному полю `created_by`. Rewire-дети проверяются как живые зависимости (связность, идентичность, подтверждение `unlinked`), но не как авторство: они перелинковываются, а не уничтожаются.

**Границы этой гарантии.** Пустой `creator_task_id` доказывает, что в создавшем процессе не был выставлен `HERMES_KANBAN_TASK`; Hermes заполняет его только для dispatcher-owned worker context, а сама изоляция worker-сессий кооперативная (маркер `HERMES_DELEGATED_CHILD_CONTEXT`, и документация Hermes прямо называет её не средством удержания кода с прямым доступом к SQLite). Worker, который сам снимает эти переменные, и любой процесс с прямым доступом к базе доски — вне модели угроз. Для более сильной гарантии нужен отдельный Factory-артефакт, привязанный к recovery-карточке, а не к окружению создателя.

Проверка тел карточек структурно тотальна: пустая обязательная секция сгенерированного тела (`# Project`, `# Mission`, `# Slice`, `# Acceptance`, `# Proof required`, `# Scope boundaries`, `# Handoff`) отклоняется как доменная ошибка, поэтому тело, написанное worker'ом, не может вызвать `IndexError`. Дополнительно каждый отказ изолирован на уровне карточки: проход ловит любое исключение при разборе одной recovery-карточки, продолжает остальные и сообщает обо всех отказах.

Остаточное ограничение: связь, видимую только со стороны ребёнка (ребёнок перечисляет родителя, а список `children` родителя его не содержит), reconciler видит лишь у уже прочитанных карточек — `show --json` родителя остаётся единственной поверхностью со списком детей. Hermes выводит оба направления из одной строки `task_links`, поэтому такая связь означает повреждение таблицы связей, а не действие worker'а; это ограничение принято осознанно.

Комментарии `recovery-validated` и `recovery-finalized` — только аудит. Любой профиль может опубликовать комментарий с любым автором, поэтому ни один запуск не пропускает проверку и не выполняет разрушительные операции по комментарию. Повторный запуск перепроверяет живой граф перед каждой фазой (`unlink`, `archive`, `archive --rm`) и завершается без изменений, если всё уже применено.

Hermes удаляет вместе с задачей её строки связей, поэтому superseded-карточки удаляются от потомков к предкам: падение между двумя удалениями оставляет выжившую цепочку проверяемой, и следующий проход завершает работу. Если цель отсутствует, а superseded-карточки ещё живы, порядок вывести не из чего, и проход отказывается удалять что-либо.

Отклонённый план одной recovery-карточки не блокирует остальные: проход финализирует независимые карточки, затем выполняет фазу dispatch для уже освобождённой работы и только после этого завершается ненулевым кодом со списком отклонённых. Отказ разбора не должен удерживать готовую работу.

Каждый класс остановки в `stall_taxonomy` называет существующий тест в поле `regression_test`; тест `test_every_stall_class_names_an_existing_regression_test` проверяет это машинно, поэтому новый класс без реального regression-теста не проходит CI.

### Автоматический watchdog через Hermes cron

Reconciler не является отдельным Windows-сервисом. Его запускает устойчивый Hermes cron в режиме `--no-agent`; скрипт должен завершаться быстро и ничего не печатать, если изменений нет.

Создайте Python-скрипт в каталоге `scripts` активного Hermes profile. Путь профиля получите командой `hermes config path`. Пример содержимого для `F:\HermesFactory`:

```python
import json
import subprocess

command = [
    "uv", "run", "--project", "F:/HermesFactory",
    "hermes-factory", "reconcile",
    "--rig", "F:/example-project/.hermes/rig.yaml",
    "--dispatch", "--json",
]
completed = subprocess.run(command, text=True, capture_output=True, check=False)
if completed.returncode:
    raise SystemExit(completed.stderr or completed.stdout or completed.returncode)
payload = json.loads(completed.stdout)
if payload["recovery_task_ids"] or payload["finalized_recovery_task_ids"] or payload["dispatched"]:
    print(json.dumps(payload, ensure_ascii=False))
```

Зарегистрируйте watchdog раз в минуту:

```powershell
hermes cron create "1m" `
  --name "Hermes Factory reconcile: example-project" `
  --script "hermes-factory-example-reconcile.py" `
  --no-agent `
  --workdir "F:\HermesFactory" `
  --deliver local
```

Проверьте регистрацию и один ручной проход:

```powershell
hermes cron list
hermes cron run <job-id>
```

Cron обнаруживает timeout, создаёт recovery-карточку и применяет завершённые планы. Декомпозицию выполняет обычная координаторская Hermes-сессия с лимитом из `rig.yaml`; привилегированную очистку графа выполняет только no-agent reconciler.

### Типизация остановок

Покажите реестр в текстовом или JSON-виде:

```powershell
uv run hermes-factory stall-taxonomy
uv run hermes-factory stall-taxonomy --json
```

Основные `reason_code`: `normal_dependency_wait`, `awaiting_dispatch`, `stale_runner`, `worker_timeout`, `stale_dependency_edge`, `review_rejected`, `human_gate`, `corrupt_state`, `recovery_coordinator_insufficient_kanban_authority`, `unknown_operational_stall`. Для каждого класса фиксируются detection, cause, recovery, authority, verification, fallback и regression test. Новая остановка не закрывается единичным ручным ремонтом: сначала добавляется воспроизводимый тест, затем detector и безопасная процедура восстановления.

### Implementation

Builder должен:

1. Прочитать intent, acceptance и scope.
2. Добавить падающий тест.
3. Внести минимальное изменение.
4. Выполнить обязательные команды.
5. Приложить обязательные артефакты.
6. Завершить implementation-карточку.

Completion metadata должна иметь такой вид:

```json
{
  "changed_files": ["src/package/file.py"],
  "commands": [
    {"command": "uv run pytest -q", "exit_code": 0}
  ],
  "limitations": []
}
```

После completion Hermes освобождает зависимую review-карточку.

### Review

Reviewer должен независимо:

1. Прочитать результат implementation.
2. Проверить изменённые файлы.
3. Повторить proof-команды.
4. Записать reviewer summary.
5. Завершить review или заблокировать её с конкретными замечаниями.

Review без summary считается ошибкой proof-аудита.

### Acceptance authority

При `decision_authority: coordinator` финальная acceptance-карточка назначается координатору. Он фиксирует решение и завершает миссию без ожидания человека. При `decision_authority: human` карточка остаётся заблокированной до решения оператора.

Посмотрите карточку:

```powershell
hermes kanban --board hermes-factory show <acceptance-task-id>
```

Для human-gate, если результат принят:

```powershell
hermes kanban --board hermes-factory complete <acceptance-task-id> `
  --result "Accepted by operator" `
  --summary "Acceptance criteria reviewed and approved."
```

Если результат не принят, добавьте причину и оставьте задачу заблокированной или верните нужную карточку в работу через dashboard.

## 13. Приложить обязательный артефакт вручную

Если mission требует `report.json`:

```powershell
hermes kanban --board hermes-factory attach `
  <task-id> `
  F:\HermesFactory\report.json `
  --name report.json `
  --author user
```

Проверьте attachment:

```powershell
hermes kanban --board hermes-factory attachments <task-id> --json
```

Путь, записанный только в completion metadata, не считается доказательством. Файл должен присутствовать в Kanban attachments.

## 14. Проверить здоровье Factory

```powershell
uv run hermes-factory status --rig .hermes\rig.yaml
```

JSON-вариант:

```powershell
uv run hermes-factory status --rig .hermes\rig.yaml --json
```

`status` проверяет:

- успешный завершённый run;
- обязательные proof-команды;
- `exit_code` каждой команды;
- изменённые файлы;
- `limitations`;
- обязательные attachments;
- reviewer summary;
- diagnostics Hermes;
- целостность audit contract.

Коды возврата:

- `0`: critical и error не найдены;
- `1`: найдена проблема выполнения или proof;
- `2`: Factory не смогла прочитать конфигурацию или выполнить Hermes CLI.

Используйте `status` как обязательную проверку перед человеческим acceptance.

## 15. Посмотреть статистику

Текстовый отчёт:

```powershell
uv run hermes-factory report --rig .hermes\rig.yaml
```

JSON:

```powershell
uv run hermes-factory report --rig .hermes\rig.yaml --json
```

Factory считает:

- задачи по статусам;
- завершённые задачи;
- throughput в день;
- proof rate;
- rework rate;
- возвраты `changes_requested`;
- неуспешные runs;
- общее количество runs;
- медианное и максимальное lead time;
- открытую работу по seat;
- возраст самой старой ready-задачи.

`report` всегда возвращает код `0`. Он показывает данные, но не принимает решение за оператора.

## 16. Сохранять статистику для истории

Factory пока строит моментальный отчёт. Она не сохраняет ежедневные snapshots автоматически.

Создайте каталог:

```powershell
New-Item -ItemType Directory -Force .hermes\reports | Out-Null
```

Сохраните один snapshot:

```powershell
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
uv run hermes-factory report `
  --rig .hermes\rig.yaml `
  --json |
  Set-Content -Encoding utf8 ".hermes\reports\report-$stamp.json"
```

Сохраните health snapshot:

```powershell
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
uv run hermes-factory status `
  --rig .hermes\rig.yaml `
  --json |
  Set-Content -Encoding utf8 ".hermes\reports\status-$stamp.json"
```

Для анализа также можно сохранять сырые данные Hermes:

```powershell
hermes kanban --board hermes-factory stats --json
hermes kanban --board hermes-factory list --json
hermes kanban --board hermes-factory diagnostics --json
hermes kanban --board hermes-factory runs <task-id> --json
```

Не коммитьте большой поток snapshots без отдельного решения. Для регулярного сбора лучше добавить `.hermes/reports/` в `.gitignore` или хранить отчёты вне репозитория.

## 17. Разобрать типичные проблемы

### Factory сообщает, что профиль не существует

Проверьте имя:

```powershell
hermes profile list
```

Исправьте `seats.<seat>.profile` в `rig.yaml` или создайте профиль.

### Карточка не запускается

```powershell
hermes kanban --board hermes-factory diagnostics
hermes kanban --board hermes-factory dispatch --dry-run --json
hermes kanban --board hermes-factory list --sort status
```

Частые причины:

- неверный assignee;
- профиль не авторизован;
- родительская карточка ещё не завершена;
- задача заблокирована;
- исчерпан лимит повторов;
- gateway не работает.

### Агент упал или превысил время

```powershell
hermes kanban --board hermes-factory runs <task-id> --json
hermes kanban --board hermes-factory diagnostics --task <task-id>
hermes kanban --board hermes-factory show <task-id>
```

Инфраструктурную ошибку можно исправить и разблокировать вручную. Тайм-аут Factory-карточки при политике декомпозиции не разблокируйте: запустите reconciler.

Для ручного восстановления после инфраструктурной ошибки:

```powershell
hermes kanban --board hermes-factory unblock <task-id> `
  --reason "Credentials fixed; retry approved."
```

### `status` сообщает `missing-proof-command`

Проверьте последний успешный run:

```powershell
hermes kanban --board hermes-factory runs <task-id> --json
```

Команда в metadata должна дословно совпадать с `proof.commands` из mission, а `exit_code` должен быть числом `0`.

### `status` сообщает `missing-proof-artifact`

```powershell
hermes kanban --board hermes-factory attachments <task-id> --json
```

Проверьте точное имя файла. `report.json` и `REPORT.JSON` нельзя использовать как два разных обязательных артефакта.

### `status` сообщает `review-without-summary`

Reviewer завершил карточку без summary. Нужно повторить review и сохранить независимое заключение в terminal lifecycle transition.

### Review не освобождается

Implementation должна быть завершена через completion. Не используйте same-card `request-review`, когда Factory уже создала отдельную review-карточку.

## 18. Ежедневный короткий сценарий

В начале работы:

```powershell
Set-Location F:\HermesFactory
uv run hermes-factory validate --rig .hermes\rig.yaml
hermes gateway status
hermes kanban --board hermes-factory diagnostics
```

Создать новую миссию:

```powershell
uv run hermes-factory mission plan `
  .hermes\missions\my-mission.yaml `
  --rig .hermes\rig.yaml

uv run hermes-factory mission apply `
  .hermes\missions\my-mission.yaml `
  --rig .hermes\rig.yaml `
  --json
```

Перед запуском:

```powershell
hermes kanban --board hermes-factory dispatch --dry-run --json
```

Запуск и наблюдение:

```powershell
hermes kanban --board hermes-factory dispatch --max 2 --json
hermes kanban --board hermes-factory watch
```

Перед приёмкой:

```powershell
uv run hermes-factory status --rig .hermes\rig.yaml
uv run hermes-factory report --rig .hermes\rig.yaml
```

## 19. Самая короткая шпаргалка

```powershell
# Проверить конфигурацию
uv run hermes-factory validate --rig .hermes\rig.yaml

# Создать или обновить доску
uv run hermes-factory apply --rig .hermes\rig.yaml

# Посмотреть будущий граф задач
uv run hermes-factory mission plan .hermes\missions\mission.yaml --rig .hermes\rig.yaml

# Создать задачи
uv run hermes-factory mission apply .hermes\missions\mission.yaml --rig .hermes\rig.yaml --json

# Проверить, что будет запущено
hermes kanban --board hermes-factory dispatch --dry-run --json

# Запустить готовые задачи
hermes kanban --board hermes-factory dispatch --max 2 --json

# Смотреть события
hermes kanban --board hermes-factory watch

# Смотреть проблемы
hermes kanban --board hermes-factory diagnostics

# Проверить proof и здоровье
uv run hermes-factory status --rig .hermes\rig.yaml

# Получить метрики
uv run hermes-factory report --rig .hermes\rig.yaml --json
```

## 20. Безопасные правила

1. Всегда запускайте `mission plan` перед `mission apply`.
2. Всегда запускайте `dispatch --dry-run` перед первым реальным dispatch новой миссии.
3. Не принимайте миссию, пока `hermes-factory status` возвращает ошибку.
4. Не считайте путь в сообщении агента доказательством артефакта. Проверяйте Kanban attachments.
5. Не завершайте acceptance-карточку, пока не прочитали implementation и review summaries.
6. Не удаляйте Kanban-доску для очистки. Архивируйте её через `hermes kanban boards rm <slug>`.
7. Не завершайте процессы вручную, если они не были запущены и записаны Hermes Factory или Hermes.
