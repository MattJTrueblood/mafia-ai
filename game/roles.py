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


class Villager(Role):
    """Villager role - basic town member with no special abilities."""

    def __init__(self):
        super().__init__("Villager")
        self.team = "town"


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


# Role registry
ROLE_CLASSES = {
    "Mafia": Mafia,
    "Villager": Villager,
    "Sheriff": Sheriff,
    "Doctor": Doctor,
    "Vigilante": Vigilante,
    "Jester": Jester,
}

