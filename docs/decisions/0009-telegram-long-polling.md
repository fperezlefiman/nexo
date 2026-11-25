# 0009 — Telegram por long polling, sin endpoint público

**Estado:** aceptado · **Fecha:** 2026-11-24

## Contexto

El canal principal es Telegram, con un grupo por perfil. El host es un homelab Linux detrás de red
doméstica, sin IP pública ni dominio con certificado para el bot; ya existe acceso remoto por tailnet,
pero solo para el operador, no para los servidores de Telegram.

Telegram ofrece dos formas de recibir updates: `getUpdates` (long polling) y `setWebhook` (el
servidor de Telegram hace POST a una URL HTTPS pública).

Además, el canal tiene restricciones propias: mensajes de 4096 caracteres, límite de edición por
segundo, y `update_id` que puede repetirse en reentregas.

## Decisión

**Long polling** como mecanismo de entrada en el v0, con `offset = last_update_id + 1` persistido en
la base (no en memoria), y salida por `sendMessage`/`editMessageText` con agrupación de deltas cada
~800 ms o por párrafo.

- **Idempotencia** en dos niveles: `update_id` descartado si ya se procesó, y
  `idempotency_key = sha256(channel_id, external_message_id)` único en `turns`.
- **Allowlist** de `chat_id`: cualquier otro remitente se descarta antes del dispatch.
- **Split** de mensajes largos por párrafo, nunca a mitad de un bloque de código.

## Consecuencias

- **Se gana:** cero infraestructura nueva (sin dominio, sin certificado, sin puerto abierto); el mismo
  proceso funciona en cualquier red doméstica; el `offset` persistido evita perder updates en un
  reinicio.
- **Cuesta:** un proceso mantiene una conexión abierta (latencia de entrega de hasta ~1 s más el
  timeout del polling); un solo consumidor por bot, así que no hay escalado horizontal del canal; y el
  polling consume ancho de banda constante (irrelevante a este volumen).
- **Se vuelve difícil:** recibir eventos de Telegram en tiempo real si algún día se necesita (se
  resuelve migrando a webhook, que el diseño deja posible porque el canal está detrás del port
  `Channel`).
- **Verificación:** test con el mismo `update_id` dos veces → un solo turno; reinicio del proceso con
  updates pendientes → ninguno perdido (comparar `offset` con lo recibido); p95 de entrega medido.

## Alternativas rechazadas

- **Webhook con URL pública** — exige dominio, certificado y exponer un endpoint; resuelve un problema
  que no tengo (volumen bajo) a cambio de la superficie de ataque más cara de mantener.
- **Webhook por túnel (Cloudflare Tunnel/ngrok)** — agrega una dependencia externa y un punto de falla
  para ganar ~1 s de latencia.
- **Otra plataforma (WhatsApp/Discord)** — Telegram es donde ya vivo; cambiar de canal no aporta nada
  al problema que motiva el proyecto.

## Lo que habilita en una entrevista

"Sin IP pública, webhook no es opción: long polling con el offset persistido, idempotencia por
`update_id` y por clave única en el turno, y allowlist de chat. La latencia extra es ~1 s, que para
recordatorios y chat no importa. Si algún día hace falta webhook, el canal está detrás de un port y se
cambia sin tocar el dominio."
