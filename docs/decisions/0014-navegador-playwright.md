# 0014 — Navegador Playwright como comando del sistema, no tool del motor

**Estado:** aceptado · **Fecha:** 2026-11-24

## Contexto

El perfil piloto usó el navegador **20 veces en 7 semanas** (`browser_navigate` 10, `browser_console`
7, `browser_exec` 3). No es volumen: es el caso donde la consulta es "¿esta oferta es real?", y las
tiendas chilenas renderizan precio, stock y "precio con plan" por JavaScript. El scraper del perfil usa
HTTP directo y por eso no cubre esos casos.

El motor elegido (ADR 0001) **no trae tool de navegador**: su repertorio verificado es `bash`, `edit`,
`read`, `write`, `grep`, `glob`, `webfetch`, `websearch`, `question`, `todowrite`, `task` y MCP. Sin un
navegador, esa capacidad del perfil se pierde y el corte no puede declararse completo.

## Decisión

Playwright con Chromium, instalado una vez en el host, expuesto al agente como **comando estable del
sistema**:

```
nexo browse <url> [--wait <selector>] [--json]
```

- El agente lo invoca por `bash`, igual que llama a `hermes-trainer-sheets` o `price_monitor.py`.
- Devuelve JSON (`url`, `title`, `text`, `prices[]`, `http_status`) para que el resultado sea
  procesable y no una pared de HTML.
- Timeout propio y límite de salida: un sitio colgado no debe colgar el turno.
- **El contrato es nuestro**: no depende de una tool del motor ni de un endpoint experimental. Si el
  motor cambia, el navegador sigue funcionando; si cambia Playwright, se toca un lugar.

## Consecuencias

- **Se gana:** capacidad completa de verificación en sitios JS-heavy; contrato propio y testeable por
  CLI; sirve después para `freelance` y `finance` sin trabajo extra.
- **Cuesta:** ~300 MB de Chromium y una dependencia de sistema que hay que actualizar; cada invocación
  levanta un browser (1–3 s de arranque por llamada); hay que cuidar el consumo de RAM en el homelab
  (un solo Chromium a la vez, `--max-instances` lógico).
- **Se vuelve difícil:** usar features específicas del motor si algún día agrega navegador nativo (se
  preferiría igualmente el comando propio, por portabilidad).
- **Verificación:** abrir las 4 tiendas de `targets.json` y devolver precio y stock reales; si un sitio
  que hoy funciona con el navegador anterior falla, el gap sigue abierto. Latencia por llamada medida
  y registrada, no estimada.

## Alternativas rechazadas

- **Solo `webfetch` del motor** — sin JavaScript: pierde exactamente los casos (precio dinámico, stock,
  checkout) por los que uno consulta a mano.
- **Dejar la capacidad fuera del v0** — obliga a volver a Hermes para esas consultas, y rompe la regla
  de un perfil en un solo sistema.
- **Servidor MCP de navegador** — mismo resultado con una dependencia más y su propio ciclo de
  versiones; además mete un protocolo entre el agente y una herramienta que es, en el fondo, un
  comando.
- **Tool de navegador dentro de `nexo`** (proceso propio con estado entre llamadas) — más cómodo para
  sesiones largas, pero agrega un servicio con ciclo de vida y memoria permanente para 20 llamadas en
  7 semanas.

## Lo que habilita en una entrevista

"El motor no trae navegador y el perfil lo usa para verificar precios en sitios que renderizan por
JavaScript. Lo resolví como comando del sistema con salida JSON, no como parte del motor: el contrato
es mío, se testea por CLI y sobrevive a un cambio del motor. El costo es 300 MB y 1–3 s por invocación,
que para verificar una oferta es aceptable."
