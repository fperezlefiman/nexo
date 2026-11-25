# 0005 — Turnos con máquina de estados y attempts para pausar

**Estado:** aceptado · **Fecha:** 2026-11-24

## Contexto

Una de las razones del reemplazo es que hoy **no se puede pausar** un turno: o termina, o se mata y lo
producido se pierde. El motor ofrece `POST /api/session/{id}/interrupt`, que corta la ejecución, pero
no ofrece "pausar y reanudar después": no hay estado, ni checkpoints de negocio, ni forma de retomar
con lo ya producido.

Además, cualquier proceso puede morir a mitad de un turno (deploy, OOM, corte de luz). Hoy eso deja
una respuesta a medias sin registro.

## Decisión

Un turno es una **entidad lógica con una máquina de estados**, persistida:

```
queued → running → awaiting_permission → running → completed
                 ↘ paused → running (attempt nuevo)
                 ↘ failed | expired
```

`turns` guarda el turno lógico (uno por mensaje de entrada, con `idempotency_key` único) y
`turn_attempts` guarda cada intento físico con su modelo, sesión del motor, tokens, costo, latencia y
error clasificado.

- **Pausa** = `interrupt` + persistir la salida parcial + `state='paused'`. Es una decisión nuestra,
  no una función del motor.
- **Reanudar** = nuevo attempt sobre el mismo turno lógico, con el transcript actualizado.
- **Lease:** el worker toma el turno con `FOR UPDATE SKIP LOCKED` y renueva `locked_until` cada 15 s.
  Un turno `running` con lease vencido pasa a `expired` y el reconciliador lo reencola (máximo N
  intentos, después `failed` con causa).

## Consecuencias

- **Se gana:** pausa real y persistente entre procesos y reinicios; reintentos acotados; comparación
  de modelos sobre el mismo turno (dos attempts, mismo input); trazabilidad de costo por intento.
- **Cuesta:** una tabla más y un reconciliador que hay que probar; el turno puede quedar "a medias"
  por diseño, así que el canal tiene que saber mostrar ese estado en vez de una respuesta incompleta
  silenciosa.
- **Se vuelve difícil:** razonar el turno como una llamada única y atómica; aceptar que un turno puede
  tener dos respuestas parciales.
- **Verificación:** test de integración que (1) pausa un turno en curso, mata el proceso, reinicia y
  reanuda; (2) deja un turno con lease vencido y comprueba que el reconciliador lo reencola una sola
  vez; (3) envía el mismo `idempotency_key` dos veces y comprueba que hay un solo turno.

## Alternativas rechazadas

- **Estado en memoria del worker** — se pierde con el proceso; exactamente el problema que se está
  resolviendo.
- **Un proceso dedicado por conversación** — pausar sería matar el proceso y perder el contexto;
  escala mal y complica el deploy.
- **Usar `interrupt` del motor como si fuera pausa** — corta la ejecución pero no registra nada: sin
  nuestro estado, reanudar es repetir el turno desde cero y pagarlo dos veces.

## Lo que habilita en una entrevista

"Pausa no era una feature del motor, era una feature del producto: la armé como máquina de estados
persistida con attempts, lease y reconciliador. La prueba es un test que mata el proceso en medio del
turno y reanuda con contexto; y el mismo input entregado dos veces genera un solo turno."
