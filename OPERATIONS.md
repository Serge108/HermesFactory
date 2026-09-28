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
- `hermes-factory reconcile` регистрирует board-level инцидент, если после grace period на доске нет `running`, но остаётся `todo` или recoverable `blocked`; отдельно создаёт recovery-задачи для task-level остановок и применяет проверенные планы замены вне capability fence исполнителя.
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
  coordinator_runtime: 45m
  completion_contract: local-only
  decision_authority: coordinator
  git_commit_policy: reviewed-slices
```

### Миграция rig v1 на v2

Версия 2 добавляет обязательный pre-builder этап. Создайте отдельный профиль decomposer, добавьте seat и укажите его в workflow. Builder и reviewer остаются отдельными профилями.

Текущие команды Hermes для профиля DeepSeek через `opencode-go` с reasoning `medium`:

```powershell
hermes profile create decomposer `
  --clone-from default `
  --description "Partitions architectural slices into bounded implementation packets without mutating the Kanban graph."
hermes -p decomposer config set model.default deepseek-v4.1-flash
hermes -p decomposer config set model.provider opencode-go
hermes -p decomposer config set agent.reasoning_effort medium --force
hermes -p decomposer config get model.default
hermes -p decomposer config get model.provider
hermes -p decomposer config get agent.reasoning_effort
hermes -p decomposer doctor
```

`--force` нужен текущему `config set`, потому что валидатор CLI ещё не перечисляет `agent.reasoning_effort`, хотя runtime читает это поле. Ожидаемые значения read-back: `deepseek-v4.1-flash`, `opencode-go`, `medium`.

Измените rig:

```yaml
version: 2
seats:
  decomposer:
    profile: decomposer
    role: decomposition
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
    decomposition_seat: decomposer
    implementation_seat: builder
    review_seat: reviewer
    acceptance_seat: coordinator
policies:
  max_runtime: 30m
  decomposer_runtime: 60m
  max_packets_per_slice: 8
  max_packet_acceptance_items: 5
```

Остальные обязательные policies из v1 сохраните. После миграции выполните `validate`, затем `mission plan --json`. План v2 должен иметь `phase: decomposition`, `pending_materialization: true` и только decomposition-карточки. Не запускайте старый и новый rig для одной mission одновременно.

Граница ответственности такая: decomposer возвращает только manifest, связанный с hash исходного контракта; trusted Factory reconciler проверяет все manifests, создаёт packet-граф через Hermes CLI и перечитывает его; builder не запускается до успешного read-back. Decomposer не вызывает `create`, `link`, `unlink`, `archive`, `purge` или `dispatch`.

Что обычно нужно менять:

- `project.slug`: короткий идентификатор проекта.
- `project.name`: отображаемое имя.
- `project.cwd`: абсолютный путь к репозиторию.
- `board.slug`: уникальный идентификатор Kanban-доски.
- `seats.*.profile`: реальные имена профилей Hermes.
- `max_retries`: число автоматических повторов.
- `max_runtime`: максимальная длительность одного запуска builder/reviewer-карточки.
- `coordinator_runtime`: бюджет карточек координатора (карточка восстановления заблокированной карточки, acceptance, локальный commit). По умолчанию равен `max_runtime`. Координатор читает граф и разбирает инцидент, поэтому ему нужен запас: при 20m карточка восстановления дважды не уложилась и была снята по таймауту до completion. Рантайм карточек замен по-прежнему берётся из целевой карточки и не зависит от этого поля.
- `decision_authority: coordinator`: значение по умолчанию. Координатор принимает все решения внутри принятой миссии; builder и reviewer не запрашивают у оператора разрешение, уточнение или подтверждение. Они выбирают безопасный обратимый вариант, фиксируют предположение и продолжают всю независимую работу. Значение `human` создаёт настоящий операторский gate только вместе с явно заданным `human_acceptance: true`; промежуточные вопросы человеку всё равно запрещены. Опущенный `decision_authority` не решает человеческую границу: `human_acceptance: true` в rig или mission без явного значения отклоняется при загрузке.
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

### Нет running при оставшемся backlog

Если после `no_agent_grace` на доске нет карточек в `running`, но остаётся `todo` либо `blocked`, не являющийся подтверждённым human gate, reconciler регистрирует класс `no_agent_activity`. Значение по умолчанию равно `2m` и задаётся в rig:

```yaml
policies:
  no_agent_grace: 2m
```

Factory читает активные статусы и время последней lifecycle-активности одной read-only транзакцией из authoritative SQLite доски, потому что JSON-поверхность Hermes не возвращает все поля, необходимые reconciler. Запись в SQLite запрещена: incident-карточка создаётся только через `hermes kanban`.

Если в snapshot есть `todo`, watchdog создаёт или повторно находит board-level incident до разбора task-level recovery, чтобы зависший разбор отдельной блокировки не заглушил сигнал полной остановки доски. При blocked-only состоянии reconciler сначала исключает подтверждённые human gates. Короткий handoff внутри grace period и доска, где уже есть `running`, incident не создают.

Incident имеет priority `100`, назначается coordinator profile, содержит reason code `no_agent_activity` и стабильный ключ `factory:{project}:reconcile:board:no-agent:{idle_epoch}:{fingerprint}`. Собственные incident-карточки и их события исключаются из idle clock и fingerprint, поэтому повторный cron-проход не размножает карточки. При `--dispatch` Factory только вызывает штатный Hermes dispatcher. Координатор диагностирует причину и создаёт ограниченную корректирующую работу; watchdog не ремонтирует backlog и не меняет граф напрямую.

### Тайм-аут и декомпозиция

При `max_retries: 1` первый неуспешный запуск переводит карточку в `blocked`. Hermes отвечает за остановку процесса и событие `gave_up`; Factory отвечает за следующую процессную реакцию:

В rig v2 действуют два разных бюджета. `decomposer_runtime` ограничивает анализ архитектурного slice и выпуск manifest. `max_runtime` ограничивает builder/reviewer packet. Тайм-аут decomposer не создаёт частичный execution-граф. Тайм-аут builder относится только к уже материализованному packet и обрабатывается обычной recovery-политикой.

Checkpoint и hard timeout не равнозначны. Builder должен сам остановить расширение scope до исчерпания бюджета, оставить репозиторий в проверяемом состоянии и заблокировать карточку с точным префиксом `Factory progress checkpoint:` и JSON по контракту карточки. Это наблюдаемый handoff для продолжения. Hard timeout означает, что Hermes принудительно завершил run с `status` или `outcome` `timed_out`; checkpoint из него не выводится автоматически. `report` считает эти показатели отдельно.

Checkpoint — единственный recovery, который Factory применяет сам, и он только добавляет карточки. Заблокированная checkpoint-карточка остаётся на доске как есть: Hermes освобождает ребёнка только когда все прямые родители `done` или `archived`, поэтому собственный review этой карточки уже никогда не запустится. Reconciler не перепривязывает граф, а создаёт два новых ключа: continuation с полным acceptance-контрактом прерванного packet (его родители — собственные prerequisites источника, поэтому сама заблокированная карточка его не задерживает) и свежий review, который зависит от continuation и клонирует контракт review исходного packet без единого изменения acceptance или proof. Ключи выводятся из ключа источника в его же форме: пятисегментный slice unit, семисегментный материализованный packet `factory:<project>:<mission>:<slice>:packet:<packet id>:<class>`, который и несёт реальная checkpoint-карточка, и legacy-форма из шести сегментов, которую ещё понимает `report`; любая другая форма ключа отклоняется, потому что continuation неверной формы получил бы идентичность, которую остальной Factory не умеет читать. Ни `link`, ни `unlink`, ни `archive` из pre-read решения не выдаются: `unlink_tasks` удаляет ребро без проверки статуса, а `archive_task` не имеет предиката состояния, поэтому завершившийся review-ребёнок или возобновлённый источник в окне между чтением и командой был бы уничтожен, и лишнее чтение это окно не закрывает. Каждый следующий проход перечитывает те же две ключевые карточки с доски и сообщает тот же continuation, ничего не создавая и не перепривязывая.

Принятая цена названа прямо: ребро собственного review заблокированной карточки и вся downstream-работа за ним остаются незапущенными навсегда. Checkpoint продолжает прерванный packet, но не освобождает цепочку за ним; переподключение цепочки — явное решение координатора (recovery-класс `unknown_operational_stall` или timeout), а не молчаливая разрушительная перепривязка внутри checkpoint-прохода.

```powershell
uv run hermes-factory reconcile `
  --rig .hermes\rig.yaml `
  --dispatch `
  --json
```

Команда:

1. находит заблокированные Factory-карточки: созданные самим Factory (`created_by: hermes-factory`) и созданные координаторским воркером внутри recovery-прогона Factory (происхождение доказывается авторитетным `created`-событием и самой recovery-карточкой-создателем, см. «Что reconciler проверяет перед первой разрушительной операцией»);
2. выбирает класс остановки по собственной терминальной записи Hermes: `gave_up` с причиной `timed_out`, `blocked`-событие самого review-воркера с префиксом отклонения review либо, для любой другой блокировки без класса и без явного human gate, catch-all `unknown_operational_stall`;
3. создаёт или повторно находит одну high-priority карточку координатора;
4. использует стабильный idempotency key, поэтому повторный запуск не создаёт дубликат;
5. читает завершённые recovery-карточки со структурированным планом;
6. проверяет replacement-граф, снимает только заявленные stale edges, архивирует и удаляет superseded-карточки, затем перечитывает граф;
7. при `--dispatch` запускает готовую recovery-карточку или освобождённую downstream-работу через штатный Hermes dispatcher.

Координатор должен изучить журнал и незавершённый diff, декомпозировать только оставшийся объём, создать последовательные пары implementation/review, добавить replacement edges и завершить recovery-карточку с точными `replacement_task_ids`, `rewires`, `superseded_task_ids` и пустым `limitations`. Он не вызывает `unlink`, `archive` или `purge`: эти команды запрещены delegated child context и выполняются доверенным no-agent reconciler после полной проверки плана.

Любая другая заблокированная Factory-карточка без зарегистрированного класса становится `unknown_operational_stall` и уходит координатору: молчаливого пропуска нет. Пропускаются только явный human gate (осознанно объявленная финальная приёмка), карточки самого reconciler и заблокированные карточки, которые не доказаны как Factory-работа (например, карточка координаторского профиля, созданная вне recovery-прогона).

### Отклонённый review и коррекция

Review-карточка, которая не прошла независимую проверку, блокируется своим воркером: причина блока начинается с `Factory review rejected:` и продолжается конкретными находками. Этот префикс прописан в генерируемом контракте review-карточки, поэтому отклонение не смешивается с ручным решением или продуктовой неопределённостью: блок без префикса остаётся решением для оператора.

Reconciler распознаёт такую карточку как класс `review_rejected` только по собственной записи Hermes — событию `blocked`, привязанному к запуску review-воркера, с непустыми находками после префикса. Текст находок попадает в тело recovery-карточки, поэтому доказательство отказа сохраняется даже после того, как superseded-review будет удалён.

От координатора требуется узкая коррекция, а не повтор rejected-объёма: одна correction-implementation карточка, исправляющая ровно записанные находки, и свежий независимый review, который заново выполняет proof. Коррекция остаётся зависимой от исходной (уже завершённой) implementation-карточки, downstream-дети переподключаются к финальному replacement-review, а superseded набор состоит только из отклонённого review — завершённая implementation сохраняется как свидетельство попытки.

### Неизвестная остановка без класса

Любая заблокированная Factory-карточка, для которой не сработал зарегистрированный detector и которая не является явным human gate, классифицируется как `unknown_operational_stall` и получает recovery-карточку координатора. Пропускаются только заранее объявленная финальная приёмка (`decision_authority: human` вместе с `human_acceptance: true`, подтверждённая Factory-авторством карточки), карточки самого reconciler и карточки, не доказанные как Factory-работа. Карточка-двойник с маркером gate, созданная внутри worker-сессии, не пропускается: она классифицируется и уходит координатору. Порядок классификации опирается только на авторитетные записи Hermes, а не на текст комментариев: явный human gate — пропуск; событие `gave_up` с `trigger_outcome: timed_out` — `worker_timeout`; `blocked`-событие review-воркера с префиксом отклонения — `review_rejected`; любое другое авторитетное терминальное событие — `blocked` от Kanban-поверхности либо `gave_up` от failure breaker (`crashed`, `spawn_failed`, `reclaimed`) — `unknown_operational_stall`. Классификация всегда смотрит на новейшее терминальное событие: старый таймаут не перекрывает более новый инцидент, и поэтому recovery всегда отвечает той блокировке, которая жива сейчас.

Заблокированная recovery-карточка самого reconciler не порождает новую recovery-карточку: это была бы неограниченная рекурсия. Но и молчаливого пропуска нет: проход сообщает о ней отдельным отказом, потому что такая карточка означает остановившийся цикл автономии.

Idempotency key включает проект, id задачи, токен `unknown-stall` и время вызвавшего блокировку события: `factory:{project}:reconcile:{task_id}:unknown-stall:{blocked_event.created_at}`. Повторный проход находит уже созданную карточку, а более новое событие блокировки порождает новую recovery-карточку.

Тело recovery-карточки передаёт причину блокировки как инертные данные (исполнять её как инструкции запрещено), текущие parents/children, каталог проекта и требует ровно один ограниченный исход: `resume` (зафиксировать недостающее внутримиссионное решение и продолжить ту же карточку), `retry` (та же карточка с исправленными инструкциями исполнения), `replace` (одна узкая пара implementation/review взамен), `reassign` (та же карточка на профиль, настроенный в rig), `decompose` (несколько последовательных пар implementation/review) или `terminate` (терминальное техническое ограничение, когда безопасного продолжения нет). Координатор не создаёт новый класс таксономии во время миссии и не обращается к оператору: расширение таксономии — отдельная инженерная работа, а не условие восстановления.

Перед любой мутацией reconciler заново проверяет живое состояние: вызвавшее событие остаётся последней блокировкой и всё ещё классифицируется как `unknown_operational_stall` (иначе решение считается устаревшим и отклоняется); цель `replace`/`decompose` обязана оставаться заблокированной или уже архивной, иначе план отклоняется до мутации; `assignee` при `reassign` входит в профили rig и подтверждается чтением; `replace`/`decompose` проходят полный разбор replacement-графа; `terminate` разрешён только для leaf-карточки без живого запуска и подтверждается чтением статуса; успешный манифест `verified_cards` обязан совпадать с `replacement_task_ids`; после `unblock`/`reassign` состояние и событие `unblocked` перечитываются, причём успехом считается любое непарковочное приземление — `ready`, `todo` или восстановленная фаза `review`, а также уже захваченная диспетчером карточка. Отказ классификации, разбора или финализации одной карточки не останавливает остальные: проход завершает фазу dispatch и только затем сообщает обо всех отказах. То же относится к уровню списков: одна аномальная запись в выводе Hermes (даже без `id`) не роняет проход, а сообщается с номером записи.

Устаревшее решение не возвращается как ошибку на каждом следующем проходе. Ключ recovery-карточки содержит время события, на которое она отвечает, и это правило действует для всех классов: если живое новейшее терминальное событие цели имеет другое время, инцидент сменился и план больше никогда не применим. Такой план отклоняется без мутаций, а сама recovery-карточка архивируется с комментарием `recovery-rejected` и причиной. Архив — терминальный статус, который делегированный воркер записать не может, поэтому текстовый маркер здесь не граница доверия: подделанный комментарий не может подавить ожидающую recovery. Новая блокировка при этом получает собственную recovery-карточку, потому что ключ идемпотентности привязан к времени нового события.

Одного сравнения времени недостаточно: применять план разрешено только к запаркованной цели, то есть к карточке в статусе `blocked` или `archived`. Разрешение карточки не пишет терминального события — `unblock` фиксирует только `unblocked`, продвижение родителя — только `promoted`, — поэтому уже разрешённая цель (например, возобновлённая или завершённая) иначе выглядела бы свежей, и разрушительный план обрушился бы на живую работу. Не-запаркованный статус цели означает, что инцидент закончился без участия плана: это устаревание, а не повод применить решение.

Если прерванный ранее проход уже применил один из rewire плана, списание не имеет права выглядеть так, будто граф не тронут: этот факт добавляется в причину отклонения, а оставшиеся superseded-карточки остаются на усмотрение recovery более нового инцидента. План всё равно списывается, потому что решение, которое уже никогда не совпадёт с живым состоянием, не должно вечно блокировать проход. Если ребёнка плана не удаётся перечитать или он отвечает чужим id, состояние графа не доказано ни в одну сторону: причина прямо сообщает, что частичное применение не исключено, и не утверждает ни того, что rewire уже применён, ни того, что граф нетронут.

Отдельный случай — план, ставший устаревшим уже после первой применённой мутации внутри текущего прохода. Тогда откат невозможен, поэтому план не списывается: проход сообщает отказ с пометкой `stale after a partial mutation`, оставляя карточку и частично изменённый граф видимыми. Следующий проход перепроверит живое состояние заново и либо продолжит применение, либо снова сообщит отказ. Мутация, оставшаяся от прерванного прохода, отличается тем, что следы её применения уже читаются в графе: такой план списывается, но с явной пометкой в причине, как описано выше.

### Что reconciler проверяет перед первой разрушительной операцией

Каждый запуск заново читает авторитетные `show --json` и `list --archived --json` записи и отклоняет план, если:

- replacement-задача создана не координаторским профилем, не имеет ровно одного события `created` с `creator_task_id` этой recovery-карточки или отсутствует в её завершающем манифесте `verified_cards`;
- replacement-задача уже архивна;
- цепочка implementation/review, дочерние задачи финального review или набор объявленных `rewires` не совпадают точно;
- метаданные плана и `verified_cards` относятся к разным запускам: метаданные берутся из последнего успешного run, а `verified_cards` — из единственного события `completed` с тем же `run_id`;
- уже снятая внешняя связь не подтверждена собственным событием Hermes `unlinked` для этого ребра — независимо от того, существует ли старый родитель ещё. Время записи не ограничивается: удаление может быть выполнено прерванным более ранним проходом той же цепочки, а rewire зависит от состояния графа, а не от автора удаления. Именно это событие отличает настоящее снятие связи от связей, исчезнувших вместе с purge старого родителя (Hermes удаляет строки связей молча);
- отсутствующий в доске superseded id не отключает проверку связности: каждый оставшийся superseded-узел обязан через свои живые parent-связи восходить к recovery-target, иначе план отклоняется до первой мутации.
- сама recovery-карточка не имеет ровно одного события `created` с пустым `creator_task_id` (то есть была создана внутри worker-сессии), а живой recovery-target не доказан как Factory-работа: он не создан Factory и не создан recovery-прогоном (см. ниже);
- живой recovery-target не несёт собственной терминальной записи Hermes для своего класса: для `worker_timeout` это событие `gave_up` с `trigger_outcome: timed_out`, для `review_rejected` — `blocked`-событие review-воркера с префиксом отклонения и непустыми находками, для `unknown_operational_stall` — терминальное событие `blocked` или `gave_up`, чьё время записано в ключе recovery-карточки, с повторной catch-all классификацией. Право на разрушение заново выводится из этой записи, а не из тела карточки;
- recovery-target отсутствует в доске, а какие-то superseded-карточки ещё живы: без живой цели precondition перепроверить нельзя, поэтому план отклоняется.

`created_by` сам по себе не является границей доверия: `hermes kanban create --created-by` — свободный флаг CLI. Доверие даёт связка авторитетного `created`-события (`creator_task_id` заполняется Hermes только для dispatcher-owned worker context) с id этой recovery-карточки и её манифестом `verified_cards`, а не имя профиля в поле. По той же причине recovery-карточка и её цель проверяются на авторство: карточка, созданная внутри worker-сессии, не может запускать разрушение, а цель обязана быть Factory-карточкой с записью таймаута.

Карточка, созданная координаторским воркером внутри recovery-прогона, — такая же Factory-работа, но доказывается она иначе, чем карточка `created_by: hermes-factory`. Проверяются три вещи: единственное `created`-событие с непустым `creator_task_id`; сам этот создатель является Factory recovery-карточкой (`created_by: hermes-factory-reconciler`, recovery-тело и recovery-форма ключа с зарегистрированным токеном класса); ключ и audit-контракт самой карточки совпадают с проектом и классом rig. Дополнительно требуется одно из двух: завершающий манифест `verified_cards` создателя перечисляет эту карточку, либо у создателя вообще нет успешного запуска — recovery-прогон, убитый собственным runtime budget, не оставил завершающего события, и манифеста для него не существует по построению, поэтому в этом случае доказательством служит только `creator_task_id` вместе с recovery-карточкой-создателем. Завершившийся прогон с пустым или чужим манифестом не годится: карточка, не перечисленная в манифесте завершившегося создателя, а также создатель, не являющийся recovery-карточкой, чужой проект или несовпадающий audit-контракт остаются отказом.

Та же проверка авторства применяется ко всем superseded-карточкам: разрушаемый набор не может выбираться по свободному полю `created_by`. Карточка, созданная внутри recovery-прогона, допускается к архивированию и удалению ровно на тех же условиях, что описаны выше. Rewire-дети проверяются как живые зависимости (связность, идентичность, подтверждение `unlinked`), но не как авторство: они перелинковываются, а не уничтожаются.

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
if (
    payload["recovery_task_ids"]
    or payload["finalized_recovery_task_ids"]
    or payload["board_incident_task_id"]
    or payload["dispatched"]
):
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

Cron сначала регистрирует и передаёт координатору board-level `no_agent_activity`, если backlog остался без исполнителя дольше grace period. Отдельно он обнаруживает task-level timeout/review/unknown stalls, создаёт recovery-карточки и применяет завершённые планы. Диагностику idle-доски и декомпозицию выполняет обычная координаторская Hermes-сессия с лимитом из `rig.yaml`; привилегированную очистку графа выполняет только no-agent reconciler.

### Типизация остановок

Покажите реестр в текстовом или JSON-виде:

```powershell
uv run hermes-factory stall-taxonomy
uv run hermes-factory stall-taxonomy --json
```

Основные `reason_code`: `normal_dependency_wait`, `awaiting_dispatch`, `stale_runner`, `worker_timeout`, `stale_dependency_edge`, `review_rejected`, `no_agent_activity`, `human_gate`, `corrupt_state`, `recovery_coordinator_insufficient_kanban_authority`, `unknown_operational_stall`. Для каждого класса фиксируются detection, cause, recovery, authority, verification, fallback и regression test. Автоматическое восстановление объявлено для девяти классов; собственные detector и recovery reconciler'а есть у board-level `no_agent_activity`, task-level `worker_timeout`, `review_rejected` и универсального catch-all `unknown_operational_stall` (любая заблокированная Factory-карточка без класса и без явного human gate), остальные закрываются нативной реакцией Hermes (`awaiting_dispatch`, `normal_dependency_wait`, `stale_runner`), финализацией графа (`stale_dependency_edge`) или исправленной поверхностью создания карточек (`recovery_coordinator_insufficient_kanban_authority`). Новая остановка не закрывается единичным ручным ремонтом: сначала добавляется воспроизводимый тест, затем detector и безопасная процедура восстановления.

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

При `decision_authority: coordinator` финальная acceptance-карточка назначается координатору. Это режим по умолчанию: implementation, review и acceptance не запрашивают у человека разрешение или уточнение, а самостоятельно выбирают безопасный обратимый вариант и фиксируют решение. Неразрешённое разрушительное или внешнее действие не выполняется: агенты завершают всю независимую безопасную работу и записывают точное техническое ограничение как результат, а не вопрос оператору.

`human_gate` возникает только при одновременном явном выборе `decision_authority: human` и `human_acceptance: true`. Он относится только к финальной acceptance-карточке; builder и reviewer не могут создавать неожиданные промежуточные human-gate.

Явность обязательна: `human_acceptance: true` в rig или mission без явного `decision_authority` отклоняется при загрузке, поэтому gate не может ни появиться, ни исчезнуть из-за значения по умолчанию. Роль человека защищена и авторством: карточка-двойник с маркером gate, созданная внутри worker-сессии, не пропускается, а маршрутизируется координатору.

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
- возраст самой старой ready-задачи;
- медианное decomposition lead time;
- число materialized packets по architectural slice;
- долю builder packets, завершённых с первой попытки;
- долю неуспешных builder packets с корректным progress checkpoint;
- hard timeout rate по task class;
- медианное packet lead time от создания implementation до завершения review/commit;
- rework rate после `changes_requested` и более поздней builder-попытки.

`time_to_first_command_minutes` и `time_to_first_file_mutation_minutes` сейчас равны `null`, а текстовый отчёт пишет `unavailable`. В доступном `runs --json` нет авторитетных action events, поэтому Factory не подменяет их пересказом агента.

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

### Decomposition завершена, но packets не материализованы

Не изменяйте Kanban SQLite вручную. Сначала сохраните read-only снимок:

```powershell
hermes kanban --board hermes-factory list --json
hermes kanban --board hermes-factory diagnostics --json
hermes kanban --board hermes-factory show <decomposition-task-id> --json
hermes kanban --board hermes-factory runs <decomposition-task-id> --json
```

В `show --json` проверьте единственное внешнее событие `created`, отсутствие `edited`, успешный run и ровно одно соответствующее событие `completed`. В metadata успешного run должны быть `version`, `project`, `mission`, `source_slice`, `source_contract_sha256`, непустой `packets` и пустой `limitations`. Для каждого packet проверьте `id`, `depends_on`, acceptance coverage, trusted proof commands и безопасные project-relative paths.

Запустите trusted reconciler без ручных мутаций:

```powershell
uv run hermes-factory reconcile --rig .hermes/rig.yaml --json
```

Затем снова выполните `list --json`. Для каждого packet должны появиться implementation/review карточки со стабильными ключами `factory:<project>:<mission>:<slice>:<packet>:implementation` и `...:review`. Команда `show <task-id> --json` показывает `parents` и `children`; по ним проверяйте граф. Если materialization отклонена, исправьте rig/mission/profile output и повторите reconciler. Не используйте `sqlite3`, `unlink`, `archive` или `purge` для обхода проверки.

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
