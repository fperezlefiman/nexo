# 0012 — Coexistencia: bots separados y un solo dueño por job

**Estado:** aceptado · **Fecha:** 2026-11-24

## Contexto

La migración es completa por perfil: `shopping` migra primero y se **desactiva** en Hermes; los otros
cinco siguen allí hasta que les toque. Durante ese período los dos sistemas conviven, pero **ningún
perfil vive en los dos**.

Tres hechos operativos hacen que "convivencia" no sea gratis:

1. **Un bot de Telegram no admite dos consumidores de `getUpdates`**: dos pollers con el mismo token
   se pisan con `409 Conflict`, y los mensajes se pierden sin ruido.
2. **Los cron son por perfil**: si el perfil se migra pero sus jobs siguen activos en el sistema
   anterior, llegan notificaciones duplicadas y no se sabe cuál sistema las produjo.
3. **Los scripts son compartidos por ruta absoluta** (`run_shopping.sh`, `price_history.db`): los dos
   sistemas los ven, así que un doble dueño también significa doble escritura sobre el mismo dato.

## Decisión

1. **Un bot por sistema.** nexo tiene su propio token; Hermes conserva el suyo. Los grupos de los
   perfiles migrados se apuntan al bot de nexo.
2. **Un solo dueño por job, declarado en la base.** Todo job lleva `owner` (`hermes` | `nexo`) y
   `enabled`. El corte de un perfil es una transacción lógica: activar sus jobs en nexo y desactivar
   el perfil en Hermes **en el mismo paso**, no en dos.
3. **Un job no puede existir sin dueño.** Un job en nexo con `owner=hermes` no se ejecuta; un job de
   Hermes marcado como migrado no se reactiva sin un cambio explícito.

## Consecuencias

- **Se gana:** cero notificaciones duplicadas y cero pérdida de updates por conflicto de polling; el
  estado de la migración es consultable (`SELECT profile, owner FROM jobs`), no una memoria.
- **Cuesta:** dos tokens de bot que rotar; el grupo de un perfil migrado cambia de bot y hay que
  avisar (un mensaje de "este perfil se mudó" evita que se escriba al bot viejo y quede mudo).
- **Se vuelve difícil:** volver atrás a medias (reactivar un perfil sin devolverle sus jobs deja al
  perfil sin proactividad, y eso es peor que no haberlo tocado).
- **Verificación:** en 24 h posteriores al corte, ningún texto duplicado entre grupos; `job_runs` sin
  dos ejecuciones para el mismo `scheduled_for`; `SELECT count(*) FROM jobs WHERE owner IS NULL` = 0.

## Alternativas rechazadas

- **Un bot para los dos sistemas, enrutando por chat** — imposible con long polling: dos pollers
  compiten por el mismo `offset`.
- **Reutilizar el bot de Hermes para nexo** — exige apagar Hermes por completo, y los otros cinco
  perfiles siguen allí.
- **Migrar todos los perfiles a la vez para evitar la coexistencia** — un fallo deja sin servicio a
  seis perfiles en lugar de uno, y no hay forma de comparar.

## Lo que habilita en una entrevista

"Migré un sistema en producción sin downtime y sin perfiles a medias: un bot por sistema, y cada job
con un dueño declarado en la base, porque dos pollers del mismo bot se pisan con 409 y dos dueños del
mismo cron producen notificaciones duplicadas. El corte es una transacción, no una serie de pasos
manuales."
