# ADR 0019 — Reglas globales inyectadas en el prompt, no como skill

- **Fecha**: 2026-11-24
- **Estado**: aceptado
- **Reemplaza**: —

## Contexto

Hay reglas que valen para todos los perfiles y no pueden fallar: idioma y registro, economía de salida,
no inventar datos, pedir confirmación antes de algo destructivo, qué skill usar para implementar
(`ponytail`) y para UI/UX (`impeccable`). El pedido fue "una skill global".

La mecánica de skills del motor es **divulgación progresiva**: el catálogo de skills se le ofrece al
modelo y el modelo decide cuándo abrir una. Eso está bien para procedimientos largos (una planilla, un
protocolo de rehabilitación), y está mal para esto: una regla que se cumple sólo cuando el modelo elige
leerla no es una regla, es una sugerencia.

## Decisión

Un único bloque de texto (`nexo/rules.py`, constante `GLOBAL_RULES`) que `dispatch.system_prompt_for()`
compone en **todos** los perfiles, en este orden:

```
SOUL del perfil  →  Reglas globales  →  ## Entorno (perfil activo, idioma, tools)
```

- Va **después** del SOUL a propósito: el SOUL es prosa escrita para otro runtime y puede contradecir
  la regla vigente; lo último que lee el modelo es lo que gana.
- Va **antes** del `## Entorno` porque las reglas son estables y el entorno cambia por perfil: así el
  prefijo se cachea mejor (el cache read fue 36 % del gasto medido).
- La protección no depende sólo del prompt: el registro tiene además un **detector determinista** de
  voseo con un reintento correctivo (ver `dispatch.language_violation`), porque el prompt por sí solo
  dejó pasar un "bajá una serie".

## Alternativas descartadas

- **Skill global**: se carga a demanda, así que se cumple a veces. Además obligaría a duplicar el texto
  o a que cada SOUL la invoque explícitamente.
- **Repetir las reglas en cada SOUL**: cinco copias que divergen en una semana y ninguna cubre perfiles
  nuevos.
- **Un archivo `.md` leído en runtime**: más cómodo de editar, pero se rompe si el paquete se instala
  sin datos y agrega un modo de falla en el arranque de cada turno. Como constante de Python, el
  release siempre la lleva y un test verifica que esté completa.

## Consecuencias

- Cuesta ~2,7 k caracteres (~700 tokens) por turno, en el prefijo estable y cacheable. Es barato al lado
  de lo que ahorra en salida: la respuesta de prueba bajó a 7 líneas y 533 caracteres.
- Cambiar una regla es un cambio de código con test: `tests/test_rules_offline.py` falla si
  desaparece alguna de las reglas pedidas, así que borrar una por accidente se nota.
- Las reglas no conocen el perfil: si un perfil necesita una excepción, va en su SOUL y la regla global
  sigue ganando en lo que declara explícitamente (idioma, veracidad, confirmación).
