# Architecture

```
 Talk button (GPIO 26) ─► deck-buttons ─► hooks/talk-start|stop ─► exo_deck.voice
     arecord ─► wav ─► stt (Deepgram → Gemini) ─► router
         talk     ─► exo-bridge POST /talk  ─► api_server 127.0.0.1:8642 ─► Hermes (default profile)
         delegate ─► exo-bridge POST /tasks ─► hermes kanban --board exo create … ─► dispatcher ─► profile
     reply ─► tts (Deepgram Aura → aplay | espeak-ng) ─► earbuds
 Kiosk dashboard ─► /api/agents ─► exo-bridge GET /board
 exo-bridge: droplet tailnet IP :8765, bearer token, 4 endpoints
```
