# Skills

Una skill es una carpeta con un `SKILL.md`: frontmatter (`name`, `description`) y un cuerpo breve.
El motor las carga **bajo demanda** por su `description`, así que esa línea es lo que decide si se usa.

## Layout

- `shared/<slug>/` — herramientas genéricas que sirven a cualquier perfil (`github`, `pdf`, `plan`).
- `<perfil>/<slug>/` — skill privada de un perfil. Solo se indexa para ese perfil.

## Tus propios perfiles

Este repo no trae perfiles: los perfiles son datos tuyos. Para tenerlos:

1. Crea un directorio fuera del repo con la misma estructura (`<perfil>/<slug>/SKILL.md`).
2. Apunta `NEXO_SKILLS_PRIVATE` a él, o deja el directorio en `$NEXO_HOME/skills-private`
   (por defecto `~/.local/share/nexo/skills-private`).
3. `nexo skills sync` indexa las dos raíces y `nexo skills deploy` copia ambas al motor.

`skills/` (este repo) es la raíz pública; la privada se suma, nunca la reemplaza. Una skill con la
misma carpeta en las dos raíces se indexa una sola vez: gana la que se lea última.

## Crear una skill

Copia `skills/.template/` a `shared/<slug>/` (o a tu raíz privada), cambia `name` y `description`, y
corre `nexo skills sync && nexo skills deploy`. El agente puede hacerlo solo con `skill_write`.
