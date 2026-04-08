# Референсные Python-скрипты для обработки больших чатов

Проверенные скрипты, использовались на реальных данных (апрель 2026). Используй для чатов >10K сообщений (supergroup). Для каналов (<10K) можно обойтись grep.

## Загрузка и индексация дампа

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

## Поиск триггерных сообщений

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

DN_VERIFY_RE = re.compile(
    r'номад|кочевни|digital[\s._-]?nomad|nomad[\s._-]?visa'
    r'|nomad[ie]\s*digital|\bdn\b|виз\S*\s+dn|dn\s+виз'
    r'|27[\s.-]*ter|decreto.*nomad|цифров\S*\s+кочевни',
    re.IGNORECASE
)

TIER2_FP_RE = re.compile(r'сообщение удалено|удалённое место', re.IGNORECASE)

trigger_ids = set()
for m in messages:
    text = get_text(m)
    if TIER1_RE.search(text) or TIER3_RE.search(text):
        trigger_ids.add(m['id'])
    elif TIER2_RE.search(text) and not TIER2_FP_RE.search(text):
        trigger_ids.add(m['id'])
```

## Восстановление тредов (supergroup)

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

## Кластеризация и верификация

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
    Без этого Tier 2 триггеры дают ~50% ложных срабатываний.
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
```

## Формирование выходного JSON

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
        "facts_extracted": 0
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
        "extracted_facts": []
    })
```

## Антипаттерны (из реального опыта)

### Tier 2 без верификации = 50% мусора
`lavoro autonomo` в rutoitalychat (103K сообщений) дал 261 совпадение, >50% — обычный ИП без DN. 529 дискуссий -> 269 после верификации. 260 ложных дискуссий, 319 ложных «фактов».

### «Виза D» ≠ «DN-виза»
Виза D — любая долгосрочная виза (lavoro subordinato, autonomo, studio, famiglia...). DN — лишь один подтип. `DN_VERIFY_RE` сознательно не содержит `виз\S*\s+[DД]`.

### Каналы vs чаты — разная стратегия
Каналы: верификация почти не отсеивает (авторы пишут целевые посты). Чаты: отсеивает ~50%.

### Не грепать по JSON построчно
Для каналов (<10K) — grep + Read для определения ID. Для чатов (>10K) — Python с `msg_by_id`.
