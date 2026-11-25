# Proxi Library

Dashboard Dockerizzata in Python per cercare brani e album con lucidadl, controllare i risultati prima del download e organizzare i file in cartelle per artista e album.

## Avvio su ZimaOS

1. Copia `.env.example` in `.env` e imposta `MUSIC_LIBRARY_PATH` sulla cartella della libreria musicale del server. `APP_DATA_PATH` deve puntare a una cartella persistente e scrivibile.
2. Avvia il progetto con `docker compose up -d --build` dalla cartella che contiene `compose.yaml`.
3. Apri `http://IP_DEL_SERVER:6067` e vai nelle impostazioni per controllare sorgente e cartella.
4. Prepara la sessione lucida.to dal pannello impostazioni. Questo passaggio usa Chromium e può richiedere un controllo Cloudflare; non è il login Google gestito dalla tua applicazione esterna.

## Pubblicazione per installazione personalizzata

Il workflow `.github/workflows/publish-image.yml` costruisce l'immagine Docker per `amd64` e `arm64` e la pubblica su GitHub Container Registry (GHCR) quando aggiorni il branch `main` o pubblichi un tag `v*`.

1. Crea un repository GitHub vuoto, ad esempio `proxi-library`, e carica il contenuto di questo progetto senza `.env`, `music/` o `app-data/` (sono esclusi da `.gitignore`). Dalla cartella del progetto puoi usare:

	```bash
	git init -b main
	git add .
	git commit -m "Initial Proxi Library release"
	git remote add origin https://github.com/TUO-UTENTE/proxi-library.git
	git push -u origin main
	```

2. In GitHub apri `Actions` e attendi il workflow **Publish Proxi Library image**. Al termine l'immagine `ghcr.io/TUO-UTENTE/proxi-library:latest` sarà pubblicata.
3. Apri il package `proxi-library` nelle impostazioni del profilo GitHub e imposta la visibilità su `Public`, così ZimaOS potrà scaricarlo senza credenziali.
4. Copia `compose.zimaos.yaml`, sostituisci `INSERISCI-UTENTE` con il nome GitHub in minuscolo e scegli i due percorsi host: la cartella musicale e una cartella persistente per i dati dell'app.
5. In ZimaOS apri l'installazione personalizzata, incolla il Compose aggiornato e avvia l'installazione. La dashboard sarà disponibile sulla porta `6067`.

Ogni push successivo su `main` aggiorna il tag `latest`; per applicare la nuova versione a ZimaOS, aggiorna o ricrea l'app in modo che scarichi l'immagine più recente. Questo flusso crea un'immagine installabile con Compose; per aggiungerla come scheda all'App Store serve anche il metadata `x-casaos` e un catalogo compatibile.

La musica dell'host viene montata nel container come `/music`. Il campo cartella nelle impostazioni è relativo a quel mount; lasciandolo vuoto, lucidadl salva direttamente nella directory host indicata da `MUSIC_LIBRARY_PATH`. Configurazione, database, cookie e profilo Chromium sono persistiti sotto `APP_DATA_PATH`.

## Flusso

- La ricerca usa la sorgente selezionata nelle impostazioni: Amazon Music è il valore iniziale; lucidadl dichiara inoltre `qobuz` e `grilledcheese`.
- `Apri` carica i dettagli del risultato e, per un album, mostra le tracce prima di avviare qualsiasi download.
- Le copertine vengono cercate nei cataloghi pubblici Apple iTunes e Deezer con corrispondenza titolo/artista; il fallback usa Deezer quando Apple non ha un risultato affidabile. Se nessuno dei due cataloghi trova una corrispondenza sicura, la dashboard mostra un segnaposto. Non servono chiavi API.
- `Conferma download` invia il risultato scelto a lucidadl. Il downloader mantiene la sua gestione dei duplicati e l'organizzazione per tag/metadati.
- La dashboard non usa Gemini CLI e non contiene un login Google. La sessione Cloudflare di lucidadl viene mantenuta nel volume dati.

## Dipendenze e sicurezza

Le dipendenze Python sono elencate in `requirements.txt`; `httpx` è usato per le ricerche delle copertine, mentre il Dockerfile installa anche Chromium e Xvfb necessari alla sessione browser di lucidadl.

La dashboard non implementa autenticazione propria perché l'accesso è gestito dall'applicazione esterna. Non esporre la porta direttamente a Internet: pubblicala solo su una rete fidata o dietro il tuo reverse proxy/autenticazione esistente. Usa lucidadl solo per contenuti che sei autorizzato a scaricare e nel rispetto delle condizioni dei servizi coinvolti.

Il container richiede accesso a Internet per installazione, ricerca e download. Le funzioni interne di lucidadl non sono una API pubblica stabile: aggiornamenti upstream potrebbero richiedere un adeguamento dell'integrazione.