"""AI service: prompt templates with placeholders, OpenAI call, output validation and balancing.

The prompt is made of four editable sections (``DEFAULT_PROMPTS``); ``None`` or an
empty string in the user's settings means "use the built-in default". Placeholders
use double braces (``{{daily_hours}}``) and are replaced by plain string
substitution so JSON braces inside the templates are safe.
"""

from __future__ import annotations

import datetime
import json
import logging
import re
from typing import Any

import config

logger = logging.getLogger(__name__)

PROMPT_FIELDS = ("system_intro", "system_rules", "system_output", "user_template")
SYSTEM_FIELDS = ("system_intro", "system_rules", "system_output")
SECTION_SEPARATOR = "\n\n"
MIN_ITEM_HOURS = 0.25
HOURS_EPSILON = 1e-6
DEFAULT_TEMPERATURE = 0.2
MAX_TOKENS = 1500
MAX_COMMIT_LINES = 30
MAX_COMMIT_MESSAGE_LEN = 120
EMPTY_DESCRIPTION = "Attività non descritta."
NO_PRACTICES_TEXT = "(nessuna pratica configurata)"
NO_ENTRIES_TEXT = "(nessuna attività)"
RETRY_INSTRUCTION = "\n\nIMPORTANTE: rispondi SOLO con JSON valido. Nessun testo, nessun markdown."
_PLACEHOLDER_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_REPO_PREFIX_RE = re.compile(r"^[\w.-]+/[\w.-]+\s+·\s+")
_FENCE_RE = re.compile(r"```(?:json)?", re.IGNORECASE)

DEFAULT_PROMPTS: dict[str, str] = {
    "system_intro": (
        "Sei un assistente specializzato nella compilazione di timesheet aziendali.\n\n"
        "Il tuo compito è ricevere l'elenco delle attività svolte durante la giornata e produrre "
        "un timesheet strutturato, classificando ogni voce con il codice pratica più appropriato.\n\n"
        "CODICI PRATICA DISPONIBILI:\n{{practices}}"
    ),
    "system_rules": (
        "REGOLE TASSATIVE:\n"
        "1. Il totale delle ore DEVE essere ESATTAMENTE {{daily_hours}} ore ({{daily_minutes}} minuti). "
        "Mai di più, mai di meno.\n"
        "2. Ogni voce deve essere classificata con uno dei codici pratica disponibili: {{codes}}\n"
        "3. Scegli il codice in base al CONTENUTO e allo SCOPO dell'attività, confrontandoli con le descrizioni "
        "delle pratiche (comprese le indicazioni \"Quando NON usarla\"). Non assegnare mai una pratica solo perché "
        "il suo nome contiene una parola presente nell'attività (es. \"meeting\", \"riunione\", \"supporto\"): "
        "il nome da solo non basta, conta la descrizione della pratica.\n"
        "4. Raggruppa le attività simili sotto la stessa pratica: se più attività hanno lo stesso codice, "
        "uniscile in un'unica voce con le ore sommate e una descrizione che le riassuma tutte. "
        "Esempio: \"Sviluppo e test dei moduli di autenticazione OAuth2, Single Sign-On e gestione "
        "sessioni per il portale aziendale.\"\n"
        "5. Arrotonda le ore in incrementi di 0.25h (15 minuti). Valori ammessi: 0.25, 0.50, 0.75, 1.00, 1.25, ecc.\n"
        "6. Le descrizioni devono essere brevi ma complete, professionali e in italiano. Ogni descrizione DEVE "
        "iniziare con la lettera maiuscola e terminare con il punto. Esempio corretto: \"Sviluppo e test del "
        "modulo di autenticazione OAuth2 per l'integrazione con il portale aziendale.\" "
        "Esempio sbagliato: \"sviluppo autenticazione\"\n"
        "7. Le riunioni (voci contrassegnate con [Riunione]) hanno una durata nota in minuti: usala come "
        "riferimento preciso. La descrizione di ogni riunione DEVE iniziare con \"Riunione:\" "
        "(esempio: \"Riunione: allineamento settimanale con il team di sviluppo.\"). Classifica ogni riunione "
        "in base al suo ARGOMENTO (allineamento del team, avanzamento di un progetto, fornitori, formazione, "
        "raccolta requisiti, supporto...) esattamente come le altre attività. Se la descrizione di una pratica "
        "pone dei vincoli (giorno della settimana, orario, durata massima, partecipanti), assegnala SOLO se la "
        "riunione li rispetta tutti: verifica il giorno indicato in \"Data\" e l'orario [inizio–fine] della "
        "voce; in caso contrario usa la pratica generica più adatta all'argomento.\n"
        "8. Le voci GitHub (commit e pull request) rappresentano lavoro di sviluppo: usa i messaggi di commit "
        "per descrivere cosa è stato fatto, raggruppando per repository o argomento. Non riportare mai "
        "hash, SHA o URL nella descrizione.\n"
        "9. Distribuisci il tempo rimanente (dopo le riunioni) tra le altre attività in modo proporzionale "
        "e ragionevole.\n"
        "10. Se il totale supera {{daily_hours}} ore, comprimi proporzionalmente le voci che non sono riunioni."
    ),
    "system_output": (
        "FORMATO OUTPUT — rispondi ESCLUSIVAMENTE con JSON valido, nessun testo extra, nessun markdown:\n"
        "{\n"
        "  \"timesheet\": [\n"
        "    {\n"
        "      \"pratica\": \"299111\",\n"
        "      \"ore\": 2.50,\n"
        "      \"descrizione\": \"Sviluppo e test delle API REST per il modulo di autenticazione OAuth2 "
        "del portale aziendale.\"\n"
        "    }\n"
        "  ],\n"
        "  \"totale_ore\": {{daily_hours}},\n"
        "  \"note\": \"\"\n"
        "}"
    ),
    "user_template": (
        "Data: {{date}} ({{weekday}})\n"
        "Utente: {{user_name}}\n\n"
        "ATTIVITÀ DELLA GIORNATA:\n"
        "{{entries}}\n\n"
        "Elabora il timesheet. Il totale DEVE essere esattamente {{daily_hours}} ore."
    ),
}

PLACEHOLDERS: list[dict[str, str]] = [
    {"name": "practices", "token": "{{practices}}",
     "description": "Elenco delle pratiche, una per riga: - codice (nome): descrizione"},
    {"name": "codes", "token": "{{codes}}", "description": "Codici pratica separati da virgola"},
    {"name": "daily_hours", "token": "{{daily_hours}}", "description": "Ore giornaliere da raggiungere (es. 8.00)"},
    {"name": "daily_minutes", "token": "{{daily_minutes}}", "description": "Minuti giornalieri (es. 480)"},
    {"name": "user_name", "token": "{{user_name}}", "description": "Nome dell'utente"},
    {"name": "date", "token": "{{date}}", "description": "Data del giorno elaborato (YYYY-MM-DD)"},
    {"name": "weekday", "token": "{{weekday}}", "description": "Giorno della settimana in italiano (es. martedì)"},
    {"name": "entries", "token": "{{entries}}", "description": "Attività della giornata già formattate"},
]


def reset_cache() -> None:
    """No in-memory cache is kept; present for the shared test fixture contract."""
    return None


# ---------------------------------------------------------------------------
# Prompt sections & placeholders
# ---------------------------------------------------------------------------

def effective_prompts(ai_settings: dict | None) -> dict[str, str]:
    """The four prompt sections actually used (``None``/blank → built-in default)."""
    settings = ai_settings if isinstance(ai_settings, dict) else {}
    return {field: _section_or_default(field, settings.get(field)) for field in PROMPT_FIELDS}


def _section_or_default(field: str, value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value
    return DEFAULT_PROMPTS[field]


def format_hours(hours: Any) -> str:
    return f"{float(hours):.2f}"


def daily_minutes(hours: Any) -> int:
    return int(round(float(hours) * 60))


def render_practices(practices: list[dict]) -> str:
    lines = [_render_practice(p) for p in practices or [] if isinstance(p, dict)]
    return "\n".join(lines) if lines else NO_PRACTICES_TEXT


def _render_practice(practice: dict) -> str:
    head = f"- {practice.get('code', '')} ({practice.get('name', '')})"
    description = " ".join(str(practice.get("description") or "").split())
    return f"{head}: {description}" if description else head


def render_codes(practices: list[dict]) -> str:
    return ", ".join(str(p.get("code", "")) for p in practices or [] if isinstance(p, dict) and p.get("code"))


WEEKDAYS_IT = ("lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica")


def weekday_name(date_iso: Any) -> str:
    """Italian weekday for an ISO date (``""`` when the date is not valid)."""
    try:
        return WEEKDAYS_IT[datetime.date.fromisoformat(str(date_iso)).weekday()]
    except (TypeError, ValueError):
        return ""


def placeholder_values(entries: list[dict], practices: list[dict], user_name: str,
                       date: str, daily_hours: float) -> dict[str, str]:
    return {
        "practices": render_practices(practices),
        "codes": render_codes(practices),
        "daily_hours": format_hours(daily_hours),
        "daily_minutes": str(daily_minutes(daily_hours)),
        "user_name": str(user_name or ""),
        "date": str(date or ""),
        "weekday": weekday_name(date),
        "entries": render_entries(entries),
    }


def fill_placeholders(template: str, values: dict[str, str]) -> str:
    """Replace ``{{name}}`` tokens (unknown names are left untouched)."""
    return _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), template or "")


def build_prompts(entries: list[dict], practices: list[dict], user_name: str, date: str,
                  daily_hours: float, ai_settings: dict | None) -> tuple[str, str]:
    """Return ``(system_prompt, user_prompt)`` with all placeholders filled."""
    prompts = effective_prompts(ai_settings)
    values = placeholder_values(entries, practices, user_name, date, daily_hours)
    system_prompt = SECTION_SEPARATOR.join(fill_placeholders(prompts[f], values) for f in SYSTEM_FIELDS)
    return system_prompt, fill_placeholders(prompts["user_template"], values)


# ---------------------------------------------------------------------------
# Entry rendering
# ---------------------------------------------------------------------------

def _first_line(message: Any, limit: int = MAX_COMMIT_MESSAGE_LEN) -> str:
    lines = [line.strip() for line in str(message or "").splitlines() if line.strip()]
    first = lines[0] if lines else ""
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


def _render_manual(entry: dict) -> str:
    lines = [line.strip() for line in str(entry.get("text") or "").splitlines() if line.strip()] or [""]
    head = f"- [{entry.get('time') or ''}] {lines[0]}"
    return "\n".join([head, *(f"    {line}" for line in lines[1:])])


def _render_meeting(entry: dict) -> str:
    meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
    start, end = meta.get("start"), meta.get("end")
    when = f"{start}–{end}" if start and end else (entry.get("time") or "")
    text = " ".join(str(entry.get("text") or "").split())
    if text.lower().startswith("riunione:"):
        text = text[len("riunione:"):].strip()
    duration = entry.get("duration_min")
    duration_part = f" (durata: {int(duration)} min)" if isinstance(duration, (int, float)) and duration else ""
    return f"- [{when}] Riunione: {text}{duration_part} [Riunione]"


def _render_commits(repo: str, commits: list) -> str:
    shown = [c for c in commits[:MAX_COMMIT_LINES] if isinstance(c, dict)]
    lines = [f"    • {_first_line(c.get('message'))}" for c in shown]
    if len(commits) > MAX_COMMIT_LINES:
        lines.append(f"    • … e altri {len(commits) - MAX_COMMIT_LINES}")
    return "\n".join([f"- [GitHub {repo}] {len(commits)} commit:", *lines])


def _render_github(entry: dict) -> str:
    meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
    repo = str(meta.get("repo") or "GitHub")
    commits = meta.get("commits")
    if meta.get("kind") == "commits" and isinstance(commits, list) and commits:
        return _render_commits(repo, commits)
    text = " ".join(str(entry.get("text") or "").split())
    if text.startswith(f"{repo} · "):
        text = text[len(repo) + 3:]
    return f"- [GitHub {repo}] {_REPO_PREFIX_RE.sub('', text)}"


def render_entries(entries: list[dict]) -> str:
    """One block per entry, in the format the prompt rules refer to."""
    renderers = {"meeting": _render_meeting, "outlook": _render_meeting, "github": _render_github}
    rendered = [renderers.get(str(e.get("type")), _render_manual)(e) for e in entries or [] if isinstance(e, dict)]
    return "\n".join(rendered) if rendered else NO_ENTRIES_TEXT


# ---------------------------------------------------------------------------
# Balancing (pure)
# ---------------------------------------------------------------------------

def round_quarter(value: Any) -> float:
    """Round to the nearest 0.25."""
    return round(round(float(value) * 4) / 4, 2)


def _hours_of(item: dict) -> float:
    try:
        return float(item.get("ore", 0))
    except (TypeError, ValueError):
        return 0.0


def balance_to_total(items: list[dict], total_hours: float) -> list[dict]:
    """Return new items whose ``ore`` sum to ``total_hours`` (quarter-hour steps, min 0.25 each).

    Every item is first rounded to a quarter; the difference is applied to the
    largest item, cascading to the next ones when an item would drop below 0.25.
    """
    total = float(total_hours)
    if total <= 0:
        raise ValueError("Il totale ore deve essere maggiore di zero.")
    if not items:
        return []
    hours = [max(MIN_ITEM_HOURS, round_quarter(_hours_of(item))) for item in items]
    remaining = round(total - sum(hours), 4)
    for idx in sorted(range(len(hours)), key=lambda i: hours[i], reverse=True):
        if abs(remaining) < HOURS_EPSILON:
            break
        target = max(MIN_ITEM_HOURS, round_quarter(hours[idx] + remaining))
        remaining = round(remaining - (target - hours[idx]), 4)
        hours[idx] = target
    if abs(remaining) >= HOURS_EPSILON:
        logger.warning("Cannot balance timesheet exactly to %.2f h (residual %.2f h)", total, remaining)
    return [{**item, "ore": round(h, 2)} for item, h in zip(items, hours)]


# ---------------------------------------------------------------------------
# OpenAI call
# ---------------------------------------------------------------------------

def _temperature(ai_settings: dict | None) -> float:
    value = (ai_settings or {}).get("temperature", DEFAULT_TEMPERATURE) if isinstance(ai_settings, dict) else None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 2:
        return float(value)
    if value is not None:
        logger.warning("Invalid AI temperature %r, using %.1f", value, DEFAULT_TEMPERATURE)
    return DEFAULT_TEMPERATURE


def _is_response_format_rejection(exc: Exception) -> bool:
    try:
        from openai import BadRequestError
    except ImportError:
        return False
    return isinstance(exc, BadRequestError) and "response_format" in str(exc).lower()


def _complete(client: Any, model: str, system_prompt: str, user_prompt: str, temperature: float) -> str:
    """One chat completion; falls back to no ``response_format`` when unsupported."""
    kwargs = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        "temperature": temperature,
        "max_tokens": MAX_TOKENS,
    }
    try:
        response = client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
    except TypeError:
        logger.info("response_format not supported by the OpenAI SDK; retrying without it")
        response = client.chat.completions.create(**kwargs)
    except Exception as exc:
        if not _is_response_format_rejection(exc):
            raise
        logger.info("Model %s rejected response_format; retrying without it", model)
        response = client.chat.completions.create(**kwargs)
    return _response_text(response)


def _response_text(response: Any) -> str:
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise ValueError("Risposta AI vuota o in un formato inatteso.") from exc
    return (content or "").strip()


def _try_parse(raw: str) -> dict | None:
    """Parse the model output as a JSON object; ``None`` when impossible."""
    cleaned = _FENCE_RE.sub("", raw or "").strip().strip("`").strip()
    for candidate in (cleaned, _extract_object(cleaned)):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _extract_object(text: str) -> str:
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if 0 <= start < end else ""


# ---------------------------------------------------------------------------
# Output validation
# ---------------------------------------------------------------------------

def format_description(text: Any) -> str:
    """Capitalise the first letter and make sure the sentence ends with a period."""
    clean = " ".join(str(text or "").split())
    if not clean:
        return EMPTY_DESCRIPTION
    clean = clean[0].upper() + clean[1:]
    return clean if clean[-1] in ".!?" else clean + "."


def _parse_hours(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(str(value).strip().replace(",", ".")) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        return None


def _normalise_item(item: Any, index: int) -> dict | None:
    if not isinstance(item, dict):
        logger.warning("Skipping AI timesheet item #%d: not an object", index)
        return None
    hours = _parse_hours(item.get("ore"))
    if hours is None or hours <= 0:
        logger.warning("Skipping AI timesheet item #%d: invalid hours %r", index, item.get("ore"))
        return None
    return {
        "pratica": str(item.get("pratica") or "").strip(),
        "ore": hours,
        "descrizione": format_description(item.get("descrizione")),
    }


def _compose_note(ai_note: Any, unknown_codes: list[str]) -> str:
    base = " ".join(str(ai_note).split()) if isinstance(ai_note, str) else ""
    flags = [f"Codice {code} non presente nell'elenco pratiche." for code in unknown_codes]
    return " ".join(part for part in [base, *flags] if part)


def _finalise(parsed: dict, practices: list[dict], daily_hours: float) -> dict:
    timesheet = parsed.get("timesheet")
    if not isinstance(timesheet, list) or not timesheet:
        raise ValueError("Struttura della risposta AI non valida: manca l'elenco 'timesheet'.")
    items = [i for i in (_normalise_item(item, n) for n, item in enumerate(timesheet)) if i]
    if not items:
        raise ValueError("L'AI ha restituito un timesheet senza voci valide.")
    known = {str(p.get("code")) for p in practices if isinstance(p, dict)}
    unknown = list(dict.fromkeys(i["pratica"] for i in items if i["pratica"] not in known))
    if unknown:
        logger.warning("AI used unknown practice codes: %s", ", ".join(unknown))
    balanced = balance_to_total(items, daily_hours)
    return {
        "timesheet": balanced,
        "totale_ore": round(sum(i["ore"] for i in balanced), 2),
        "note": _compose_note(parsed.get("note"), unknown),
    }


def _check_inputs(entries: list, practices: list, daily_hours: Any, api_key: Any) -> None:
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("Chiave API OpenAI non configurata. Inseriscila nelle impostazioni "
                         "oppure nella variabile OPENAI_API_KEY.")
    if not entries:
        raise ValueError("Nessuna attività da elaborare. Aggiungi almeno una voce.")
    if not practices:
        raise ValueError("Nessuna pratica configurata. Aggiungi almeno una pratica nelle impostazioni.")
    try:
        hours = float(daily_hours)
    except (TypeError, ValueError) as exc:
        raise ValueError("Ore giornaliere non valide.") from exc
    if hours <= 0:
        raise ValueError("Ore giornaliere non valide.")


def elaborate_timesheet(entries: list[dict], practices: list[dict], user_name: str, date: str,
                        daily_hours: float, ai_settings: dict | None, model: str, api_key: str) -> dict:
    """Turn the day's entries into ``{"timesheet","totale_ore","note"}`` via OpenAI.

    Raises ``ValueError`` for configuration/validation problems (missing key, no
    entries, unusable AI output after one retry); OpenAI errors are re-raised.
    """
    _check_inputs(entries, practices, daily_hours, api_key)
    from openai import OpenAI

    client = OpenAI(api_key=api_key.strip())
    system_prompt, user_prompt = build_prompts(entries, practices, user_name, date, daily_hours, ai_settings)
    temperature = _temperature(ai_settings)
    model_name = model.strip() if isinstance(model, str) and model.strip() else config.DEFAULT_MODEL

    hours = float(daily_hours)
    raw = _complete(client, model_name, system_prompt, user_prompt, temperature)
    result, problem = _parse_and_finalise(raw, practices, hours)
    if result is not None:
        return result
    logger.warning("AI response unusable (%s); retrying once (raw: %r)", problem, (raw or "")[:200])
    raw = _complete(client, model_name, system_prompt + RETRY_INSTRUCTION, user_prompt, temperature)
    result, problem = _parse_and_finalise(raw, practices, hours)
    if result is not None:
        return result
    raise ValueError(f"{problem} (anche dopo un nuovo tentativo).")


def _parse_and_finalise(raw: str, practices: list[dict], hours: float) -> "tuple[dict | None, str | None]":
    """Parse + validate one model reply → ``(result, None)`` or ``(None, problem)``."""
    parsed = _try_parse(raw)
    if parsed is None:
        return None, "La risposta dell'AI non è in formato JSON valido"
    try:
        return _finalise(parsed, practices, hours), None
    except ValueError as exc:
        return None, str(exc).rstrip(".")
