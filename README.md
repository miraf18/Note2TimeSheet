# ⏱ TimeSheet App

Applicazione desktop locale per registrare le attività giornaliere e generare **timesheet professionali** tramite AI (OpenAI GPT-4.1-mini).

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)
![Flask](https://img.shields.io/badge/Flask-3.0-black?logo=flask)
![OpenAI](https://img.shields.io/badge/OpenAI-GPT--4.1--mini-412991?logo=openai)
![License](https://img.shields.io/badge/License-MIT-green)

---

## Funzionalità

- 📝 **Registrazione rapida** attività durante la giornata (testo libero)
- 📅 **Importazione riunioni Outlook** con deduplicazione automatica
- 🤖 **Elaborazione AI** — trasforma appunti informali in timesheet strutturati
- ⚖️ **Bilanciamento automatico a 8 ore** esatte, garantito lato server
- 🏷️ **Pratiche dinamiche** — aggiungi/modifica/elimina i codici pratica (con descrizioni per l'AI)
- ✏️ **Tabella editabile** — modifica ore e descrizioni prima di copiare
- 📋 **Copia JSON** — output pronto per integrazione con altri sistemi
- 🌙 **Dark / Light mode** con persistenza locale
- 🔗 **Webhook n8n** opzionale (disabilitato di default)

---

## Struttura del Progetto

```
timesheet-app/
├── app.py                  # Flask app — tutte le route API
├── practices.json          # Codici pratica (editabili dall'UI)
├── requirements.txt
├── .env.example            # Template variabili d'ambiente
├── services/
│   ├── ai_service.py       # Elaborazione OpenAI GPT-4.1-mini
│   ├── state_service.py    # Stato giornaliero su file JSON
│   ├── outlook_service.py  # Importazione riunioni Outlook (COM)
│   └── webhook_service.py  # Invio dati a webhook esterno
└── templates/
    └── index.html          # UI completa (CSS + JS inline)
```

---

## Installazione

### 1. Clona il repo

```bash
git clone https://github.com/<tuo-username>/timesheet-app.git
cd timesheet-app
```

### 2. Crea un virtualenv e installa le dipendenze

```bash
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

pip install -r requirements.txt
```

> **Nota:** `pywin32` è richiesto solo su Windows per l'integrazione Outlook.

### 3. Configura le variabili d'ambiente

```bash
cp .env.example .env
```

Apri `.env` e compila:

```env
APP_PORT=5599
USER_NAME=Il Tuo Nome
OUTLOOK_ACCOUNT=tuo@email.com
OPENAI_API_KEY=sk-...
ENABLE_WEBHOOK=false
WEBHOOK_URL=https://...
```

### 4. Avvia l'app

```bash
python app.py
```

Il browser si apre automaticamente su `http://localhost:5599`.

---

## Pratiche

Le pratiche sono i codici che identificano le tipologie di attività nel timesheet.
Vengono gestite tramite il pannello ⚙️ nell'app e salvate in `practices.json`.

Esempio di default:

| Codice | Nome | Descrizione (usata dall'AI per classificare) |
|--------|------|---------------------------------------------|
| 6450 | Programmazione | Sviluppo software, coding, debugging, deploy... |
| 6447 | Supporto IT | Assistenza utenti, help desk, troubleshooting... |

---

## API

| Metodo | Endpoint | Descrizione |
|--------|----------|-------------|
| GET | `/api/config` | Configurazione app |
| GET | `/api/entries` | Lista attività del giorno |
| POST | `/api/entry` | Aggiungi attività |
| PUT | `/api/entry/<id>` | Modifica attività |
| DELETE | `/api/entry/<id>` | Elimina attività |
| POST | `/api/outlook` | Importa riunioni Outlook |
| POST | `/api/elaborate` | Elabora timesheet con AI |
| GET | `/api/elaborate` | Ottieni ultima elaborazione |
| GET | `/api/practices` | Lista pratiche |
| POST | `/api/practices` | Aggiungi pratica |
| PUT | `/api/practices/<code>` | Aggiorna pratica |
| DELETE | `/api/practices/<code>` | Elimina pratica |

---

## Webhook n8n (opzionale)

Il flusso n8n originale è **disabilitato di default**.
Per riattivarlo imposta nel `.env`:

```env
ENABLE_WEBHOOK=true
WEBHOOK_URL=https://tuo-n8n.cloud/webhook/...
```

---

## Requisiti

- Python 3.10+
- Windows (per Outlook via `pywin32`) — su altri OS Outlook è disabilitato automaticamente
- Chiave API OpenAI

---

## License

MIT
