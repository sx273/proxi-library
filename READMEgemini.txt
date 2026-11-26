PROXI LIBRARY - PASSAGGIO DI PROGETTO
Aggiornato: 2026-11-26

SCOPO
Proxi Library e una dashboard web Python/FastAPI per cercare musica tramite lucidadl, controllare il risultato prima del download, selezionare le tracce di un album e salvare i file in una libreria organizzata per artista/album. L'interfaccia e utilizzabile da desktop e smartphone. Non usa Gemini CLI e non include login Google.

STATO ATTUALE
- Applicazione sviluppata nel workspace e preview locale avviata su http://127.0.0.1:6067.
- Nome visibile: Proxi Library.
- Porta interna e host predefinita: 6067.
- Sorgente predefinita: Amazon Music; lucidadl include anche Qobuz e GrilledCheese.
- La dashboard usa lucidadl 1.4.3. Le funzioni di integrazione sono API interne di lucidadl, non API pubbliche stabili.
- L'accesso a lucida.to richiede una sessione Chromium/Cloudflare persistente, distinta dal login Google. Il container usa Chromium e Xvfb; se Cloudflare richiede un CAPTCHA o intervento umano, il browser nascosto potrebbe non poterlo completare.
- L'utente ha riferito di avere autorizzazione per i download e gestisce separatamente il proprio login.

FUNZIONALITA IMPLEMENTATE
- Ricerca di album e brani; pagina di anteprima prima del download.
- In un album si possono selezionare tutte le tracce o solo alcune. Le tracce non disponibili sono disabilitate; il backend accetta solo numeri presenti e disponibili.
- Se vengono scelte tutte le tracce, il backend usa la URL dell'album di lucidadl. Per una selezione parziale usa le URL delle singole tracce.
- Download a qualita originale con organizzazione e deduplicazione di lucidadl.
- Stato di download visibile, barra viola e percentuale; se non ci sono lavori attivi compare "Tutto vuoto qui!".
- Copertine per risultati, anteprima album, conferma e download recenti. Le fonti sono Apple iTunes Search e Deezer; titolo e artista vengono confrontati in modo restrittivo per evitare copertine errate. Se non c'e un match affidabile viene usato un segnaposto.
- Le immagini sono servite tramite /api/artwork, un proxy same-origin con allowlist HTTPS limitata a host statici Apple e Deezer.
- Le copertine mancanti nei download recenti esistenti vengono cercate e salvate nel database.
- Dashboard smartphone: il vinile e sotto il testo stretto, cosi non si sovrappone a titolo/descrizione; CSS versionato in app/static/index.html per evitare cache obsolete.
- Il vinile ruota in 12 secondi per giro; la rotazione e disattivata quando il browser indica prefers-reduced-motion.

FILE IMPORTANTI
- app/main.py: backend FastAPI, API lucidadl, ricerca artwork Apple/Deezer, selezione tracce, download, stato e cronologia.
- app/static/index.html, app/static/app.js, app/static/styles.css: interfaccia e comportamento browser.
- requirements.txt: dipendenze Python incluse nell'immagine Docker.
- Dockerfile: installa Python, pacchetti requirements, Chromium Playwright e Xvfb; avvia Uvicorn su 6067.
- compose.yaml: Compose per sviluppo locale, costruisce l'immagine da Dockerfile.
- compose.zimaos.yaml: template per installazione personalizzata ZimaOS tramite immagine GHCR; sostituire INSERISCI-UTENTE e i due percorsi host.
- .github/workflows/publish-image.yml: build multi-arch amd64/arm64 e pubblicazione su GitHub Container Registry (GHCR) a ogni push su main, tag v* o avvio manuale.
- .env.example: esempio variabili locali.
- .gitignore e .dockerignore: escludono dati locali, .env, musica e file generati.

PERSISTENZA E INSTALLAZIONE ZIMAOS
compose.zimaos.yaml mappa:
- cartella host della musica -> /music nel container;
- cartella persistente app -> /data nel container, dove sono salvati settings, database, cookie e profilo Chromium.
La dashboard e pubblicata su porta 6067.

Per installazione personalizzata da Docker Compose, l'immagine deve essere prima pubblicata: il Compose ZimaOS usa `image: ghcr.io/INSERISCI-UTENTE/proxi-library:latest` e non `build: .`, quindi non richiede il Dockerfile sul NAS. La procedura prevista e:
1. Creare un repository GitHub (esempio proxi-library) e caricare il progetto senza .env, music o app-data.
2. Attendere GitHub Actions -> Publish Proxi Library image.
3. Rendere pubblico il package GHCR per permettere a ZimaOS di scaricare l'immagine senza credenziali.
4. Modificare compose.zimaos.yaml: username GitHub minuscolo e percorsi reali sul server.
5. In ZimaOS aprire Installazione personalizzata, incollare il Compose e installare; aprire http://IP_DEL_SERVER:6067.
Per avere una scheda nell'App Store ZimaOS anziche installazione personalizzata servono anche metadati x-casaos e un catalogo/app store, non sono inclusi nel Compose personalizzato.

PROBLEMA BUILD GITHUB ATTUALE
L'utente ha inviato uno screenshot del run GitHub Actions:
- repository: sx273/proxi-library
- run: https://github.com/sx273/proxi-library/actions/runs/45724702899
- job: publish
- il job fallisce nel layer Dockerfile che esegue:
  pip install --no-cache-dir -r requirements.txt && python -m playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*
- la sintesi mostra exit code 127, ma non il comando interno mancante.
- compare anche un warning che alcune GitHub Actions sono forzate a Node.js 24 per deprecazione di Node.js 20; e un warning separato, non l'errore fatale mostrato.

E' stato controllato il codice di Playwright 1.65.2. Con UID root, install --with-deps esegue apt-get direttamente; non-root cerca sudo e poi su. Il Dockerfile python:3.12-slim gira come root per impostazione predefinita, quindi non e corretto concludere senza log che manchi sudo. GitHub non ha esposto i log completi anonimamente (API 403/login richiesto). La causa specifica resta da confermare.

PROSSIMO PASSO PER RISOLVERE IL BUILD
Aprire il run GitHub Actions, espandere il job publish e lo step Build and publish image, poi leggere/copiare le righe immediatamente precedenti a exit code 127. In alternativa separare il RUN del Dockerfile in step distinti per isolare pip install, download Chromium e installazione librerie OS. Non rimuovere alla cieca --with-deps: Chromium necessita delle librerie di sistema. Dopo la correzione, fare push su main e verificare che Actions pubblichi GHCR.

VERIFICHE GIA ESEGUITE
- Python: compileall e import con venv temporaneo.
- FastAPI: smoke test su impostazioni, access gate, selezione album, rifiuto tracce non valide e cronologia.
- Artwork: test live iTunes e Deezer, immagine servita via proxy, corrispondenza corretta titolo/artista, rifiuto di artista errato e backfill download recenti.
- UI: browser test per selezione singole tracce, immagine 400px, barra progresso viola, disco animato e layout smartphone.
- Vinile verificato a 12s; reduced motion lo disattiva.
- Smartphone: test a viewport CSS effettivi 256/288/312/344px (emulazione browser di richieste 320/360/390/430px): nessuna sovrapposizione titolo/disco, gap 20-21px, nessun overflow orizzontale.
- Compose e workflow GHCR: parsing YAML e asserzioni di base riusciti.
- Non e stata possibile una build Docker locale: Docker non e installato nell'ambiente di sviluppo. Non e stata ancora completata l'installazione su ZimaOS.
