# ADR 0017 — Se retira `shared-memory`: se absorbe lo que tenía y se queda un solo camino

- **Fecha**: 2026-11-24
- **Estado**: aceptado
- **Reemplaza parcialmente**: 0016 (que dejó dos caminos de escritura de memoria abiertos)

## Contexto

El motor tenía registrado un segundo servidor MCP, `shared-memory` (`~/bin/shared-memory-mcp`,
binario Go), conviviendo con el `nexo` que acabamos de construir. Dos formas de escribir memoria en el
mismo agente es una invitación a que la mitad de los hechos viva donde nadie los busca.

Antes de desconectarlo había que ver qué guardaba. No es memoria de chat: es un **grafo de
conocimiento por proyecto** (entidades, observaciones, relaciones), con almacenamiento remoto en
Postgres (`<host>:5433`) y caché local en `~/.local/share/shared-memory-mcp/local.db`.

Lo que tenía, medido:

| Proyecto | Entidades | Observaciones |
|---|---|---|
| repo del homelab | 172 (148 vigentes) | 2046 (1413 vigentes) |
| `__defaults` | 1 | 6 (plantilla de política, no conocimiento) |
| `homelab` | 0 | 0 (proyecto vacío que era el activo) |
| repo de nexo | 0 | 0 |

## Decisión

1. **Absorber**: exportar el grafo vigente a
   `docs/importado/shared-memory-grafo.{json,md}` (no publicado en este repo). El JSON es lossless (entidades,
   observaciones y relaciones); el Markdown es la versión buscable con `grep`.
2. **No importar esas observaciones a `memory_items`.** Son conocimiento de infraestructura del
   homelab (later, HA, pipelines), no memoria conversacional por perfil. `memory_items` se inyecta en
   el prompt de cada turno y es por perfil: meter 1413 hechos de infra aplastaría la memoria real de
   cada bot. El archivo conserva el dato, con sus relaciones, cosa que una tabla plana perdería.
3. **Desconectar** el servidor del registro MCP del motor. El binario y la base remota **no se
   borran**: retirar el cableado no es destruir el dato, y la exportación queda versionada por si
   algún día hace falta.
4. **El motor pasa a estar supervisado.** Al reiniciarlo se descubrió que corría como proceso suelto
   desde hacía 15 días: si se caía, nada lo levantaba. Ahora es `opencode-engine.service` con
   `Restart=always`, junto a los otros tres de nexo.

## Alternativas descartadas

- **Dejarlo conviviendo**: cada memoria escrita tendría dos destinos posibles y ningún criterio.
- **Importar todo a `memory_items`**: degrada los prompts de todos los perfiles y pierde las
  relaciones entre entidades.
- **Borrar el binario y la base remota**: irreversible y sin necesidad. Desconectar consigue lo mismo
  con una puerta abierta.

## Consecuencias

- Queda **un solo camino** de escritura de memoria: `memory_write` (nexo), por perfil.
- `docs/importado/*.json` y `*.md` son texto muerto en git: pesan ~470 KB y no se indexan. Están para
  consulta humana, no para que el agente los lea.
- Se exportaron 1413 de 2052 observaciones: las 639 restantes estaban marcadas como borradas en el
  origen y no se recuperaron a propósito.
- El motor ahora se reinicia solo. `systemctl --user restart opencode-engine` es el camino para
  aplicar cambios de configuración que antes requerían matar un proceso a mano.
