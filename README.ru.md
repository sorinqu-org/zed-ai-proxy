# zed-ai-proxy (Русская версия)

Высокопроизводительный асинхронный обратный прокси-сервер, открывающий доступ к AI-моделям подписки Zed IDE (Claude Opus 5, Claude Sonnet 5, Claude Haiku 4.5, GPT-5.6, Gemini 3) для внешних инструментов, редакторов (Cursor, Open WebUI и др.) и скриптов через стандартные интерфейсы OpenAI (`/v1/chat/completions`) и Anthropic (`/v1/messages`).

Построен на базе FastAPI, HTTPX и встроенного пула учетных записей.

[English Version](README.md)

---

## Возможности

- **Пул и ротация нескольких аккаунтов:** Поддержка нескольких аккаунтов Zed Pro с автоматическим распределением запросов по алгоритму Round-Robin.
- **Отказоустойчивость и кулдаун:** При получении HTTP 429 (Rate Limit) аккаунт временно переводится в режим ожидания (cooldown), а запрос автоматически перенаправляется на следующий доступный аккаунт.
- **Автоматическое обновление токенов:** Фоновый процесс отслеживает срок действия JWT-токенов (`exp`) и заранее обновляет их через внутренний API `cloud.zed.dev/client/llm_tokens` до истечения срока.
- **Поддержка двух протоколов:** Совместимость со спецификациями OpenAI (`/v1/chat/completions`, `/v1/models`) и Anthropic (`/v1/messages`) с полной поддержкой стриминга Server-Sent Events (SSE).
- **Фильтрация больших изображений:** Автоматическая очистка тяжелых base64 data URL для защиты от переполнения контекста и ошибок HTTP 413 Payload Too Large.
- **Панель мониторинга:** Веб-интерфейс `/dashboard` и JSON-эндпоинт `/status` для отслеживания состояния пула, таймеров жизни токенов и счетчиков запросов.

---

## Как получить данные аккаунта (user_id и access_token)

Zed IDE авторизуется через GitHub OAuth и сохраняет учетные данные в защищенном системном хранилище ОС (Keyring / Keychain / Credential Manager). Для настройки прокси вам понадобятся `user_id` и `access_token`.

### Linux

Zed на Linux сохраняет данные в Secret Service через `oo7` под атрибутом `url = "https://zed.dev"` с меткой `zed-github-account`.

**Способ 1. Через терминал (`secret-tool`):**
```bash
secret-tool search url "https://zed.dev"
```

Вывод команды:
```text
[/25]
label = zed-github-account
secret = {"version":2,"id":"client_token_...","token":"..."}
attribute.url = https://zed.dev
attribute.username = 1059832
```

- Значение `attribute.username` — это ваш `user_id` (например, `1059832`).
- Значение `secret` целиком (строка JSON `{"version":2,...}`) — это ваш `access_token`.

Если требуется получить только пароль в буфер или на экран:
```bash
secret-tool lookup url "https://zed.dev"
```

**Способ 2. Через графический интерфейс:**
1. Откройте приложение **Пароли и ключи** (Seahorse).
2. Перейдите во вкладку **Вход** (Login) или **Связка ключей по умолчанию**.
3. Найдите запись с меткой `zed-github-account` или URL `https://zed.dev`.
4. В свойствах: «Имя пользователя» — это `user_id`, значение пароля — это `access_token`.

---

### macOS

Zed на macOS сохраняет данные в Связке ключей (Keychain) как Internet Password для сервера `https://zed.dev`.

**Способ 1. Через Терминал (`security`):**
```bash
security find-internet-password -s "https://zed.dev" -g
```

Вывод команды содержит:
```text
"acct"<blob>="1059832"
password: "{\"version\":2,\"id\":\"client_token_...\",\"token\":\"...\"}"
```

- В строке `acct` указан ваш `user_id`.
- В строке `password` указан ваш `access_token`.

**Способ 2. Через графический интерфейс (Связка ключей / Keychain Access):**
1. Откройте **Связка ключей** (Keychain Access).
2. В строке поиска введите `https://zed.dev` или `zed.dev`.
3. Откройте найденную запись двойным кликом.
4. Поле **Учетная запись** (Account) содержит ваш `user_id`.
5. Поставьте галочку **Показать пароль** (Show password) и введите пароль пользователя macOS, чтобы скопировать `access_token`.

---

### Windows

Zed на Windows сохраняет данные в Диспетчере учетных данных Windows (Windows Credential Manager) как общие учетные данные (Generic Credentials) с именем цели `zed:url=https://zed.dev`.

**Способ 1. Через графический интерфейс:**
1. Нажмите сочетание клавиш `Win + R`, введите `control keymgr.dll` и нажмите Enter (либо откройте **Панель управления -> Диспетчер учетных данных**).
2. Выберите раздел **Учетные данные Windows** (Windows Credentials).
3. В списке **Общие учетные данные** (Generic Credentials) найдите пункт:
   `zed:url=https://zed.dev`
4. Поле **Имя пользователя** (User name) содержит ваш `user_id`.
5. Нажмите кнопку **Показать** (Show) рядом со строкой пароля, чтобы скопировать `access_token`.

**Способ 2. Просмотр через консоль:**
```cmd
cmdkey /list:zed*
```

---

## Архитектура работы

1. Прокси отправляет запрос `POST https://cloud.zed.dev/client/llm_tokens` с заголовком `Authorization: <user_id> <access_token>` и получает временный JWT LLM-токен.
2. Фоновый поток обновляет этот токен за 5 минут до его истечения.
3. Клиентские запросы (OpenAI/Anthropic) преобразуются в формат Zed Cloud и направляются к `https://cloud.zed.dev/models/...` с заголовком `Authorization: Bearer <llm_token>`.

```
[ Внешний клиент / Cursor / Скрипты ]
                 |
                 v  (OpenAI / Anthropic REST & SSE)
         [ zed-ai-proxy :8080 ]
           |-- AccountPool (Round-Robin и проверка доступности)
           |-- TokenRefresher (Фоновое обновление токенов)
           |-- PayloadFilter (Защита от переполнения контекста)
                 |
                 v  (Протокол Zed Cloud)
         [ cloud.zed.dev Gateway ]
                 |
                 v
      [ Claude Opus 5 / Sonnet 5 / GPT-5.6 ]
```

---

## Установка и быстрый старт

### 1. Глобальная установка CLI (Bash, Zsh, Fish)

Для установки утилиты `zed-proxy` глобально в систему выполните установочный скрипт:

```bash
git clone https://github.com/sorinqu-org/zed-ai-proxy.git
cd zed-ai-proxy
./install.sh
```

Скрипт автоматически:
- Развернет изолированное окружение в `~/.local/share/zed-ai-proxy`.
- Установит исполняемый бинарник `zed-proxy` (и алиас `zed-ai-proxy`) в `~/.local/bin`.
- Настроит `PATH` для оболочек **bash**, **zsh** и **fish**.
- Установит автодополнение команд (tab completion) для всех трех оболочек.

После установки доступны глобальные команды из любой папки:
```bash
zed-proxy sync --name "Мой Zed"   # Импорт/обновление аккаунта из связки ключей
zed-proxy start -d                # Запуск прокси в фоне как демона
zed-proxy status                  # Просмотр таблицы статуса и счетчиков запросов
zed-proxy stop                    # Остановка демона
zed-proxy logs -f                 # Просмотр логов в реальном времени
```

---

### 2. Локальный запуск без глобальной установки (альтернатива)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Автоматическое добавление или обновление аккаунтов

Скрипт синхронизации автоматически достает учетные данные из системного хранилища, проверяет их на серверах Zed Cloud и сохраняет в `accounts.json` (а если прокси уже запущен — отправляет сигнал горячей перезагрузки):

```bash
python3 scripts/sync_account.py --name "Мой аккаунт Zed"
```

- Если пользователь уже есть в `accounts.json`, скрипт обновит его токен и статус.
- Если пользователя нет, он автоматически создаст новую запись.
- Для проверки без записи в файл используйте флаг `--dry-run`.

### 3. Ручная настройка учетных записей (альтернатива)

Если вы настраиваете прокси на удаленном сервере без GUI/Keyring, скопируйте шаблон вручную:
```bash
cp accounts.example.json accounts.json
```

Заполните `accounts.json` полученными данными:
```json
[
  {
    "id": "acc-1",
    "name": "Zed Main",
    "user_id": "1059832",
    "access_token": "{\"version\":2,\"id\":\"client_token_...\",\"token\":\"...\"}",
    "organization_id": null
  },
  {
    "id": "acc-2",
    "name": "Zed Secondary",
    "user_id": "987654",
    "access_token": "{\"version\":2,\"id\":\"client_token_...\",\"token\":\"...\"}",
    "organization_id": null
  }
]
```

> Примечание: В поле `access_token` можно передавать как JSON-строку целиком, так и сам JSON-объект, либо значение поля token.

### 4. Запуск сервера

```bash
python3 -m app.main
```
Сервер запустится на адресе `http://0.0.0.0:8080`.

Откройте в браузере `http://localhost:8080/dashboard` для просмотра статуса пула аккаунтов.

---

## Примеры использования

### OpenAI Python SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8080/v1",
    api_key="anything",
)

response = client.chat.completions.create(
    model="claude-3-5-sonnet",
    messages=[{"role": "user", "content": "Объясни устройство async в Python в двух предложениях."}],
    stream=True,
)

for chunk in response:
    content = chunk.choices[0].delta.content
    if content:
        print(content, end="", flush=True)
```

### Anthropic Python SDK

```python
import anthropic

client = anthropic.Anthropic(
    base_url="http://localhost:8080",
    api_key="anything",
)

with client.messages.stream(
    model="claude-3-5-sonnet",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Привет!"}],
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
```

### cURL (Стриминг)

```bash
curl -N http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer anything" \
  -d '{
    "model": "claude-sonnet-5",
    "messages": [{"role": "user", "content": "ping"}],
    "stream": true
  }'
```

---

### Настройка Claude Code

Для перенаправления Claude Code через `zed-ai-proxy`:

1. Отредактируйте конфигурационный файл `~/.claude/settings.json`:
```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://localhost:8080",
    "ANTHROPIC_AUTH_TOKEN": "zed-proxy-token",
    "ANTHROPIC_MODEL": "claude-sonnet-5",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-5",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-5",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-haiku-4-5",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"
  },
  "model": "claude-sonnet-5"
}
```

2. Запустите Claude Code:
```bash
claude
```

3. Переключение моделей внутри Claude Code:
- Переключение на Claude Opus:
  ```text
  /model claude-opus-5
  ```
- Переключение на Claude Sonnet:
  ```text
  /model claude-sonnet-5
  ```
- Либо запуск сразу с нужной моделью:
  ```bash
  claude --model claude-opus-5
  ```

---

### Настройка OpenAI Codex CLI

Для использования `zed-ai-proxy` в Codex CLI:

1. Добавьте провайдера `[model_providers.zed]` в файл `~/.codex/config.toml`:
```toml
model = "claude-sonnet-5"
model_provider = "zed"

[model_providers.zed]
name = "Zed AI Proxy"
base_url = "http://127.0.0.1:8080/v1"
wire_api = "responses"
request_max_retries = 3
stream_max_retries = 5
```

2. Запустите Codex:
```bash
codex
```

3. Запуск с переопределением модели через CLI:
```bash
# Использовать Claude Sonnet
codex -c model_provider=zed -c model=claude-sonnet-5

# Использовать Claude Opus
codex -c model_provider=zed -c model=claude-opus-5

# Использовать GPT-5.6 Luna
codex -c model_provider=zed -c model=gpt-5.6-luna
```

---

## Мониторинг баланса и лимитов (Billing & Usage)

`zed-ai-proxy` в реальном времени считает расход токенов, оценивает затраты и ведет учет остатка баланса как по каждому отдельному аккаунту, так и суммарно по всему пулу:

- Просмотр баланса в терминале через CLI:
  ```bash
  zed-proxy status
  ```
  Команда выводит суммарный оставшийся баланс пула, общее число использованных токенов, оценочную стоимость, а также прямые ссылки на страницу биллинга и расхода в Zed Dashboard (`https://dashboard.zed.dev/{org_id}/billing/usage`).
- Веб-дашборд: `http://localhost:8080/dashboard`.
- Программный JSON-статус: `GET /status`.

---

## Изоляция моделей и безопасность при хостинге и перепродаже API

Если вы размещаете `zed-ai-proxy` на публичном сервере/VDS для раздачи или продажи доступа:

1. **Как устроен Tool Use (вызов инструментов):**
   Языковые модели сами по себе **не выполняют код на сервере прокси**. Блоки вызова (`tool_use` или `tool_calls`) передаются клиенту (например, в терминал пользователя, Cursor, Claude Code или расширение IDE), и именно клиент на своей машине запускает команды. Прокси является промежуточным шлюзом и не имеет встроенного исполнителя bash-команд.
2. **Политика изоляции инструментов (`TOOL_POLICY`):**
   - `sanitize` (по умолчанию): перехватывает и вырезает из клиентских запросов опасные системные инструменты выполнения (`bash`, `exec`, `terminal`, `command`, `write_file`, `subprocess` и др.).
   - `block_all`: полностью вырезает `tools` и `tool_choice` из любых запросов, гарантируя чистый диалоговый режим без генерации tool calls.
   - `allow`: сквозной пропуск всех инструментов.
3. **Песочница и системный промпт (`STRICT_ISOLATION=true`):**
   Автоматически добавляет в системный промпт модели директиву строгой изоляции (запрет на попытки исследования хоста, запуск деструктивных команд `rm -rf`, доступ к переменным окружения).
4. **Авторизация клиентских ключей (`PROXY_API_KEYS`):**
   Защита эндпоинтов `/v1/*` токенами Bearer, чтобы сторонние пользователи не могли тратить ваш баланс без оплаты:
   ```bash
   export PROXY_API_KEYS="sk-client-key-1,sk-client-key-2"
   ```
5. **Защита админ-панели (`ADMIN_KEY`):**
   Закрывает доступ к `/dashboard`, `/status` и `/api/refresh` от посторонних глаз:
   ```bash
   export ADMIN_KEY="super-secret-admin-pass"
   ```

---

## Запуск тестов

```bash
PYTHONPATH=. pytest tests/ -v
```

---

## Лицензия

MIT License.
