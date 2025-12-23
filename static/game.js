// Game JavaScript utilities

// Utility functions for game interactions
const GameUtils = {
    formatPlayerName: function(playerId, players) {
        const player = players.find(p => p.player_id === playerId);
        return player ? player.name : playerId;
    },
    
    formatPhase: function(phase) {
        return phase.charAt(0).toUpperCase() + phase.slice(1);
    },
    
    formatRole: function(role) {
        return role.charAt(0).toUpperCase() + role.slice(1);
    }
};

// Export for use in templates
if (typeof module !== 'undefined' && module.exports) {
    module.exports = GameUtils;
}

