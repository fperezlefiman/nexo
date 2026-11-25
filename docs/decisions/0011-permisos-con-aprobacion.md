# 0011 — Permisos de tools con aprobación humana por chat

**Estado:** aceptado · **Fecha:** 2026-11-24

## Contexto

El motor ejecuta tools sobre la máquina real: terminal, escritura de archivos, red. Corre en el mismo
homelab donde viven los datos personales, los repositorios y los contenedores. Un turno mal dirigido
—por un prompt mal escrito, por contenido malicioso leído de una página web, o por una alucinación—
puede borrar un directorio o cambiar configuración.

El motor expone un endpoint de permisos:
`POST /api/session/{id}/permission/{requestID}/reply`. Es decir: **el pedido de permiso existe del lado
del motor**; lo que falta es la decisión humana, que hoy no está conectada a nada.

## Decisión

Toda request de permiso del motor se convierte en un mensaje de Telegram con botones
(**Permitir una vez · Permitir siempre en este turno · Denegar**), y el turno pasa a
`awaiting_permission`:

- Sin respuesta en **120 s** → **deny** (falla cerrada, no abierta).
- La decisión se persiste en `permission_requests` con `decided_at` y `decided_by`.
- Defensa en profundidad, independiente del diálogo: el motor corre bajo un **usuario dedicado** con
  acceso solo al workspace, y el `chat_id` está en allowlist.
- "Permitir siempre" tiene alcance de **turno**, nunca global: no existe un `always allow` persistente
  en el v0. Un permiso permanente se otorga editando configuración, a mano, fuera del chat.

## Consecuencias

- **Se gana:** el agente puede trabajar con terminal real sin que un error silencioso sea
  irrecuperable; queda un registro de qué se autorizó y cuándo; el costo es una interacción ocasional.
- **Cuesta:** fricción (un turno puede quedar esperando); si el operador no está mirando el chat, la
  tarea se cae a los 120 s; hay que diseñar la UI de botones (y su edición al decidir) en el canal.
- **Se vuelve difícil:** operar el sistema desatendido con tools peligrosas; los jobs proactivos deben
  declarar un conjunto de tools acotado en vez de confiar en aprobaciones que nadie va a dar.
- **Verificación:** test que simula una request de permiso sin respuesta → deny a los 120 s con fila en
  `permission_requests`; test que aprueba → el turno continúa; audit de que no existe ninguna ruta de
  código que responda "allow" por defecto.

## Alternativas rechazadas

- **Allow automático** — un LLM con terminal y sin freno; el modo de falla es catastrófico e
  irreversible.
- **Deny total a tools peligrosas** — deja al agente sin capacidad de hacer trabajo real; el proyecto
  pierde su razón de ser.
- **Aprobación por lista blanca de comandos** — se rompe con la primera tarea legítima que no está en
  la lista, y una lista de comandos permitidos es difícil de mantener correcta (peor: da falsa
  seguridad).

## Lo que habilita en una entrevista

"El agente tiene terminal sobre mi máquina, así que la decisión de permiso es del humano y falla
cerrada: 120 segundos sin respuesta es un deny, el permiso 'siempre' dura un turno, y hay registro de
cada autorización. Además el proceso corre con un usuario dedicado, así que la aprobación no es la
única defensa."
