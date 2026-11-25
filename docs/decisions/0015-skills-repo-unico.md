# ADR 0015 — Skills: un repo es la fuente, el motor las carga, no se inyectan

- **Fecha**: 2026-11-24
- **Estado**: aceptado
- **Reemplaza**: —

## Contexto

Las skills del sistema viejo viven copiadas en cada perfil de Hermes (~26 por perfil, casi todas
iguales). Cinco son propias: 3 del entrenador y 2 del canal freelance. Editarlas
significa acordarse en cuál perfil está la buena, y no hay versión: dos perfiles pueden tener el
mismo archivo con contenido distinto y nadie se entera.

Al medir los cuerpos apareció el dato que decidió el diseño: **35 k caracteres cada una** (77 k el
perfil entrenador completo). Inyectarlas en el prompt de cada turno sería pagar decenas de miles de
tokens por conversación que casi nunca las usa.

## Decisión

1. **Un directorio git (`skills/`) es la fuente de verdad.** Nada se edita en las copias.
2. **La base es un índice, no la fuente**: guarda hash de contenido, descripción y allowlist por
   perfil (`skills.profiles`). Un segundo `sync` sin cambios reindexa **0**.
3. **El runtime es el directorio de skills del motor** (`~/.config/opencode/skills/`), que las carga
   bajo demanda con divulgación progresiva. `nexo skills deploy` lo mantiene por manifiesto: borra
   sólo lo que él mismo escribió.
4. **Nada se inyecta en el prompt.** El motor decide cuándo abrir una skill.
5. **`nexo skills export --hermes`** copia el repo a `skills/shared/` de cada perfil del sistema
   viejo, respetando la allowlist, y **reporta colisiones**: si el slug ya existe en otro lugar del
   perfil, hay dos copias de la misma skill y hay que retirar la vieja.

## Alternativas descartadas

- **Repo aparte de código**: mejor separación, pero un operador único no gana nada y suma un
  despliegue más que mantener.
- **Inyectar los cuerpos en el prompt**: simple y sin dependencias del motor; cuesta 77 k caracteres
  por turno y el presupuesto cortaba por orden alfabético — la primera skill entraba, las demás no.
  Ese bug existió en este repositorio durante una hora, se midió y se descartó el enfoque.
- **Base de datos como fuente**: permite editar desde el chat, pero pierde revisión, diff y rollback.
  Un archivo en git es más barato de auditar que una tabla.

## Consecuencias

- Un `git pull` no cambia el comportamiento: hay que correr `nexo skills sync && nexo skills deploy`.
- El directorio de skills del motor es **global**: un perfil puede cargar la skill de otro. Se acepta
  porque todas son del operador; la allowlist ya existe y se aplica en el export a Hermes, así que
  aislar por perfil es cuestión de un directorio por perfil el día que importe.
- Una skill sin frontmatter válido no rompe el sync: se reporta en `invalid` y las demás siguen.
