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


class Town(Role):
    """Town role - basic town member with no special abilities."""
    
    def __init__(self):
        super().__init__("Town")
        self.team = "town"


class Sheriff(Role):
    """Sheriff role - can investigate players at night."""
    
    def __init__(self):
        super().__init__("Sheriff")
        self.team = "town"
        self.investigations = []  # List of (player_name, result) tuples


class Doctor(Role):
    """Doctor role - can protect players at night."""
    
    def __init__(self):
        super().__init__("Doctor")
        self.team = "town"
        self.last_protected = None  # Cannot protect same person twice in a row


class Vigilante(Role):
    """Vigilante role - can kill one player during the game."""
    
    def __init__(self):
        super().__init__("Vigilante")
        self.team = "town"
        self.bullet_used = False  # One bullet for entire game


# Role registry
ROLE_CLASSES = {
    "Mafia": Mafia,
    "Town": Town,
    "Sheriff": Sheriff,
    "Doctor": Doctor,
    "Vigilante": Vigilante,
}

