# ADR 0018 — Canal multi-bot: un chat, un perfil; un token, un poller

- **Fecha**: 2026-11-24
- **Estado**: aceptado
- **Reemplaza**: —

## Contexto

El canal de Telegram se escribió para un solo bot: un token en `NEXO_TELEGRAM_TOKEN`, un poller, un
offset global. Alcanzaba para migrar `shopping` porque era el único perfil en nexo. Con el segundo
perfil (`personal-trainer`) aparecen dos necesidades que el diseño no cubría:

1. Un bot distinto por perfil, si se quiere identidad separada (nombre, avatar) y aislamiento.
2. Varios bots en el mismo proceso si se prefiere un solo servicio.

El sistema viejo resolvía esto con un gateway único multiplexando perfiles: **un bot, varios grupos**.
Eso sigue siendo válido y no requiere crear bots nuevos — el `chat_id` ya distingue el perfil.

## Decisión

1. **La identidad se resuelve por canal, no por proceso**: `channels.settings.token_env` nombra la
   variable de entorno del bot de ese canal. Si falta, se usa `NEXO_TELEGRAM_TOKEN` como compartido.
   Nada de tokens en la base: la base guarda *el nombre de la variable*.
2. **Un hilo por token distinto.** `TelegramChannel.run()` agrupa los canales por token y levanta un
   poller por cada uno; el offset vive en `kv` con clave `telegram.offset.<sha256(token)[:12]>`,
   porque `getUpdates` es por bot, no por chat.
3. **El worker resuelve el bot del canal del turno** antes de responder. Si no hay token, el turno
   falla con `NoToken` en vez de quedarse mudo: un turno sin destino es peor que un error visible.
4. **Un chat pertenece a un perfil** (`channels.kind + external_id` es único). Agregar un grupo nuevo
   es insertar una fila; no hay enrutamiento por nombre ni por heurística.

## Alternativas descartadas

- **Un proceso de canal por perfil**: aísla mejor, pero multiplica unidades de systemd y repite la
  lógica de comandos (`/stop`, `/sigue`) por perfil.
- **Guardar el token en la base**: cómodo, pero convierte un dump de Postgres en una filtración de
  credenciales. El nombre de la variable es un puntero, no un secreto.
- **Enrutar por nombre de grupo**: frágil (renombrar un grupo rompe el ruteo) y ambiguo con grupos
  de nombre parecido.

## Consecuencias

- Agregar un perfil son dos pasos: alta del perfil y alta del canal con su `chat_id` (y, si es un bot
  nuevo, la variable de entorno).
- Con un solo bot compartido, todos los perfiles comparten identidad visible en Telegram. Es lo que
  hacía el sistema viejo y es aceptable; separar identidad es cambiar una variable.
- El arranque del canal falla ruidosamente si no encuentra ningún token, en vez de quedarse
  escuchando para siempre sin poder responder.
- Un fallo de polling ahora imprime el traceback completo: el `type(exc).__name__` solapó un
  `TypeError` real durante veinte minutos y no valía la pena.
