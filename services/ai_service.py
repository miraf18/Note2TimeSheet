"""
AI Service — elaborazione timesheet tramite OpenAI GPT-4.1-mini.
Classificazione automatica per pratica, bilanciamento garantito a 8.00 ore.
"""

import os
import json
import re


def _load_practices():
    """Load practices from practices.json."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, "practices.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return [
            {"code": "6450", "name": "Sviluppo IT", "description": "Programmazione e sviluppo software"},
            {"code": "6447", "name": "Supporto IT", "description": "Assistenza e supporto utenti"},
        ]


def _build_system_prompt(practices):
    practices_block = "\n".join(
        f"  - {p['code']} ({p['name']}): {p['description']}"
        for p in practices
    )
    codes_list = ", ".join(p["code"] for p in practices)

    return f"""Sei un assistente specializzato nella compilazione di timesheet aziendali.

Il tuo compito è ricevere una lista di attività svolte durante la giornata e produrre un timesheet strutturato.

CODICI PRATICA DISPONIBILI:
{practices_block}

REGOLE TASSATIVE:
1. Il totale delle ore DEVE essere ESATTAMENTE 8.00 ore (480 minuti). Mai di più, mai di meno.
2. Ogni voce deve essere classificata con uno dei codici pratica disponibili: {codes_list}
3. Scegli il codice più appropriato in base alla descrizione dell'attività e alle tipologie elencate.
4. Raggruppa attività simili sotto la stessa pratica quando ha senso.
5. Arrotonda le ore in incrementi di 0.25h (15 min). Valori ammessi: 0.25, 0.50, 0.75, 1.00, 1.25, ecc.
6. Le descrizioni devono essere brevi ma complete. Professionali in italiano. Ogni descrizione DEVE iniziare con la lettera maiuscola e terminare con il punto. Esempio corretto: "Sviluppo e test del modulo di autenticazione OAuth2 per l'integrazione con il portale aziendale." Esempio sbagliato: "sviluppo autenticazione"
6.1. Metti inisieme attività similli, se ci sono 3 attività simili con lo stesso numero di pratica, raggruppale in un'unica voce con ore sommate e descrizione che riassume tutte e 3. Esempio: "Sviluppo e test dei moduli di autenticazione OAuth2, Single Sign-On e gestione sessioni per il portale aziendale."
7. Le riunioni Outlook hanno una durata nota in minuti: usala come riferimento preciso. Voglio che le riunioni vengano salvate con "Riunioni:...".
8. Distribuisci il tempo rimanente (dopo le riunioni) tra le attività manuali in modo proporzionale e ragionevole.
9. Se il totale supera 8 ore, comprimi proporzionalmente le voci senza riunioni. 

FORMATO OUTPUT — rispondi ESCLUSIVAMENTE con JSON valido, nessun testo extra, nessun markdown:
{{
  "timesheet": [
    {{
      "pratica": "6450",
      "ore": 2.50,
      "descrizione": "Sviluppo e test delle API REST per il modulo di autenticazione OAuth2 del portale aziendale."
    }}
  ],
  "totale_ore": 8.00,
  "note": ""
}}"""


def _round_quarter(value):
    """Round to nearest 0.25."""
    return round(round(value * 4) / 4, 2)


def _balance_to_8h(items):
    """
    Enforce exactly 8.00h total by adjusting the largest item.
    Operates on a copy of the list.
    """
    items = [dict(i) for i in items]
    total = sum(i["ore"] for i in items)
    delta = round(8.0 - total, 4)

    if abs(delta) < 0.001:
        return items

    # Sort indices by ore descending to find best candidate to adjust
    sorted_idx = sorted(range(len(items)), key=lambda i: items[i]["ore"], reverse=True)

    for idx in sorted_idx:
        new_val = _round_quarter(items[idx]["ore"] + delta)
        if new_val >= 0.25:
            items[idx]["ore"] = new_val
            break
    else:
        # Edge case: add to first item regardless
        items[0]["ore"] = _round_quarter(max(0.25, items[0]["ore"] + delta))

    return items


def _parse_json_response(text):
    """Strip markdown fences if present and parse JSON."""
    text = re.sub(r"```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip().rstrip("`").strip()
    return json.loads(text)


def elaborate_timesheet(entries, user_name, date, practices=None):
    """
    Elaborate freeform entries into a structured timesheet using GPT-4.1-mini.

    Args:
        entries: list of entry dicts (from state_service)
        user_name: str
        date: str (YYYY-MM-DD)
        practices: list of practice dicts (loaded from practices.json if None)

    Returns:
        dict with keys: timesheet (list), totale_ore (float), note (str)

    Raises:
        ValueError: if API key missing, entries empty, or AI response invalid after retry
    """
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("OPENAI_API_KEY non configurato nel .env")

    if not entries:
        raise ValueError("Nessuna attività da elaborare. Aggiungi almeno una voce.")

    if practices is None:
        practices = _load_practices()

    client = OpenAI(api_key=api_key)
    system_prompt = _build_system_prompt(practices)

    # Build user prompt
    lines = []
    for e in entries:
        line = f"- [{e['time']}] {e['text']}"
        if e.get("duration_min"):
            line += f" (durata: {e['duration_min']} min)"
        if e.get("type") == "outlook":
            line += " [Riunione Outlook]"
        lines.append(line)

    user_prompt = (
        f"Data: {date}\n"
        f"Utente: {user_name}\n\n"
        f"ATTIVITÀ DELLA GIORNATA:\n"
        + "\n".join(lines)
        + "\n\nElabora il timesheet. Il totale DEVE essere esattamente 8.00 ore."
    )

    def _call(extra=""):
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": system_prompt + extra},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=1200,
        )
        return response.choices[0].message.content.strip()

    # First attempt
    raw = _call()
    try:
        result = _parse_json_response(raw)
    except json.JSONDecodeError:
        # Retry with stricter instruction
        raw = _call("\n\nIMPORTANTE: Rispondi SOLO con JSON valido. Nessun testo, nessun markdown.")
        try:
            result = _parse_json_response(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Risposta AI non parseable dopo retry: {exc}\nRisposta raw: {raw[:500]}"
            )

    # Validate structure
    if "timesheet" not in result or not isinstance(result.get("timesheet"), list):
        raise ValueError(f"Struttura risposta AI non valida (manca 'timesheet'): {str(result)[:300]}")

    if not result["timesheet"]:
        raise ValueError("L'AI ha restituito un timesheet vuoto.")

    # Enforce correct field types + description formatting
    for item in result["timesheet"]:
        item["pratica"] = str(item.get("pratica", ""))
        item["ore"] = float(item.get("ore", 0))
        desc = str(item.get("descrizione", "")).strip()
        if desc:
            desc = desc[0].upper() + desc[1:]          # Capital first letter
            if not desc.endswith("."):
                desc = desc + "."                       # Trailing period
        item["descrizione"] = desc

    # Server-side 8h enforcement
    result["timesheet"] = _balance_to_8h(result["timesheet"])
    result["totale_ore"] = round(sum(i["ore"] for i in result["timesheet"]), 2)
    result["note"] = result.get("note", "")

    return result
