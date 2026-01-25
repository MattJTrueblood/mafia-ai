"""Role definitions and logic for the Mafia game."""


class Role:
    """Base class for all roles."""
    
    def __init__(self, name):
        self.name = name
    
    def __str__(self):
        return self.name
    
    def __eq__(self, other):
        if isinstance(other, Role):
            return self.name == other.name
        return False
    
    def __hash__(self):
        return hash(self.name)


class Mafia(Role):
    """Mafia role - wins with mafia, loses with town."""

    def __init__(self):
        super().__init__("Mafia")
        self.team = "mafia"


class Godfather(Role):
    """Godfather role - mafia leader who appears innocent to sheriff."""

    def __init__(self):
        super().__init__("Godfather")
        self.team = "mafia"
        self.investigation_immunity_used = False  # Track if immunity has been consumed


class Villager(Role):
    """Villager role - basic town member with no special abilities."""

    def __init__(self):
        super().__init__("Villager")
        self.team = "town"


class Miller(Role):
    """Miller role - town member who appears guilty to sheriff."""

    def __init__(self):
        super().__init__("Miller")
        self.team = "town"
        self.false_positive_used = False  # Track if false positive has been consumed


class Sheriff(Role):
    """Sheriff role - can investigate players at night."""

    # Night action steps - used by phases.py for automatic step sequencing
    night_steps = ["sheriff_discuss", "sheriff_act"]

    def __init__(self):
        super().__init__("Sheriff")
        self.team = "town"
        self.investigations = []  # List of (player_name, result) tuples


class Doctor(Role):
    """Doctor role - can protect players at night."""

    # Night action steps - used by phases.py for automatic step sequencing
    night_steps = ["doctor_discuss", "doctor_act"]

    def __init__(self):
        super().__init__("Doctor")
        self.team = "town"
        self.last_protected = None  # Cannot protect same person twice in a row


class Vigilante(Role):
    """Vigilante role - can kill one player during the game."""

    # Night action steps - used by phases.py for automatic step sequencing
    night_steps = ["vigilante_discuss", "vigilante_act"]

    def __init__(self):
        super().__init__("Vigilante")
        self.team = "town"
        self.bullet_used = False  # One bullet for entire game


class Jester(Role):
    """Jester role - third party that wins by being lynched."""

    def __init__(self):
        super().__init__("Jester")
        self.team = "third_party"


class Survivor(Role):
    """Survivor role - third party that wins by surviving to the end."""

    def __init__(self):
        super().__init__("Survivor")
        self.team = "third_party"


class Mason(Role):
    """Mason role - town member who knows the other Masons."""

    night_steps = ["mason_discussion"]

    def __init__(self):
        super().__init__("Mason")
        self.team = "town"


class Tracker(Role):
    """Tracker role - town member who can see who their target visited."""

    night_steps = ["tracker_discuss", "tracker_act"]

    def __init__(self):
        super().__init__("Tracker")
        self.team = "town"
        self.tracking_results = []  # List of (target, visited) tuples


class Escort(Role):
    """Escort role - town roleblocker who prevents targets from using night abilities."""

    night_steps = ["escort_discuss", "escort_act"]

    def __init__(self):
        super().__init__("Escort")
        self.team = "town"
        self.block_history = []  # List of blocked player names


class Grandma(Role):
    """Grandma role - town member immune to night kills who kills visitors."""

    def __init__(self):
        super().__init__("Grandma")
        self.team = "town"


class Executioner(Role):
    """Executioner role - third party who wins by getting their target lynched."""

    def __init__(self):
        super().__init__("Executioner")
        self.team = "third_party"
        self.target = None  # Name of the town player they must get lynched
        self.has_won = False  # Track if they achieved their win


class Amnesiac(Role):
    """Amnesiac role - third party who can remember a dead player's role."""

    night_steps = ["amnesiac_discuss", "amnesiac_act"]

    def __init__(self):
        super().__init__("Amnesiac")
        self.team = "third_party"
        self.has_remembered = False  # Can only remember once


class Medium(Role):
    """Medium role - town member who can ask dead players yes/no questions."""

    night_steps = ["medium_discuss", "medium_act"]

    def __init__(self):
        super().__init__("Medium")
        self.team = "town"
        self.seance_history = []  # List of (dead_player, question, answer) tuples


class Consort(Role):
    """Consort role - mafia roleblocker who prevents targets from using night abilities."""

    night_steps = ["consort_discuss", "consort_act"]

    def __init__(self):
        super().__init__("Consort")
        self.team = "mafia"
        self.block_history = []  # List of blocked player names


class Consigliere(Role):
    """Consigliere role - undercover mafia member who appears innocent to sheriff.

    Does not participate in mafia night discussions, but mafia know their identity.
    Can choose to permanently convert to regular Mafia role each night (before mafia discussion).
    """

    night_steps = ["consigliere_convert"]  # Conversion happens before mafia discussion

    def __init__(self):
        super().__init__("Consigliere")
        self.team = "mafia"
        self.has_converted = False  # Once True, they become regular Mafia


# Role registry
ROLE_CLASSES = {
    "Mafia": Mafia,
    "Godfather": Godfather,
    "Consort": Consort,
    "Consigliere": Consigliere,
    "Villager": Villager,
    "Miller": Miller,
    "Sheriff": Sheriff,
    "Doctor": Doctor,
    "Vigilante": Vigilante,
    "Jester": Jester,
    "Survivor": Survivor,
    "Mason": Mason,
    "Tracker": Tracker,
    "Escort": Escort,
    "Grandma": Grandma,
    "Executioner": Executioner,
    "Amnesiac": Amnesiac,
    "Medium": Medium,
}

