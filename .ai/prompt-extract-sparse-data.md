# Промпт: извлечение DN-релевантной информации из больших внешних чатов

## 1. Цель

Ты обрабатываешь **большие дампы Telegram-чатов и каналов** общей тематики (эмиграция, Италия), в которых лишь **≤1% сообщений** касается визы цифрового кочевника (DN). Твоя задача — найти эти сообщения, восстановить полный контекст дискуссии вокруг них и извлечь полезную информацию для обновления гайда.

**Ключевой принцип:** НЕ читай дамп целиком. Работай через поиск по ключевым словам → расширение контекста → **верификация DN-релевантности** → извлечение фактов.

## 2. Входные данные

### 2.1. Источники

Дампы хранятся в `.datasource/YYYY-MM-DD/`. Каждая папка — один канал или чат:

```
.datasource/2026-04-06/
  emigrantista_answers_2026-04-06/   ← канал Q&A по эмиграции
  immigrazia_IT_2026-04-06/          ← канал про иммиграцию в Италию
  rutoitaly_2026-04-06/              ← канал про жизнь в Италии
  rutoitalychat_2026-04-06/          ← чат (supergroup) с тредами
```

### 2.2. Формат данных

Каждая папка содержит `result.json` — Telegram-экспорт (TDLib):

```json
{
  "name": "Название канала/чата",
  "type": "public_channel" | "public_supergroup",
  "id": 123456789,
  "messages": [...]
}
```

**Два типа источников:**

| Тип | `type` | Треды | Особенности |
|-----|--------|-------|-------------|
| Канал | `public_channel` | Нет `reply_to_message_id` | Посты от авторов канала, комментарии отсутствуют |
| Чат (supergroup) | `public_supergroup` | Есть `reply_to_message_id` | Живые обсуждения, цепочки реплаев |

Формат сообщения:
```json
{
  "id": 1234,
  "type": "message",
  "date": "2026-02-15T10:30:00",
  "date_unixtime": "1739612400",
  "from": "Имя",
  "from_id": "user123456",
  "text": "текст или массив объектов",
  "text_entities": [{"type": "plain", "text": "..."}],
  "reply_to_message_id": 1230,
  "forwarded_from": "Другой канал",
  "reactions": [{"type": "emoji", "emoji": "👍", "count": 3}]
}
```

> **Важно:** поле `text` может быть строкой ИЛИ массивом (при форматировании). Для поиска по тексту используй как `text`, так и `text_entities`.

## 3. Триггерные строки для поиска

### Tier 1 — Прямые упоминания DN (безусловный триггер)

Любое совпадение = сообщение точно релевантно. Grep case-insensitive.

| Паттерн (regex) | Что покрывает |
|------------------|--------------|
| `номад` | номад, номада, номаду, номадом, номаде, номады, номадов, номадам, номадами, номадах |
| `кочевни` | кочевник, кочевника, кочевнику, кочевником, кочевнике, кочевники, кочевников, кочевникам, кочевниками, кочевниках |
| `цифров.{0,5}кочевни` | цифровой кочевник, цифровых кочевников, цифровым кочевникам |
| `digital.?nomad` | digital nomad, digital-nomad, Digital Nomad |
| `nomad[ie]\s*digital[ie]` | nomade digitale, nomadi digitali |
| `nomad.?visa` | nomad visa |
| `виз\S*\s+(?:DN\|номад\|кочевни)` | виза DN, визу номада, визы кочевника |
| `(?:DN\|ND)\s+виз` | DN виза, ND виза |

### Tier 2 — Контекстные триггеры (требуют проверки)

Совпадение = сообщение **может быть** релевантно. После нахождения проверь контекст (±5 сообщений) — речь идёт о DN/удалённой работе из Италии, или о чём-то другом?

| Паттерн (regex) | Что покрывает | Типичные false positives |
|------------------|--------------|--------------------------|
| `удал[её]н` | удалённая работа, удалённый работник, удалёнка, удалённо | «сообщение удалено», «удалённое место» |
| `lavorator[ie]\s*(?:da\s*)?remoto` | lavoratore da remoto, lavoratori remoti | — |
| `lavoro\s*(?:da\s*)?remoto` | lavoro da remoto, lavoro remoto | — |
| `lavoro\s*autonomo` | lavoro autonomo | **ОЧЕНЬ ШУМНЫЙ** — общий контекст ИП в Италии, не привязанный к DN |
| `фрилан[сз]` | фриланс, фрилансер, фрилансеры, фрилансить | общие обсуждения фриланса без привязки к DN |
| `самозанят` | самозанятый, самозанятость, самозанятых | общие обсуждения самозанятости |
| `remote\s*work` | remote work, remote worker | — |
| `работа.{0,10}из.?за\s*рубеж` | работа из-за рубежа | — |
| `relocat\S*.*(?:ital\|итал)` | relocation в Италию | общий контекст релокации |

> **ВАЖНО: ложные друзья DN-визы.** Виза D (долгосрочная виза в Италию) бывает многих типов: lavoro subordinato (наёмная работа), lavoro autonomo (ИП/фриланс), studio (учёба), famiglia (семья), elective residence и др. Только один подтип — **nomade digitale / lavoratore da remoto** — относится к DN. Обсуждение «визы D», «lavoro autonomo», «partita IVA», «ВНЖ» без упоминания DN/номада/кочевника — это НЕ DN-тематика. Такие дискуссии нужно отсеивать на шаге верификации.

### Tier 3 — Юридические/специфичные (редкие, но точные)

| Паттерн (regex) | Что покрывает |
|------------------|--------------|
| `27[\s.-]*ter` | статья 27-ter (правовая основа DN-визы) |
| `decreto.*nomad\|nomad.*decreto` | decreto nomadi digitali |
| `visto.*nomad\|nomad.*visto` | visto per nomadi |
| `permesso.*nomad\|nomad.*permesso` | permesso di soggiorno per nomade digitale |

### Tier 4 — Составные триггеры (2+ слова рядом)

Отдельно эти слова слишком шумные. Но если 2+ из них появляются в одном сообщении или в соседних (±3 по ID) — вероятна DN-тематика:

- `ВНЖ` + `удалён` / `фриланс` / `номад`
- `partita\s*iva` + `номад` / `кочевни` / `digital nomad`
- `forfettari` + `номад` / `кочевни` / `digital nomad`
- `квестур` + `номад` / `кочевни`
- `codice\s*fiscale` + `номад` / `кочевни`
- `INPS` + `номад` / `кочевни` / `фриланс`
- `impatriati` + `номад` / `кочевни`

## 4. Алгоритм работы

### Шаг 1: Поиск сообщений-кандидатов

Для каждого файла `result.json`:

1. **Grep** по всем Tier 1 паттернам (case-insensitive). Каждое совпадение — безусловный кандидат.
2. **Grep** по Tier 2 паттернам. Для каждого совпадения проверь текст сообщения — отфильтруй очевидные false positives (например, «сообщение удалено»).
3. **Grep** по Tier 3 паттернам. Каждое совпадение — безусловный кандидат.
4. Собери список уникальных `id` сообщений-кандидатов.

> **Практически:** используй `grep -n` по файлу, чтобы найти строки с совпадениями, затем определи `id` ближайшего сообщения (ищи `"id":` выше по файлу).

### Шаг 2: Расширение контекста

Для каждого сообщения-кандидата собери **полную дискуссию**:

#### 2a. Для чатов (supergroup, есть `reply_to_message_id`):

1. **Тред назад:** Если у кандидата есть `reply_to_message_id` → загрузи это сообщение → если у него тоже есть `reply_to_message_id` → рекурсивно до корня.
2. **Тред вперёд:** Найди все сообщения, у которых `reply_to_message_id` указывает на кандидата или на любое сообщение в его цепочке (транзитивно).
3. **Соседние сообщения:** Загрузи ±15 сообщений по `id` от каждого сообщения в треде. Из них отбери те, что:
   - В пределах 30 минут от сообщений треда
   - Тематически связаны (упоминают тот же вопрос, отвечают тому же автору)
4. **Дедупликация:** Объедини все найденные сообщения, убери дубликаты по `id`, отсортируй по `date`.

#### 2b. Для каналов (public_channel, нет тредов):

1. Загрузи ±5 сообщений по `id` от кандидата.
2. Если пост длинный (разбит на несколько сообщений от одного автора подряд) — собери все части.
3. Если есть `forwarded_from` — отметь источник.

### Шаг 3: Верификация DN-релевантности (ОБЯЗАТЕЛЬНЫЙ)

> **Это критический шаг.** Без него Tier 2 триггеры дают массу ложных срабатываний, особенно в больших чатах про Италию. На rutoitalychat (103K сообщений) без верификации было 529 дискуссий; после — 269. Половина ушла, потому что это были обсуждения lavoro autonomo, фриланса, самозанятости **без привязки к DN**.

Для каждой собранной дискуссии проверь: **содержит ли объединённый текст ВСЕХ сообщений дискуссии хотя бы одно DN-специфичное ключевое слово?**

#### Регулярное выражение для верификации DN-контекста:

```python
DN_VERIFY_RE = re.compile(
    r'номад|кочевни|digital[\s._-]?nomad|nomad[\s._-]?visa'
    r'|nomad[ie]\s*digital|\bdn\b|виз\S*\s+dn|dn\s+виз'
    r'|27[\s.-]*ter|decreto.*nomad|цифров\S*\s+кочевни',
    re.IGNORECASE
)
```

**Правило:** если `DN_VERIFY_RE` не находит ни одного совпадения в объединённом тексте всех сообщений дискуссии — дискуссия отсеивается.

**Почему это работает:**
- Tier 1 дискуссии проходят автоматически — триггерное сообщение уже содержит DN-слово.
- Tier 2 дискуссии проходят, только если в расширенном контексте (соседние сообщения, тред) кто-то упомянул DN. Это отсекает general lavoro autonomo, общий фриланс, самозанятость без DN-контекста.
- `\bdn\b` ловит аббревиатуру «DN» в тексте, что покрывает случаи типа «подавался на DN в Белграде» — формально нет Tier 1 слова «номад», но DN-контекст есть.

> **Что НЕ является DN-контекстом:**
> - «виза D» — это общая категория долгосрочных виз Италии (lavoro subordinato, autonomo, studio, famiglia, elective residence, и т.д.)
> - «lavoro autonomo» без слова «номад»/«DN»/«кочевник» — это обычная фрилансерская виза
> - «partita IVA», «ВНЖ», «квестура» сами по себе — общая иммиграционная тематика

### Шаг 4: Извлечение информации

Из каждой подтверждённой дискуссии извлеки факты, следуя тем же правилам, что в `prompt-update-guide.md`:

**Что извлекать (приоритет):**

| Категория | Примеры | Приоритет |
|-----------|---------|-----------|
| Конкретные кейсы | «подал DN в Белграде, получил за 3 недели» | Высший |
| Изменения процедур | «теперь для номадов квестура Милана требует X» | Высший |
| Документы и требования | «для DN-визы нужен апостиль на DDV» | Высший |
| Сроки и стоимость | «перевод стоил 5000₽», «ждал 45 дней» | Высший |
| Сравнения с другими визами | «DN vs наёмный — разница в сроках» | Высокий |
| Контакты и ресурсы | переводчики, консультанты, ссылки | Средний |
| Практические советы | «записывайтесь через бот», «берите копии» | Средний |
| Общие рассуждения без конкретики | «номадам в Италии сложно» | Игнорировать |

**Обязательный контекст для каждого извлечения:**
- Источник: название канала/чата + ID сообщения → `[emigrantista #1234]`, `[rutoitalychat #5678]`
- Консульство / квестура / город (если применимо)
- Тип занятости (ИП / наёмный) (если применимо)
- Дата события или сообщения

### Шаг 5: Формирование выхода

Сохрани извлечённые данные **в ту же папку, где лежит исходный `result.json`**, с префиксом `extracted_`:

```
.datasource/2026-04-06/
  emigrantista_answers_2026-04-06/
    result.json                          ← исходник
    extracted_emigrantista_answers.json   ← результат извлечения
```

Формат JSON:

```json
{
  "source": "emigrantista_answers_2026-04-06",
  "extraction_date": "2026-04-06",
  "discussions": [
    {
      "discussion_id": 1,
      "trigger_messages": [1234, 1235],
      "all_message_ids": [1230, 1231, 1234, 1235, 1236, 1240],
      "topic": "Краткое описание темы",
      "messages": [
        {
          "id": 1234,
          "date": "2026-03-15T10:30:00",
          "from": "Имя",
          "text": "полный текст"
        }
      ],
      "extracted_facts": [
        {
          "fact": "Квестура Милана для DN требует дополнительную справку X",
          "category": "Изменения процедур",
          "confidence": "🟡 Из опыта",
          "context": "квестура Милана, ИП",
          "date": "2026-03-15",
          "source_ids": [1234, 1235],
          "guide_section": "08-after-arrival.md"
        }
      ]
    }
  ],
  "stats": {
    "total_messages_scanned": 50000,
    "trigger_matches": 150,
    "discussions_found": 25,
    "discussions_after_filtering": 18,
    "facts_extracted": 42
  }
}
```

## 5. Обработка ссылок и пересланных сообщений

### 5.1. Пересланные сообщения

Если триггерное сообщение содержит `forwarded_from` — это может быть пересланный пост из DN-специфичного канала. Учитывай как источник, отмечай оригинальный канал.

### 5.2. Ссылки

Если в релевантном обсуждении есть ссылки на:
- **Официальные источники** (сайты консульств, integrazionemigranti.gov.it, законодательство) → переходи и верифицируй
- **Другие Telegram-каналы/чаты** → отметь как потенциальный источник для будущей обработки
- **Статьи и гайды** → извлеки суть, если касается DN

## 6. Система маркировки достоверности

Та же, что в основном гайде:
- 🟢 **Официально** — из законодательства, сайтов консульств, integrazionemigranti.gov.it
- 🟡 **Из опыта** — подтверждено реальными кейсами (особенно ценно из внешних чатов — независимый источник)
- 🔴 **Спорно** — противоречивая информация или расходится с данными из основного чата

> **Важно:** факты из внешних чатов, **подтверждающие** информацию из основного DN-чата, особенно ценны — это независимая верификация. Отмечай такие случаи.

## 7. Ключевые правила

1. **Экономь контекст** — не читай весь дамп, работай через grep
2. **Не выдумывай** — если информация неполная, сохрани что есть и пометь «требует уточнения»
3. **Различай факты и мнения** — «DN-визу дали за 2 недели» (факт) vs «это консульство лучше для номадов» (мнение)
4. **Нейтрализуй эмоции** — извлекай суть из эмоциональных сообщений
5. **Учитывай дату** — более свежая информация приоритетнее
6. **Перекрёстная проверка** — если факт из внешнего чата противоречит основному гайду, сохрани оба варианта с пометкой 🔴

## 8. Чеклист перед финализацией

- [ ] Все Tier 1 триггеры проверены по каждому файлу
- [ ] Для Tier 2 совпадений проверен контекст (ложные срабатывания отфильтрованы)
- [ ] **Верификация DN-релевантности пройдена** для каждой дискуссии (Шаг 3)
- [ ] Для чатов (supergroup) восстановлены полные треды (reply_to вперёд-назад)
- [ ] Каждый извлечённый факт имеет ссылку на источник `[канал #ID]` и дату
- [ ] Указан контекст (консульство/квестура/город/тип занятости) где применимо
- [ ] Маркировка достоверности (🟢/🟡/🔴) проставлена
- [ ] Факты, подтверждающие или противоречащие основному гайду, особо отмечены
- [ ] Статистика (сколько просканировано / найдено / извлечено) заполнена

---

## Приложение A: Референсные скрипты

Проверенные скрипты для обработки дампов. Использовались на реальных данных (апрель 2026).

### A.1. Загрузка и индексация дампа (для supergroup)

Для больших чатов (100K+ сообщений) эффективнее загрузить JSON один раз и работать через словарь, чем grep-ить каждый раз. Для каналов (<10K сообщений) можно обойтись grep.

```python
import json, re
from collections import defaultdict

data = json.load(open('result.json'))
messages = [m for m in data['messages'] if m.get('type') == 'message']
msg_by_id = {m['id']: m for m in messages}

# Индекс: кто на кого отвечает (для восстановления тредов)
replies_to = defaultdict(list)  # parent_id -> [child_ids]
for m in messages:
    if 'reply_to_message_id' in m:
        replies_to[m['reply_to_message_id']].append(m['id'])

def get_text(msg):
    """Извлечь плоский текст из сообщения (text может быть строкой или массивом)."""
    t = msg.get('text', '')
    if isinstance(t, str):
        return t
    if isinstance(t, list):
        return ''.join(
            part if isinstance(part, str) else part.get('text', '')
            for part in t
        )
    return str(t)
```

### A.2. Поиск триггерных сообщений

```python
TIER1_RE = re.compile(
    r'номад|кочевни|цифров.{0,5}кочевни'
    r'|digital.?nomad|nomad[ie]\s*digital[ie]|nomad.?visa'
    r'|виз\S*\s+(?:DN|номад|кочевни)|(?:DN|ND)\s+виз',
    re.IGNORECASE
)

TIER2_RE = re.compile(
    r'удал[её]н|lavorator[ie]\s*(?:da\s*)?remoto'
    r'|lavoro\s*(?:da\s*)?remoto|lavoro\s*autonomo'
    r'|фрилан[сз]|самозанят|remote\s*work'
    r'|работа.{0,10}из.?за\s*рубеж|relocat\S*.*(?:ital|итал)',
    re.IGNORECASE
)

TIER3_RE = re.compile(
    r'27[\s.-]*ter|decreto.*nomad|nomad.*decreto'
    r'|visto.*nomad|nomad.*visto|permesso.*nomad|nomad.*permesso',
    re.IGNORECASE
)

# Регулярка для верификации DN-контекста всей дискуссии (Шаг 3)
DN_VERIFY_RE = re.compile(
    r'номад|кочевни|digital[\s._-]?nomad|nomad[\s._-]?visa'
    r'|nomad[ie]\s*digital|\bdn\b|виз\S*\s+dn|dn\s+виз'
    r'|27[\s.-]*ter|decreto.*nomad|цифров\S*\s+кочевни',
    re.IGNORECASE
)

# False positive фильтр для Tier 2
TIER2_FP_RE = re.compile(r'сообщение удалено|удалённое место', re.IGNORECASE)

trigger_ids = set()
for m in messages:
    text = get_text(m)
    if TIER1_RE.search(text) or TIER3_RE.search(text):
        trigger_ids.add(m['id'])
    elif TIER2_RE.search(text) and not TIER2_FP_RE.search(text):
        trigger_ids.add(m['id'])
```

### A.3. Восстановление тредов (supergroup)

```python
def get_thread(msg_id):
    """Собрать полный тред: от корня вниз через все reply-цепочки."""
    thread = set()

    # Вверх до корня
    current = msg_id
    while current in msg_by_id:
        thread.add(current)
        parent = msg_by_id[current].get('reply_to_message_id')
        if parent and parent in msg_by_id and parent not in thread:
            current = parent
        else:
            break

    # Вниз: все ответы (BFS)
    queue = list(thread)
    while queue:
        mid = queue.pop(0)
        for child_id in replies_to.get(mid, []):
            if child_id not in thread:
                thread.add(child_id)
                queue.append(child_id)

    return thread

def expand_with_neighbors(thread_ids, window=15, time_limit_sec=1800):
    """Добавить соседние сообщения в пределах ±window по ID и time_limit по времени."""
    expanded = set(thread_ids)
    thread_times = set()
    for mid in thread_ids:
        if mid in msg_by_id:
            thread_times.add(int(msg_by_id[mid].get('date_unixtime', 0)))

    if not thread_times:
        return expanded

    min_time = min(thread_times) - time_limit_sec
    max_time = max(thread_times) + time_limit_sec

    for mid in list(thread_ids):
        for offset in range(-window, window + 1):
            neighbor_id = mid + offset
            if neighbor_id in msg_by_id and neighbor_id not in expanded:
                t = int(msg_by_id[neighbor_id].get('date_unixtime', 0))
                if min_time <= t <= max_time:
                    expanded.add(neighbor_id)

    return expanded
```

### A.4. Кластеризация и верификация DN-релевантности

```python
def cluster_discussions(trigger_ids):
    """Объединить перекрывающиеся треды в дискуссии."""
    clusters = []
    assigned = set()

    for tid in sorted(trigger_ids):
        if tid in assigned:
            continue
        thread = get_thread(tid)
        expanded = expand_with_neighbors(thread)

        # Мержим с существующими кластерами, если есть пересечение
        merged = False
        for cluster in clusters:
            if cluster['ids'] & expanded:
                cluster['ids'] |= expanded
                cluster['triggers'].add(tid)
                merged = True
                break

        if not merged:
            clusters.append({'ids': expanded, 'triggers': {tid}})

        assigned |= expanded

    return clusters

def verify_dn_relevance(cluster):
    """
    ОБЯЗАТЕЛЬНЫЙ ШАГ: проверить, что дискуссия действительно про DN.
    Без этого Tier 2 триггеры дают ~50% ложных срабатываний в больших чатах.
    """
    combined_text = ' '.join(
        get_text(msg_by_id[mid])
        for mid in cluster['ids']
        if mid in msg_by_id
    )
    return bool(DN_VERIFY_RE.search(combined_text))

# Применение
clusters = cluster_discussions(trigger_ids)
verified = [c for c in clusters if verify_dn_relevance(c)]

print(f"До верификации: {len(clusters)} дискуссий")
print(f"После верификации: {len(verified)} дискуссий")
```

### A.5. Формирование выходного JSON

```python
output = {
    "source": f"{channel_name}_{date}",
    "extraction_date": date,
    "discussions": [],
    "stats": {
        "total_messages_scanned": len(messages),
        "trigger_matches": len(trigger_ids),
        "discussions_found": len(clusters),
        "discussions_after_filtering": len(verified),
        "facts_extracted": 0  # заполнить после извлечения
    }
}

for i, cluster in enumerate(verified, 1):
    sorted_ids = sorted(cluster['ids'])
    disc_messages = [
        {
            "id": mid,
            "date": msg_by_id[mid].get('date', ''),
            "from": msg_by_id[mid].get('from', ''),
            "text": get_text(msg_by_id[mid])
        }
        for mid in sorted_ids
        if mid in msg_by_id
    ]

    output["discussions"].append({
        "discussion_id": i,
        "trigger_messages": sorted(cluster['triggers']),
        "all_message_ids": sorted_ids,
        "topic": disc_messages[0]['text'][:150] if disc_messages else "",
        "messages": disc_messages,
        "extracted_facts": []  # заполняется при анализе содержания
    })
```

---

## Приложение B: Антипаттерны

Ошибки, обнаруженные на реальных данных. Не повторять.

### B.1. Tier 2 без верификации = 50% мусора

**Проблема:** `lavoro autonomo` как триггер в чате про Италию (rutoitalychat, 103K сообщений) дал 261 совпадение. Из них больше половины — обсуждения обычного фриланса/ИП без DN-контекста.

**Масштаб:** 529 дискуссий → 269 после верификации. 260 ложных дискуссий, 319 ложных «фактов».

**Решение:** обязательная верификация DN-контекста (шаг 3) по объединённому тексту всей дискуссии, а не только триггерного сообщения.

### B.2. «Виза D» ≠ «DN-виза»

**Проблема:** «виза D» — это любая долгосрочная виза в Италию. Типы: lavoro subordinato, lavoro autonomo, studio, famiglia, elective residence, motivi religiosi, и др. DN (nomade digitale / lavoratore da remoto) — лишь один из подтипов.

**Как проявляется:** дискуссия типа «получил визу D lavoro autonomo, открыл partita IVA, работаю как libero professionista» — это НЕ DN, если нигде в треде не упоминается «номад», «кочевник», «digital nomad», «DN».

**Решение:** `DN_VERIFY_RE` не содержит паттерна `виз\S*\s+[DД]` — это сознательно. «Виза D» используется только как Tier 2 контекстный триггер, но не проходит верификацию сама по себе.

### B.3. Каналы vs чаты — разная стратегия

**Проблема:** для каналов (emigrantista, immigrazia_IT, rutoitaly) верификация практически не отсеивает — авторы каналов пишут целевые посты, если упомянули `lavoro autonomo`, то обычно в контексте DN. Для чатов (rutoitalychat) — отсеивает ~50%.

**Вывод:** для каналов (<10K сообщений) можно обойтись grep + ручной просмотр. Для чатов (>10K) — обязательно Python-скрипт с `DN_VERIFY_RE`.

### B.4. Не грепать по всему JSON построчно

**Проблема:** `grep -n "номад" result.json` работает, но для восстановления `id` сообщения нужно искать ближайшее `"id":` выше по файлу, что ненадёжно (JSON может быть prettified или minified по-разному).

**Решение:** для каналов (<10K сообщений) — grep + Read вокруг совпадения для определения ID. Для чатов (>10K) — загрузить в Python и работать через `msg_by_id` словарь.
