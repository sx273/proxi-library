# 📚 Code & Function Reference Guide

Questo documento spiega nel dettaglio la struttura del progetto **Proxi Library** e il funzionamento di ogni singola funzione e classe, sia nel backend Python FastAPI ([app/main.py](../app/main.py)) sia nel frontend JavaScript ([app/static/app.js](../app/static/app.js)).

---

## Indice

1. [Panoramica dell'Architettura](#panoramica-dellarchitettura)
2. [Backend: FastAPI (`app/main.py`)](#backend-fastapi-appmainpy)
   - [Modelli Pydantic](#modelli-pydantic)
   - [Gestione Dati, Cartelle e Storage](#gestione-dati-cartelle-e-storage)
   - [Integrazione con `lucidadl`](#integrazione-con-lucidadl)
   - [Ricerca e Match Copertine (Artwork)](#ricerca-e-match-copertine-artwork)
   - [Gestione Download e Reporter](#gestione-download-e-reporter)
   - [Endpoint API REST](#endpoint-api-rest)
3. [Frontend: JavaScript Vanilla (`app/static/app.js`)](#frontend-javascript-vanilla-appstaticappjs)
   - [Comunicazione e Rendering UI](#comunicazione-e-rendering-ui)
   - [Flusso di Ricerca e Anteprima](#flusso-di-ricerca-e-anteprima)
   - [Flusso di Conferma e Download](#flusso-di-conferma-e-download)
   - [Inizializzazione ed Event Listener](#inizializzazione-ed-event-listener)

---

## Panoramica dell'Architettura

```text
[ Browser Utente (Desktop / Mobile) ]
             ▲
             │ HTTP / JSON
             ▼
[ FastAPI Backend (app/main.py) ]
  ├── Proxy Artwork (Apple iTunes / Deezer)
  ├── SQLite Database (/data/dashboard/dashboard.sqlite3)
  └── lucidadl Engine
        ├── Headed Chromium (Xvfb virtual display) -> Cloudflare clearance
        └── LucidaClient (httpx HTTP API) -> lucida.to -> File audio
                                                  │
                                                  ▼
                                       [/music/Artists/<Artist>/<Album>/]
```

---

## Backend: FastAPI (`app/main.py`)

### Modelli Pydantic

Questi modelli definiscono e convalidano la struttura dei dati inviati dal client nelle richieste HTTP:

1. **`SearchRequest(BaseModel)`**
   - **Cosa fa:** Valida il corpo della richiesta `POST /api/search`.
   - **Campi:** `query: str` (lunghezza compresa tra 2 e 200 caratteri).

2. **`SettingsRequest(BaseModel)`**
   - **Cosa fa:** Valida il corpo della richiesta `PUT /api/settings`.
   - **Campi:** `service: str` (identificativo del servizio, es. `amazon`) e `music_folder: str` (sottocartella desiderata).

3. **`DownloadRequest(BaseModel)`**
   - **Cosa fa:** Valida il corpo della richiesta `POST /api/candidates/{candidate_id}/download`.
   - **Campi:** `track_numbers: list[int] | None` (lista opzionale dei numeri delle tracce selezionate dall'utente).

---

### Gestione Dati, Cartelle e Storage

1. **`_ensure_storage() -> None`**
   - **Cosa fa:** Crea le directory persistenti `DATA_DIR` e `MUSIC_ROOT` se non esistono. Inizializza il database SQLite `dashboard.sqlite3` con la tabella `downloads` (se non ancora presente) ed esegue una migrazione trasparente per aggiungere la colonna `artwork_url` se assente.

2. **`_read_settings() -> dict[str, str]`**
   - **Cosa fa:** Legge il file `settings.json`. Se il file è mancante o corrotto, carica i valori di default: servizio `amazon` e cartella libreria vuota (radice di `/music`).

3. **`_music_directory(music_folder: str) -> Path`**
   - **Cosa fa:** Risolve il percorso specificato dall'utente e verifica tramite `is_relative_to(MUSIC_ROOT)` che la destinazione rimanga rigorosamente confinata all'interno del volume montato `/music`, prevenendo tentativi di directory traversal.

4. **`_write_settings(settings: dict[str, str]) -> None`**
   - **Cosa fa:** Salva atomicamente le impostazioni scrivendo prima su un file temporaneo `.tmp` e poi rimpiazzando `settings.json`, evitando corruzioni in caso di riavvio improvviso.

5. **`_prune_candidates() -> None`**
   - **Cosa fa:** Scorre la memoria volatile del server (`_candidates`) ed elimina le ricerche/anteprime create da più di 15 minuti (`CANDIDATE_TTL_SECONDS`), liberando memoria.

6. **`_save_download(title, artist, album, kind, service, files, artwork_url) -> None`**
   - **Cosa fa:** Inserisce nel database SQLite (`downloads`) una riga che traccia il completamento del download con data/ora (timestamp), lista dei file prodotti su disco e URL della copertina.

---

### Integrazione con `lucidadl`

1. **`async _open_client(service: str) -> tuple[LucidaClient, str]`**
   - **Cosa fa:** Carica i cookie `cf_clearance` e lo `user-agent` salvati in precedenza da `lucidadl`. Se presenti, istanzia e avvia il client HTTP asincrono `LucidaClient` configurato per scaricare in qualità originale con metadati completi.

2. **`async _fetch_item_page(client: LucidaClient, item_url: str, country: str) -> tuple[dict, str]`**
   - **Cosa fa:** Scarica i metadati di una traccia o di un album da `lucida.to`. Se l'URL appartiene ad Amazon Music, implementa un meccanismo di fallback automatico tra diversi marketplace regionali (`""`, `"US"`, `"GB"`, `"JP"`) per trovare il primo che restituisce tracce con token validi.

3. **`_artists(value: Any) -> str`**
   - **Cosa fa:** Normalizza la struttura degli artisti restituita da `lucidadl` (che può essere una stringa o una lista di dizionari) unendoli in una stringa leggibile separata da virgole.

---

### Ricerca e Match Copertine (Artwork)

1. **`_normalized_text(value: str) -> str`**
   - **Cosa fa:** Converte una stringa in minuscolo, rimuove caratteri speciali e punteggiatura mantenendo solo caratteri alfanumerici e spazi. Serve a confrontare titoli e artisti in modo uniforme.

2. **`_match_artwork(candidate: dict, catalog: list[dict], kind: str) -> str`**
   - **Cosa fa:** Esegue una corrispondenza "fuzzy" ponderata (utilizzando `difflib.SequenceMatcher`) tra i metadati del brano e i risultati del catalogo copertine. Applica soglie minime di similarità per evitare copertine errate (es. omonimie) e sostituisce la risoluzione delle immagini Apple per ottenerle a `400x400`.

3. **`async _fetch_artwork_catalog(query: str, kind: str) -> list[dict]`**
   - **Cosa fa:** Interroga l'API pubblica di ricerca Apple iTunes per trovare copertine di album o singoli brani. I risultati vengono memorizzati in una cache LRU in memoria (`_artwork_cache`).

4. **`async _fetch_deezer_artwork_catalog(query: str, kind: str) -> list[dict]`**
   - **Cosa fa:** Interroga l'API pubblica di Deezer come sorgente alternativa quando Apple non fornisce risultati adatti, recuperando immagini di grandi dimensioni (`cover_xl` o `cover_big`).

5. **`async _candidate_artwork(item, kind, query, albums, tracks, fallback_limit) -> str`**
   - **Cosa fa:** Coordina l'intero processo di ricerca della copertina: prova prima il match locale con i risultati già in cache, poi ricerche mirate su iTunes per album/traccia, e infine fallback su Deezer sotto il controllo di un semaforo di concorrenza (`asyncio.Semaphore`).

6. **`_is_artwork_url(url: str) -> bool`**
   - **Cosa fa:** Controlla che una URL provenga esclusivamente via HTTPS da CDN ufficiali di Apple (`mzstatic.com`) o Deezer (`cdn-images.dzcdn.net`), bloccando qualsiasi richiesta non autorizzata.

---

### Gestione Download e Reporter

1. **Classe `JobReporter`**
   - Implementa l'interfaccia reporter richiesta da `lucidadl` per catturare eventi e progresso:
     - **`__init__(job)`**: Associa il reporter a un dizionario di lavoro in memoria (`_jobs`).
     - **`log(message)`**: Registra un messaggio testuale nel log del download.
     - **`start(key, label)`**: Inizializza lo stato di una traccia (in coda/preparazione).
     - **`status(key, message)`**: Aggiorna lo stato attuale restituito dal provider per una singola traccia.
     - **`progress(key, downloaded, total)`**: Calcola la percentuale di avanzamento in base ai byte scaricati.
     - **`finish(key, ok, line)`**: Imposta la traccia come completata (100%) o in errore.
     - **`close()`**: Soddisfa il contratto dell'interfaccia senza bloccare risorse.

2. **`async _run_download(job_id: str, candidate_id: str) -> None`**
   - **Cosa fa:** Wrapper asincrono che acquisisce il lock globale `_download_lock` per garantire che i download vengano eseguiti in serie, evitando conflitti di scrittura.

3. **`async _run_download_serial(job_id: str, candidate_id: str) -> None`**
   - **Cosa fa:** Cuore dell'esecuzione del download. Ricava i dati del candidato, determina se scaricare l'intero album o singole tracce, invoca `downloader.run_batch` di `lucidadl`, organizza i file in cartelle per artista/album e salva la voce nella tabella `downloads`.

---

### Endpoint API REST

1. **`GET /`** (`dashboard`)
   - Restituisce la pagina web principale `index.html`.

2. **`GET /api/settings`** (`get_settings`)
   - Restituisce la sorgente corrente (es. `amazon`) e i percorsi assoluti e relativi della libreria.

3. **`PUT /api/settings`** (`update_settings`)
   - Valida la nuova sorgente e la cartella, verifica tramite scrittura di un file di test temporaneo che la directory su disco sia realmente scrivibile, e salva le nuove impostazioni.

4. **`GET /api/access`** (`access_status`)
   - Controlla se sul server esiste già un cookie/sessione `cf_clearance` valido per `lucida.to`.

5. **`GET /api/artwork`** (`artwork_image`)
   - Endpoint proxy: scarica e serve le copertine approvate al browser del client, evitando problemi di Cross-Origin (CORS).

6. **`POST /api/access/prepare`** (`prepare_access`)
   - Avvia Chromium sotto Xvfb ed esegue il flusso di autorizzazione Cloudflare per `lucida.to`, salvando la sessione nel volume persistente.

7. **`POST /api/search`** (`search`)
   - Esegue la ricerca tramite `lucidadl`, arricchisce i risultati con le copertine Apple/Deezer e memorizza i candidati per l'anteprima.

8. **`POST /api/candidates/{candidate_id}/preview`** (`preview_candidate`)
   - Risolve l'URL del candidato selezionato e restituisce la lista di tutte le tracce con indicazione della disponibilità.

9. **`POST /api/candidates/{candidate_id}/download`** (`confirm_download`)
   - Riceve la conferma dell'utente (e gli eventuali numeri di traccia selezionati), crea un `job` in coda e avvia il task di download in background.

10. **`GET /api/jobs/{job_id}`** (`get_job`)
    - Endpoint di polling interrogato dal frontend per conoscere stato, percentuali e log del download in corso.

11. **`GET /api/recent`** (`recent_downloads`)
    - Restituisce le ultime 12 tracce/album scaricati salvati nel database, recuperando le copertine se mancanti.

---

## Frontend: JavaScript Vanilla (`app/static/app.js`)

### Comunicazione e Rendering UI

1. **`request(path, options)`**
   - Funzione wrapper per `fetch`: invia richieste HTTP JSON, gestisce le risposte e solleva errori leggibili in caso di codici HTTP non-200.

2. **`showToast(message)`**
   - Mostra un banner di notifica temporaneo in basso a destra per conferme o errori, nascondendolo automaticamente dopo 3.6 secondi.

3. **`escapeHtml(value)`**
   - Esegue l'escape di caratteri speciali HTML (`&`, `<`, `>`, `"`, `'`) per proteggere l'interfaccia da XSS.

4. **`artworkMarkup(url, alt)`**
   - Genera il codice HTML del riquadro copertina utilizzando l'endpoint proxy `/api/artwork`. Se la copertina è assente, mostra il segnaposto con la nota musicale `♫`.

5. **`handleArtworkFailure(event)`**
   - Intercetta gli errori di caricamento delle immagini (`onerror`) e sostituisce l'immagine fallita con il fallback `♫`.

6. **`serviceName(service)`**
   - Converte gli ID tecnici delle sorgenti nei nomi visibili per l'utente (`amazon` -> `Amazon Music`, `qobuz` -> `Qobuz`, `grilledcheese` -> `GrilledCheese`).

---

### Flusso di Ricerca e Anteprima

1. **`searchMusic(query)`**
   - Invia la stringa cercata a `POST /api/search`, gestisce lo stato di caricamento del pulsante e renderizza l'elenco dei risultati trovati.

2. **`resultMarkup(result, index)`**
   - Genera l'HTML di una singola riga di risultato della ricerca con artwork, badge `Album` o `Brano` e pulsante `Apri`.

3. **`previewCandidate(row)`**
   - Chiamata quando l'utente clicca `Apri`: interroga `POST /api/candidates/{id}/preview`, espande il pannello delle tracce, inserisce i checkbox di selezione e il pulsante per avviare la conferma.

4. **`trackMarkup(track, selectable)`** e **`trackListMarkup(tracks, selectable)`**
   - Costruiscono l'elenco ordinato delle tracce con numero, titolo, artista ed eventuale etichetta "non disponibile".

5. **`updateTrackSelection(row)`**
   - Mantiene sincronizzati il checkbox "Tutte", il contatore di tracce selezionate e il testo del pulsante di download ("Scarica tutte" o "Scarica selezionate (N)").

6. **`collectionArtworkMarkup(preview, compact)`**
   - Mostra la copertina ingrandita e le informazioni principali dell'album sopra la lista delle tracce.

---

### Flusso di Conferma e Download

1. **`openConfirmation(row)`**
   - Legge le tracce selezionate nel pannello di anteprima e apre il dialogo modale nativo `<dialog>` (`#confirmDialog`), chiedendo esplicita conferma all'utente prima di scaricare.

2. **`startDownload()`**
   - Invia l'ID del candidato e l'elenco delle tracce a `POST /api/candidates/{id}/download`, chiude il modale e attiva il polling sul nuovo lavoro.

3. **`pollJob(jobId)`**
   - Interroga periodicamente `GET /api/jobs/{id}` ogni 1.2 secondi finché il lavoro non risulta completato o fallito, aggiornando la UI con i progressi.

4. **`renderJob(job)`**
   - Disegna la card del download in corso con titolo, barra di progressione percentuale dinamica per ogni traccia e gli ultimi log ricevuti da `lucidadl`.

5. **`removeFinishedJob(jobId)`**
   - Rimuove la card di un download terminato e, se la coda è vuota, ripristina la dicitura "Tutto vuoto qui!".

6. **`loadRecent()`**
   - Richiede a `GET /api/recent` le ultime 12 uscite scaricate e le visualizza nella sezione "Scaricate di recente".

---

### Inizializzazione ed Event Listener

1. **`loadSettings()`**
   - Carica le preferenze correnti dal server e aggiorna i campi del form impostazioni e i percorsi mostrati a fondo pagina.

2. **`loadAccessStatus()`**
   - Verifica se la sessione browser per `lucida.to` è pronta o da preparare, aggiornando il pallino di stato in testata (verde/rosso).

3. **`openSettings()`** e **`saveSettings(event)`**
   - Gestiscono l'apertura del dialogo impostazioni e l'invio delle modifiche al backend (`PUT /api/settings`).

4. **`prepareAccess()`**
   - Innesca la chiamata `POST /api/access/prepare` per rinnovare la sessione Cloudflare e aggiorna l'indicatore visivo al termine.

5. **`bindEvents()`**
   - Associa tutti i gestori di eventi DOM (submit del form di ricerca, click sui pulsanti di anteprima, cambio selezioni tracce, dialoghi modali).

6. **`initialize()`**
   - Funzione di bootstrap invocata al caricamento della pagina: registra gli eventi e carica parallelamente impostazioni, download recenti e stato della sessione.
