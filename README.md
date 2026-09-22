# Note2TimeSheet v2

Applicazione locale (single-user) che trasforma le note scritte al volo durante la giornata in un timesheet ordinato, professionale e gia' associato al codice pratica corretto.

Durante il giorno scrivi le attivita' in testo libero, anche in modo informale. Con un clic importi i commit e le pull request di GitHub e le riunioni del calendario Microsoft 365. A fine giornata l'AI (OpenAI) raggruppa tutto, scrive descrizioni pulite, assegna il codice pratica piu' adatto e bilancia il totale alle ore giornaliere configurate (default `8.00`). Il risultato si rivede, si corregge inline e si copia dove serve.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)
![Flask](https://img.shields.io/badge/Flask-3.x-black?logo=flask)
![OpenAI](https://img.shields.io/badge/OpenAI-gpt--4.1--mini-412991?logo=openai)
![Docker](https://img.shields.io/badge/Docker-porta%205600-2496ED?logo=docker)
![License](https://img.shields.io/badge/License-MIT-green)

> Documento di riferimento per architettura, contratti dei moduli e API: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Screenshot

> Gli screenshot della v1 sono stati rimossi perche' non rispecchiano piu' l'interfaccia.
> Aggiungi immagini aggiornate in `docs/screenshots/` e collegale qui; prima di pubblicarle
> verifica che non mostrino nomi, email, codici cliente o informazioni interne.

## Cosa fa

- Registra attivita' giornaliere in testo libero, senza obbligarti a compilare subito un timesheet formale.
- **GitHub**: collega il tuo account (OAuth *Device Flow* o Personal Access Token), scegli i repository da monitorare e importa con un clic i commit, le pull request (e opzionalmente le issue) del giorno.
- **Microsoft 365**: collega il tuo account (device code flow, permesso delegato `Calendars.Read`) e importa le riunioni del giorno con durata e orario. Su Windows nativo resta disponibile il fallback su Outlook desktop (COM).
- Classifica automaticamente le attivita' in base ai codici pratica che definisci tu, con descrizioni che aiutano l'AI a scegliere.
- Trasforma appunti disordinati in descrizioni professionali in italiano, pronte da copiare in un gestionale.
- Bilancia il risultato al totale giornaliero configurato, con incrementi di 0,25 h.
- **Storico giornaliero**: ogni giorno ha il suo file; puoi tornare indietro, rielaborare e correggere.
- Tabella finale modificabile inline (pratica, ore e descrizione) con salvataggio automatico, piu' **Copia JSON**.
- **Impostazioni dall'interfaccia**: nome, ore giornaliere, fuso orario, modello e chiave OpenAI, integrazioni, pratiche e persino il prompt dell'AI, tutto senza riavviare.

## Come funziona

1. Durante la giornata aggiungi attivita' con testo libero (note veloci, ticket, supporti, sviluppi).
2. Con i pulsanti accanto al campo di testo importi i commit/PR di GitHub e le riunioni del calendario.
3. Premi **Elabora**: l'app invia all'AI le attivita', le riunioni con la loro durata reale, i commit raggruppati per repository e l'elenco delle pratiche.
4. Il modello raggruppa le attivita' simili, scrive descrizioni pulite e assegna a ogni voce il codice pratica piu' adatto.
5. Il backend valida il risultato, forza il totale alle ore giornaliere e lo salva nello storico del giorno.
6. Correggi inline se serve e copia il JSON (`{data, utente, timesheet:[{pratica, ore, descrizione}], totale_ore}`).

## Novita' della v2

| Area | v2 |
|------|----|
| GitHub | Connessione con OAuth Device Flow o token personale, scelta repository, import commit / pull request / issue |
| Microsoft | Connessione account Microsoft 365 (MSAL device code), import riunioni, disconnessione dalle impostazioni |
| Impostazioni | Pannello con schede *Generale*, *Integrazioni*, *Pratiche*, *Prompt AI*; tutto salvato in `data/settings.json` |
| Prompt AI | Prompt suddiviso in sezioni modificabili con segnaposto `{{...}}`, ripristino default e anteprima |
| Storico | Modifiche manuali alla tabella salvate per giorno |
| Avvio | `start_timesheet.bat` (crea venv, installa, avvia) oppure Docker sulla porta **5600** |
| Rimosso | Tutto cio' che riguardava n8n / webhook / callback |

## Avvio rapido

### Requisiti

- Windows, macOS o Linux con **Python 3.10+** (per l'avvio nativo) **oppure** Docker.
- Una chiave API OpenAI (vedi [guida](#c-openai-chiave-api)).
- Facoltativi: un Client ID GitHub e/o una App registration Azure per le integrazioni (guide piu' sotto). Entrambi si possono inserire anche dopo, dalle impostazioni.

### A) Windows: doppio clic su `start_timesheet.bat`

Il launcher e' pensato per chi non vuole toccare il terminale:

1. cerca Python (`py -3`, poi `python`) e verifica che sia almeno la 3.10;
2. crea l'ambiente virtuale `.venv` se non esiste;
3. installa le dipendenze **solo** quando `requirements.txt` e' piu' recente del file di controllo `.venv\.deps-installed`;
4. avvia `app.py`, che apre il browser su `http://localhost:5600`.

Se qualcosa va storto il messaggio resta visibile nella finestra (`pause`) invece di sparire.
`start_timesheet_min.bat` fa la stessa cosa ma in una finestra ridotta a icona e restituisce subito il controllo: comodo per un collegamento nella cartella Esecuzione automatica.

Al primo avvio copia `.env.example` in `.env` (facoltativo: tutto e' impostabile anche dall'interfaccia).

### B) Manuale (qualsiasi sistema operativo)

```bash
git clone https://github.com/<tuo-username>/Note2TimeSheet.git
cd Note2TimeSheet
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env      # cp .env.example .env su macOS/Linux
python app.py
```

L'app e' raggiungibile su `http://localhost:5600` (porta modificabile con `APP_PORT`).

### C) Docker

La v2 e' pensata per **convivere con la v1**: il servizio si chiama `timesheet-v2`, il container `timesheet-app-v2`, l'immagine `timesheet-app:v2` e la porta e' la **5600**. Il vecchio container `timesheet-app` sulla 5599 non viene toccato; quando non ti serve piu' fermalo con `docker stop timesheet-app`.

```bash
copy .env.example .env        # compila almeno OPENAI_API_KEY, oppure inseriscila poi dall'interfaccia
docker compose up -d --build
```

Apri `http://localhost:5600`. I dati modificabili (impostazioni, segreti, pratiche, storico, cache token Microsoft) stanno in una cartella del PC montata come volume: di default `./data`, oppure la cartella indicata in `TIMESHEET_HOST_DATA_DIR` nel `.env` (ad esempio `C:/Users/<tu>/Desktop/Note2Timesheet`). Puoi ricostruire l'immagine quando vuoi senza perdere nulla.

```bash
docker compose up -d --build   # aggiorna dopo modifiche al codice
docker compose logs -f         # log
docker compose restart
docker compose down            # ferma e rimuove il container (i dati in ./data restano)
```

Note per Docker:

- `AUTO_OPEN_BROWSER`, `APP_PORT`, `APP_HOST`, `TIMESHEET_DATA_DIR` e `TZ` sono forzati dal `docker-compose.yml`; il resto arriva dal tuo `.env`, che **non** viene copiato nell'immagine.
- Il fallback Outlook desktop (COM) non e' disponibile nel container: usa l'integrazione **Account Microsoft**.
- L'immagine ha un `HEALTHCHECK` su `/api/config` (`docker inspect --format '{{.State.Health.Status}}' timesheet-app-v2`).

## Configurazione

### Variabili d'ambiente (`.env`)

Tutte facoltative. Servono come valore iniziale al primo avvio: quello che salvi poi dalle impostazioni ha la precedenza. Il template documentato e' [`.env.example`](.env.example).

| Variabile | Scopo | Default |
|-----------|-------|---------|
| `APP_PORT` | Porta HTTP locale | `5600` |
| `APP_HOST` | Indirizzo di ascolto: `127.0.0.1` = solo questo PC (l'app non ha login), `0.0.0.0` = anche dalla rete | `127.0.0.1` (Docker: `0.0.0.0`) |
| `AUTO_OPEN_BROWSER` | Apre il browser all'avvio (`true`/`false`) | `true` |
| `USER_NAME` | Nome utente iniziale | |
| `TIMESHEET_TIMEZONE` | Fuso orario IANA della giornata | `Europe/Rome` |
| `OPENAI_API_KEY` | Chiave OpenAI (la chiave inserita dall'interfaccia vince) | |
| `OPENAI_MODEL` | Modello OpenAI iniziale | `gpt-4.1-mini` |
| `GITHUB_CLIENT_ID` | Client ID della OAuth App GitHub con Device Flow | |
| `GRAPH_CLIENT_ID` | Application (client) ID della App registration Azure | |
| `GRAPH_TENANT_ID` | Tenant Azure: `common`, `organizations`, `consumers` o GUID | `common` |
| `OUTLOOK_ACCOUNT` | Account Outlook desktop per il fallback COM (solo Windows) | profilo predefinito |
| `TIMESHEET_DATA_DIR` | Cartella dati per l'avvio locale (`.bat` / `python app.py`) | `./data` |
| `TIMESHEET_HOST_DATA_DIR` | Cartella del PC montata dal container Docker su `/app/data` (letta da `docker-compose.yml`) | `./data` |
| `HISTORY_KEEP_DAYS` | Giorni di storico conservati (1-365) | `30` |

### Dalle impostazioni nell'app (icona ingranaggio)

- **Generale**: nome utente, ore giornaliere (il totale a cui l'AI bilancia il timesheet), fuso orario, giorni di storico, modello OpenAI, chiave OpenAI (campo in sola scrittura: l'app mostra solo se e' configurata e da dove, impostazioni o `.env`), tema chiaro/scuro.
- **Integrazioni**:
  - *GitHub*: stato della connessione (avatar, login, metodo), **Connetti con GitHub** (device flow: l'app mostra un codice da inserire su github.com), oppure **usa un token personale**; **Disconnetti**; campo *Client ID*; selettore dei repository da monitorare (ricerca, spunta, **Aggiorna elenco**, **Salva repository**; nessun repository selezionato = tutti quelli in cui hai attivita'); interruttori per commit / pull request / issue.
  - *Microsoft*: stato (account collegato), **Connetti account Microsoft** (device code), **Disconnetti**; campi *Client ID* e *Tenant*; sorgente riunioni: *Automatico* (account Microsoft se collegato, altrimenti Outlook desktop se disponibile), *Account Microsoft*, *Outlook desktop*.
- **Pratiche**: elenco dei codici pratica con nome e descrizione, modifica inline, aggiunta ed eliminazione. Piu' sono chiare le descrizioni ("quando usarla / quando non usarla"), migliore e' la classificazione.
- **Prompt AI**: quattro sezioni modificabili (Introduzione, Regole, Formato output, Messaggio utente), ognuna con **Ripristina default** e badge *modificato*; chip dei segnaposto da inserire con un clic; **Anteprima prompt del giorno** che mostra esattamente cosa verrebbe inviato al modello.

Segnaposto disponibili nel prompt:

| Segnaposto | Valore |
|------------|--------|
| `{{practices}}` | una riga per pratica: `- <codice> (<nome>): <descrizione>` |
| `{{codes}}` | codici separati da virgola |
| `{{daily_hours}}` / `{{daily_minutes}}` | totale giornaliero, es. `8.00` / `480` |
| `{{user_name}}` | nome utente |
| `{{date}}` | giorno in formato `YYYY-MM-DD` |
| `{{weekday}}` | giorno della settimana in italiano (es. `martedì`) |
| `{{entries}}` | attivita' della giornata gia' formattate |

## Guide passo-passo

### (a) GitHub: OAuth App con Device Flow

Serve un solo dato, il **Client ID** (nessun secret), perche' l'app usa il *Device Flow*.

1. Vai su <https://github.com/settings/developers> → **OAuth Apps** → **New OAuth App**.
2. Compila: *Application name* (es. `Note2TimeSheet`), *Homepage URL* e *Authorization callback URL* con un valore qualsiasi, ad esempio `http://localhost:5600` (con il Device Flow il callback non viene usato).
3. **Register application**. Nella pagina dell'app appena creata spunta **Enable Device Flow** e salva (*Update application*).
4. Copia il **Client ID** e incollalo in *Impostazioni → Integrazioni → GitHub → Client ID* (oppure in `GITHUB_CLIENT_ID` nel `.env`).
5. Premi **Connetti con GitHub**: l'app mostra un codice e apre `https://github.com/login/device`; inserisci il codice, autorizza e torna all'app. Scopi richiesti: `repo` (per i repository privati) e `read:user`.
6. Scegli i repository da monitorare e salva.

**Alternativa senza OAuth App**: crea un *Personal Access Token (classic)* su <https://github.com/settings/tokens> con gli scope `repo` e `read:user`, poi incollalo in *Integrazioni → GitHub → Oppure usa un token personale → Collega*. Il token viene salvato solo in locale (`data/secrets.json`).

### (b) Microsoft 365: App registration su Azure

1. Vai su <https://portal.azure.com> → **Microsoft Entra ID** (Azure Active Directory) → **App registrations** → **New registration**.
2. *Name*: es. `Note2TimeSheet`. *Supported account types*: scegli in base al tuo caso (solo la tua organizzazione, qualsiasi organizzazione, oppure anche account personali). Nessun *Redirect URI* e' necessario. **Register**.
3. **Authentication** → sezione *Advanced settings* → **Allow public client flows** = **Yes** → *Save* (indispensabile per il device code flow).
4. **API permissions** → **Add a permission** → **Microsoft Graph** → **Delegated permissions** → spunta **Calendars.Read** (resta anche `User.Read`, aggiunto di default) → *Add permissions*.
5. Se il tenant lo richiede, premi **Grant admin consent for [nome tenant]** (serve un amministratore).
6. Da **Overview** copia **Application (client) ID** e **Directory (tenant) ID**; incollali in *Impostazioni → Integrazioni → Microsoft* (o in `GRAPH_CLIENT_ID` / `GRAPH_TENANT_ID`). Per il tenant puoi usare anche `common` (qualsiasi account) o `organizations`.
7. Premi **Connetti account Microsoft**: inserisci il codice su <https://microsoft.com/devicelogin>, accedi, accetta i permessi.

> In un tenant aziendale e' frequente che il consenso utente sia disabilitato: in quel caso al login compare un errore del tipo *"Need admin approval"* (AADSTS65001 / AADSTS90094) e un amministratore deve approvare l'app (punto 5) o la richiesta di consenso.

### (c) OpenAI: chiave API

1. Vai su <https://platform.openai.com/api-keys> → **Create new secret key**.
2. Copia la chiave (`sk-...`) e incollala in *Impostazioni → Generale → Chiave OpenAI* (salvata in `data/secrets.json`), oppure in `OPENAI_API_KEY` nel `.env`.
3. Verifica che l'account abbia credito/fatturazione attiva: un errore 401 o 429 all'elaborazione dipende quasi sempre da chiave errata o quota esaurita.

## Cartella dati e privacy

Tutto cio' che l'app scrive sta in un'unica cartella (`TIMESHEET_DATA_DIR`, default `./data`, in Docker `/app/data`), gia' esclusa da Git e dall'immagine Docker:

```text
data/
|-- settings.json            # impostazioni non segrete (generale, ai, github, microsoft)
|-- secrets.json             # chiave OpenAI e token GitHub (permessi 600 dove supportato)
|-- practices.json           # pratiche (copiato da ./practices.json al primo avvio)
|-- .timesheet_history/      # un file JSON per giorno: attivita', id importati, elaborazione
|   `-- 2026-09-16.json
`-- .graph_token_cache.bin   # cache token MSAL (account Microsoft)
```

- **Nessun dato lascia il tuo PC** tranne: le attivita' del giorno inviate a OpenAI per l'elaborazione, le chiamate alle API GitHub e Microsoft Graph con il tuo token.
- `secrets.json` e `.graph_token_cache.bin` contengono credenziali: non copiarli in chat, ticket o repository. **Disconnetti** dalle impostazioni li svuota/rimuove.
- Le API dell'app non restituiscono mai segreti (la chiave OpenAI e' in sola scrittura).
- Lo storico contiene dati reali di lavoro: per resettare basta cancellare i file in `.timesheet_history/` (vengono comunque conservati solo gli ultimi `history_keep_days` giorni).
- Il file `.env` e la cartella `data/` sono nel `.gitignore`: non pubblicarli.

## API

Tutte le risposte sono JSON. In caso di errore: `{"success": false, "error": "<messaggio>"}` con codice HTTP appropriato (400 validazione, 401 autenticazione richiesta, 404 non trovato, 502 servizio esterno, 500 inatteso). Le rotte che riguardano un giorno accettano `?date=YYYY-MM-DD` o `date` nel body (assente/non valida → oggi). Dettagli completi delle forme JSON in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#7-http-api).

| Metodo | Endpoint | Descrizione |
|--------|----------|-------------|
| GET | `/` | Interfaccia web |
| GET | `/api/config` | Configurazione: nome app/versione, utente, ore giornaliere, fuso, oggi, stato OpenAI e integrazioni |
| GET | `/api/days` | Giorni presenti nello storico (`date`, `entry_count`, `elaborated`) |
| GET | `/api/entries?date=` | Attivita' del giorno |
| POST | `/api/entry` | Aggiunge un'attivita' manuale (`text`, `date`) |
| PUT | `/api/entry/<id>` | Modifica un'attivita' |
| DELETE | `/api/entry/<id>?date=` | Elimina un'attivita' |
| POST | `/api/elaborate` | Genera il timesheet con l'AI per il giorno |
| GET | `/api/elaborate?date=` | Ultima elaborazione del giorno |
| PUT | `/api/elaborate` | Salva le modifiche manuali alla tabella (ricalcola `totale_ore`) |
| GET | `/api/practices` | Elenco pratiche |
| POST | `/api/practices` | Crea una pratica (`code`, `name`, `description`) |
| PUT | `/api/practices/<code>` | Modifica una pratica |
| DELETE | `/api/practices/<code>` | Elimina una pratica |
| GET | `/api/settings` | Impostazioni, prompt default/effettivi, segnaposto, stato chiave OpenAI, fusi orari |
| PUT | `/api/settings` | Aggiornamento parziale (incl. `openai_api_key`, salvata nei segreti) |
| POST | `/api/settings/ai/reset` | Ripristina i prompt di default (tutti o `fields` indicati) |
| GET | `/api/settings/ai/preview?date=` | Prompt esatti che verrebbero inviati per il giorno |
| GET | `/api/github/status` | Stato connessione GitHub + repository selezionati |
| POST | `/api/github/connect` | Avvia il Device Flow (restituisce codice e URL) |
| POST | `/api/github/token` | Collega con Personal Access Token (`token`) |
| POST | `/api/github/disconnect` | Scollega l'account GitHub |
| GET | `/api/github/repos?refresh=1` | Repository disponibili e selezionati |
| PUT | `/api/github/repos` | Salva i repository da monitorare (`repos`) |
| POST | `/api/github/import` | Importa commit / PR / issue del giorno |
| GET | `/api/microsoft/status` | Stato connessione Microsoft, provider attivo, disponibilita' Outlook COM |
| POST | `/api/microsoft/connect` | Avvia il device code flow Microsoft |
| POST | `/api/microsoft/disconnect` | Scollega l'account Microsoft |
| POST | `/api/microsoft/import` | Importa le riunioni del giorno |

## Struttura del progetto

```text
Note2TimeSheet/
|-- app.py                   # create_app() + avvio (banner, apertura browser)
|-- config.py                # percorsi, default da env, costanti, fuso orario
|-- practices.json           # pratiche di default (copiate in data/ al primo avvio)
|-- requirements.txt         # dipendenze runtime
|-- requirements-dev.txt     # pytest, coverage
|-- start_timesheet.bat      # launcher Windows (venv + dipendenze + avvio)
|-- start_timesheet_min.bat  # come sopra, finestra ridotta a icona
|-- Dockerfile / docker-compose.yml / .dockerignore
|-- .env.example
|-- docs/ARCHITECTURE.md     # contratti di moduli, JSON e API
|-- routes/                  # blueprint Flask: ui, entries, elaborate, practices, settings, github, microsoft
|-- services/                # settings, practices, state (storico), ai, github, graph, outlook
|-- static/css, static/js    # frontend ES modules senza build step
|-- templates/index.html
`-- tests/                   # pytest (HTTP esterno sempre mockato)
```

## Risoluzione problemi

| Sintomo | Causa / rimedio |
|---------|-----------------|
| `Address already in use` / la finestra si chiude subito | La porta 5600 e' occupata (spesso un'altra istanza dell'app). Chiudi l'altra istanza oppure cambia `APP_PORT` nel `.env`; con Docker cambia la porta host in `docker-compose.yml` (`"5601:5600"`). |
| Il codice dispositivo e' scaduto (`expired_token`) | I codici GitHub/Microsoft valgono pochi minuti: premi di nuovo **Connetti** e inserisci il nuovo codice. |
| GitHub: `incorrect_client_credentials` o `unsupported_grant_type` | Client ID errato oppure **Enable Device Flow** non spuntato nella OAuth App. |
| GitHub: "Limite API GitHub raggiunto" | Rate limit (5000 richieste/ora per token). Riduci i repository monitorati e riprova dopo qualche minuto. |
| GitHub: import vuoto | L'API eventi copre solo gli ultimi 90 giorni; controlla che i repository selezionati siano quelli giusti e che i commit siano a tuo nome. |
| Microsoft: "Need admin approval" (AADSTS65001 / AADSTS90094) | Il tenant richiede il consenso dell'amministratore: vedi guida (b), punto 5. |
| Microsoft: `AADSTS7000218` / "public client" | In Azure manca **Allow public client flows = Yes** (guida (b), punto 3). |
| Microsoft: le riunioni non compaiono | Verifica il permesso delegato `Calendars.Read`, il fuso orario in *Generale* e che la sorgente non sia forzata su *Outlook desktop* in Docker. |
| Outlook desktop non disponibile | Il fallback COM richiede Windows nativo con Outlook installato e `pywin32`; in Docker usa l'account Microsoft. |
| OpenAI: 401 chiave non valida / 429 quota | Controlla la chiave in *Generale* (se presente sovrascrive il `.env`) e il credito sull'account OpenAI. |
| L'AI restituisce un errore di formato | Se hai modificato il *Prompt AI*, assicurati che restino `{{practices}}`, `{{entries}}` e il vincolo di rispondere solo con JSON; in caso di dubbio **Ripristina default**. |
| `.venv` corrotto / dipendenze non installabili | Elimina la cartella `.venv` e rilancia `start_timesheet.bat`. |
| Docker: conflitto con la v1 | Sono container distinti (`timesheet-app` su 5599, `timesheet-app-v2` su 5600). Se vuoi solo la v2: `docker stop timesheet-app`. |

## Sviluppo e test

```bash
pip install -r requirements-dev.txt
pytest -q
pytest --cov=services --cov=routes --cov-report=term-missing
```

Convenzioni: codice, identificatori e commenti in inglese; stringhe rivolte all'utente in italiano; file piccoli e focalizzati; nessun HTTP reale nei test (`requests`, `openai`, `msal` sono sempre mockati). I contratti da rispettare sono in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Licenza

MIT
