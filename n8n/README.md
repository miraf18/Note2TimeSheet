# Riunioni via n8n

Soluzione alternativa per importare le riunioni: invece di far autenticare l'app
a Microsoft (COM o Graph), un flusso **n8n** — che ha già le credenziali Microsoft
configurate — legge il calendario e risponde all'app con le riunioni.

Vantaggi: funziona ovunque (anche in Docker), nessun setup Azure lato app, l'auth
resta centralizzata in n8n. Quando `N8N_MEETINGS_URL` è impostato, l'app usa
**questo** provider con priorità su COM e Graph.

## Come funziona

```
App  ──POST {date, start, end}──▶  Webhook n8n
                                     │
                                     ▼
                          Microsoft Graph /me/calendarView
                                     │
                                     ▼
                              Formatta riunioni (Code)
                                     │
App  ◀──{ "meetings": [...] }──  Rispondi all'app
```

L'app invia `start`/`end` già calcolati in UTC per il giorno selezionato, quindi
n8n non deve gestire fusi orari.

## Setup (una tantum)

1. In n8n: **Workflows → Import from File** → scegli
   [`calendar-to-timesheet.json`](./calendar-to-timesheet.json).
2. Apri il nodo **"Microsoft Graph — calendarView"** e seleziona la tua
   credenziale Microsoft (tipo *Microsoft Outlook OAuth2 API*). La credenziale
   deve avere lo scope **`Calendars.Read`**.
3. **Attiva** il workflow (toggle in alto a destra).
4. Sul nodo **Webhook** copia la *Production URL*
   (es. `https://tuo-n8n/webhook/timesheet-meetings`).
5. Nel `.env` dell'app:
   ```
   N8N_MEETINGS_URL=https://tuo-n8n/webhook/timesheet-meetings
   ```
6. Riavvia l'app. Il bottone riunioni ora passa da n8n (lo vedi anche nel log
   d'avvio: `Riunioni: n8n (webhook Microsoft)`).

## Sicurezza (opzionale ma consigliato)

Il webhook è pubblico per default. Per proteggerlo:

- Nel `.env` imposta `N8N_MEETINGS_TOKEN=<un-segreto>`: l'app lo invia come header
  `X-Timesheet-Token`.
- In n8n aggiungi un nodo **IF** subito dopo il Webhook che confronta
  `{{ $json.headers["x-timesheet-token"] }}` col segreto, e fai rispondere
  errore se non combacia. (Oppure abilita *Header Auth* sul nodo Webhook.)

## Formato della risposta atteso dall'app

L'app accetta sia la forma "pronta" sia eventi Graph grezzi. Forma pronta:

```json
{
  "meetings": [
    {
      "subject": "Standup",
      "duration": 30,
      "duration_label": "30",
      "is_allday": false,
      "meeting_id": "AAMk..."
    }
  ]
}
```

Lato app, le riunioni con durata < 5 min (non all-day) e quelle annullate vengono
scartate, con deduplica per `meeting_id`.
