"""Normalización compartida de motes y títulos; sin dependencias de OCR."""

# COL-102, reapertura estructural del 26 sep, job real `10a7fba6fda04585`,
# partida 5 (Ender): "Ender sent out MineMine the Peckish!" no es una entrada
# doble ni un mote raro -"the Peckish" es un Título real de Pokémon Champions
# (la Lunchtime Mark, heredada de Espada/Escudo vía Pokémon HOME junto con
# naturaleza, forma y shiny; confirmado contra la página de marcas y títulos
# de Champions, no es genérico de Escarlata/Púrpura). El juego lo pega al
# mote al anunciar la entrada; sin reconocerlo, "MineMine the Peckish" se
# leía como un solo mote mezclado y "MineMine" solo nunca alcanzaba el
# margen de desempate contra él. Lista exhaustiva y verificada, no un
# patrón genérico "the X": un mote real podría legítimamente contener
# " the " suelto, y una entrada doble real dice "X and Y", nunca "X the Y"
# -de hecho "the Tried and True" es un título real que SÍ contiene " and ",
# así que hay que descartar el título antes de mirar si el resto tiene una
# entrada doble, no al revés.
_POKEMON_TITLES = (
    # Marcas (Marks): especiales, de clima/hora, de rareza y de personalidad.
    "the Chosen One",
    "the Treasure Hunter",
    "the Gourmet",
    "the Great",
    "the Unrivaled",
    "the Teeny",
    "the Reliable Partner",
    "the Former Titan",
    "the Former Alpha",
    "the Peckish",
    "the Sleepy",
    "the Dozy",
    "the Early Riser",
    "the Cloud Watcher",
    "the Sodden",
    "the Thunderstruck",
    "the Snow Frolicker",
    "the Shivering",
    "the Sandswept",
    "the Parched",
    "the Mist Drifter",
    "the Recluse",
    "the Sociable",
    "the Rowdy",
    "the Spacey",
    "the Anxious",
    "the Giddy",
    "the Radiant",
    "the Serene",
    "the Feisty",
    "the Daydreamer",
    "the Joyful",
    "the Furious",
    "the Beaming",
    "the Teary-Eyed",
    "the Chipper",
    "the Grumpy",
    "the Scholar",
    "the Rampaging",
    "the Opportunist",
    "the Stern",
    "the Kindhearted",
    "the Easily Flustered",
    "the Driven",
    "the Apathetic",
    "the Arrogant",
    "the Reluctant",
    "the Humble",
    "the Pompous",
    "the Lively",
    "the Worn-Out",
    "the Curry Connoisseur",
    "the Catch of the Day",
    # Títulos derivados de listones (Ribbons): campeón, torre/instalaciones,
    # concursos, memoria y eventos.
    "the Paldea Champion",
    "the Galar Champion",
    "the Alola Champion",
    "the Hoenn Champion",
    "the Kalos Champion",
    "the Sinnoh Champion",
    "of the Distant Past",
    "the Great Friend",
    "the Once Well-Trained",
    "the Rank Master",
    "the Pokémon Fan",
    "the Tower Master",
    "the Royal Master",
    "the Tree Victor",
    "the Tree Master",
    "the Gorgeous",
    "the Royal",
    "the Gorgeous Royal",
    "the Strutter",
    "the Veteran",
    "the Master",
    "the Living Legend",
    "the Once Vigilant",
    "the Once Cowardly",
    "the Once Shaken",
    "the Once Imperfect",
    "the Once Well-Rested",
    "the Once Sleepy",
    "the Once Cheery",
    "the Tried and True",
    "the Former Star",
    "the Vintage Beauty",
    "the Former Idol",
    "the Historic Genius",
    "the Formerly Buff",
    "the Shining Star",
    "the Twinkling Star",
    "the Treasured Memory",
    "the Exciting Memory",
    "the Battle Champion",
    "the Wish Granter",
    "the Festive",
    "the Celebratory",
    "the Cherished",
    "the Premium",
)
# Los títulos más largos primero: "the Royal Master" no puede cortarse en
# "the Royal" y dejar "Master" pegado al mote.
_POKEMON_TITLES_BY_LENGTH = tuple(sorted(_POKEMON_TITLES, key=len, reverse=True))


def strip_pokemon_title(value: str) -> str:
    """Saca un Título/Marca de Pokémon Champions pegado al final de un mote.

    Compara contra la lista exhaustiva y exacta -no un patrón genérico
    "the X"- para no confundir un mote real que contenga " the " suelto, ni
    una entrada doble real ("X and Y"). Sin coincidencia, devuelve el texto
    intacto.
    """

    stripped = value.strip()
    lowered = stripped.casefold()
    for title in _POKEMON_TITLES_BY_LENGTH:
        suffix = f" {title}".casefold()
        if lowered.endswith(suffix) and len(stripped) > len(suffix):
            return stripped[: -len(suffix)].strip()
    return stripped
