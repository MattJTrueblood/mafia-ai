// Game page JavaScript

let currentGameId = null;
let gameStarted = false;
let isPaused = false;
let displayedEventCount = 0; // Track how many events we've displayed
let socket = null;
let waitingPlayer = null; // Track which player we're waiting for
let interruptingPlayers = []; // Track which players want to interrupt
let passingPlayers = []; // Track which players want to pass their turn

function initializeGame(gameId) {
    currentGameId = gameId;

    // Set up event listeners
    document.getElementById('start-btn').addEventListener('click', handleStart);
    document.getElementById('pause-btn').addEventListener('click', handlePause);
    
    // Connect to WebSocket
    socket = io();
    
    // Join the game room
    socket.emit('join_game', { game_id: gameId });
    
    // Listen for game state updates
    socket.on('game_state_update', (gameState) => {
        updateDisplay(gameState);
    });

    // Listen for discussion status updates
    socket.on('discussion_status', (status) => {
        updateDiscussionStatus(status);
    });

    // Listen for pause state updates
    socket.on('pause_state', (data) => {
        isPaused = data.paused;
        updatePauseButton();
    });
    
    // Listen for connection confirmation
    socket.on('joined_game', (data) => {
        console.log('Joined game:', data.game_id);
        // Load initial state
        loadGameState();
    });
    
    // Handle connection errors
    socket.on('connect_error', (error) => {
        console.error('WebSocket connection error:', error);
        // Fallback to polling if WebSocket fails
        loadGameState();
    });
}

async function loadGameState() {
    if (!currentGameId) return;
    
    try {
        const response = await fetch(`/game/${currentGameId}/state`);
        const gameState = await response.json();
        
        if (response.ok) {
            updateDisplay(gameState);
        } else {
            console.error('Failed to load game state:', gameState.error);
        }
    } catch (error) {
        console.error('Error loading game state:', error);
    }
}

function updateDisplay(gameState) {
    // Update phase and day
    document.getElementById('phase').textContent = gameState.phase;
    document.getElementById('day').textContent = gameState.day_number;
    
    // Update game status
    const statusEl = document.getElementById('game-status');
    if (gameState.game_over) {
        statusEl.textContent = `Game Over! ${gameState.winner === 'mafia' ? 'Mafia' : 'Town'} wins!`;
        statusEl.className = 'status game-over';
        document.getElementById('start-btn').disabled = true;
        document.getElementById('start-btn').textContent = 'Game Over';
        document.getElementById('pause-btn').disabled = true;
    } else if (gameStarted) {
        statusEl.textContent = isPaused ? 'Game paused' : 'Game running';
        statusEl.className = 'status';
    } else {
        statusEl.textContent = 'Ready to start';
        statusEl.className = 'status';
    }
    
    // Update players
    updatePlayers(gameState.players);

    // Update unified event log
    updateEventLog(gameState.events || [], gameState.players || []);
}

function updatePlayers(players) {
    const container = document.getElementById('players-list');
    container.innerHTML = '';

    players.forEach(player => {
        const card = document.createElement('div');
        let className = 'player-card';

        if (!player.alive) {
            className += ' dead';
        } else if (player.team === 'mafia') {
            className += ' mafia';
        } else if (player.team === 'town') {
            className += ' town';
        }

        // Add waiting class if this player is being queried
        const isWaiting = waitingPlayer === player.name;
        if (isWaiting) {
            className += ' waiting';
        }

        // Add interrupting class if this player wants to interrupt
        const isInterrupting = interruptingPlayers.includes(player.name);
        if (isInterrupting) {
            className += ' interrupting';
        }

        // Add passing class if this player wants to pass
        const isPassing = passingPlayers.includes(player.name);
        if (isPassing) {
            className += ' passing';
        }

        const roleDisplay = player.role || 'Unknown';
        const hasContext = player.has_context;

        // Build indicator HTML
        let indicatorHtml = '';
        if (isWaiting) {
            indicatorHtml = '<span class="waiting-indicator">...</span>';
        } else if (isInterrupting) {
            indicatorHtml = '<span class="interrupt-indicator" title="Wants to interrupt">✋</span>';
        } else if (isPassing) {
            indicatorHtml = '<span class="pass-indicator" title="Passing this turn">⏭</span>';
        }

        card.className = className;
        card.innerHTML = `
            <span class="player-name">${escapeHtml(player.name)}</span>
            ${indicatorHtml}
            <span class="player-role">${escapeHtml(roleDisplay)}</span>
            <span class="player-model">${escapeHtml(player.model)}</span>
            <button class="btn-context"
                    onclick="showPlayerContext('${escapeHtml(player.name).replace(/'/g, "\\'")}')"
                    ${hasContext ? '' : 'disabled'}
                    title="${hasContext ? 'View LLM context' : 'No context available yet'}">
                Context
            </button>
        `;

        container.appendChild(card);
    });
}

function updateDiscussionStatus(status) {
    const panel = document.getElementById('discussion-status');

    if (status.action === 'discussion_end') {
        // Hide panel and clear waiting/interrupt/pass state
        panel.classList.add('hidden');
        waitingPlayer = null;
        interruptingPlayers = [];
        passingPlayers = [];
        // Re-render players to remove indicators
        loadGameState();
        return;
    }

    // Show the panel
    panel.classList.remove('hidden');

    // Update action
    const actionEl = document.getElementById('status-action');
    const actionLabels = {
        'discussion_start': 'Starting discussion',
        'interrupt_polling': 'Checking for interrupts',
        'waiting_interrupt': 'Polling interrupt',
        'waiting_message': 'Getting message',
        'discussion_paused': 'PAUSED'
    };
    actionEl.textContent = actionLabels[status.action] || status.action;

    // Add special styling for paused state
    if (status.action === 'discussion_paused') {
        actionEl.classList.add('paused');
    } else {
        actionEl.classList.remove('paused');
    }

    // Update waiting player
    const waitingEl = document.getElementById('status-waiting');
    if (status.waiting_player) {
        waitingEl.textContent = status.waiting_player;
        waitingEl.classList.add('waiting-highlight');
        waitingPlayer = status.waiting_player;
    } else {
        waitingEl.textContent = '-';
        waitingEl.classList.remove('waiting-highlight');
        waitingPlayer = null;
    }

    // Update message count
    document.getElementById('status-messages').textContent =
        `${status.message_count} / ${status.max_messages}`;

    // Update interrupting players
    const interruptingEl = document.getElementById('status-interrupting');
    if (status.interrupting_players && status.interrupting_players.length > 0) {
        interruptingPlayers = status.interrupting_players;
        interruptingEl.textContent = status.interrupting_players.join(', ');
        interruptingEl.classList.add('has-items');
    } else {
        interruptingPlayers = [];
        interruptingEl.textContent = '-';
        interruptingEl.classList.remove('has-items');
    }

    // Update passing players
    const passingEl = document.getElementById('status-passing');
    if (passingEl) {
        if (status.passing_players && status.passing_players.length > 0) {
            passingPlayers = status.passing_players;
            passingEl.textContent = status.passing_players.join(', ');
            passingEl.classList.add('has-items');
        } else {
            passingPlayers = [];
            passingEl.textContent = '-';
            passingEl.classList.remove('has-items');
        }
    } else if (status.passing_players) {
        // Element doesn't exist yet, just update the state
        passingPlayers = status.passing_players;
    }

    // Update is_interrupt indicator
    const interruptModeEl = document.getElementById('status-interrupt-mode');
    if (interruptModeEl) {
        if (status.is_interrupt) {
            interruptModeEl.textContent = 'Yes (interrupt)';
            interruptModeEl.classList.add('is-interrupt');
        } else {
            interruptModeEl.textContent = 'No (scheduled turn)';
            interruptModeEl.classList.remove('is-interrupt');
        }
    }

    // Re-render players to show waiting/interrupt indicators
    updatePlayerIndicators();
}

function updatePlayerIndicators() {
    // Update player cards to show/hide waiting, interrupt, and pass indicators
    const cards = document.querySelectorAll('.player-card');
    cards.forEach(card => {
        const nameEl = card.querySelector('.player-name');
        if (!nameEl) return;

        const playerName = nameEl.textContent;
        const existingWaitingIndicator = card.querySelector('.waiting-indicator');
        const existingInterruptIndicator = card.querySelector('.interrupt-indicator');
        const existingPassIndicator = card.querySelector('.pass-indicator');

        // Handle waiting indicator (highest priority)
        if (playerName === waitingPlayer) {
            card.classList.add('waiting');
            if (!existingWaitingIndicator) {
                const indicator = document.createElement('span');
                indicator.className = 'waiting-indicator';
                indicator.textContent = '...';
                nameEl.after(indicator);
            }
            // Remove other indicators if showing waiting
            if (existingInterruptIndicator) existingInterruptIndicator.remove();
            if (existingPassIndicator) existingPassIndicator.remove();
            card.classList.remove('interrupting', 'passing');
        } else {
            card.classList.remove('waiting');
            if (existingWaitingIndicator) {
                existingWaitingIndicator.remove();
            }

            // Handle interrupt indicator (second priority)
            if (interruptingPlayers.includes(playerName)) {
                card.classList.add('interrupting');
                if (!existingInterruptIndicator) {
                    const indicator = document.createElement('span');
                    indicator.className = 'interrupt-indicator';
                    indicator.textContent = '✋';
                    indicator.title = 'Wants to interrupt';
                    nameEl.after(indicator);
                }
                // Remove pass indicator if showing interrupt
                if (existingPassIndicator) existingPassIndicator.remove();
                card.classList.remove('passing');
            } else {
                card.classList.remove('interrupting');
                if (existingInterruptIndicator) {
                    existingInterruptIndicator.remove();
                }

                // Handle pass indicator (third priority)
                if (passingPlayers.includes(playerName)) {
                    card.classList.add('passing');
                    if (!existingPassIndicator) {
                        const indicator = document.createElement('span');
                        indicator.className = 'pass-indicator';
                        indicator.textContent = '⏭';
                        indicator.title = 'Passing this turn';
                        nameEl.after(indicator);
                    }
                } else {
                    card.classList.remove('passing');
                    if (existingPassIndicator) {
                        existingPassIndicator.remove();
                    }
                }
            }
        }
    });
}

function updateEventLog(events, players) {
    const container = document.getElementById('event-log');

    // Check if user is scrolled to bottom (within 50px)
    const wasScrolledToBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 50;

    // Handle empty events
    if (events.length === 0) {
        if (container.children.length === 0 || (container.children.length === 1 && container.children[0].tagName === 'P')) {
            container.innerHTML = '<p class="empty-state">No events yet.</p>';
        }
        displayedEventCount = 0;
        return;
    }

    // If the log got shorter (shouldn't happen, but handle it), reset
    if (events.length < displayedEventCount) {
        container.innerHTML = '';
        displayedEventCount = 0;
    }

    // Remove empty placeholder if it exists
    const emptyState = container.querySelector('.empty-state');
    if (emptyState) emptyState.remove();

    // Create a map of player names to their team/role
    const playerMap = {};
    players.forEach(player => {
        playerMap[player.name] = {
            team: player.team,
            role: player.role || 'Unknown'
        };
    });

    // Only append new events (incremental update)
    const newEvents = events.slice(displayedEventCount);

    newEvents.forEach(event => {
        const div = createEventElement(event, playerMap);
        if (div) {
            container.appendChild(div);
        }
    });

    // Update displayed count
    displayedEventCount = events.length;

    // Only auto-scroll if user was already at the bottom
    if (wasScrolledToBottom && newEvents.length > 0) {
        container.scrollTop = container.scrollHeight;
    }
}

function createEventElement(event, playerMap) {
    const div = document.createElement('div');
    div.className = `event-entry event-${event.type}`;
    div.dataset.eventId = event.id;
    div.dataset.visibility = event.visibility;

    // Add visibility class for private events
    if (event.visibility !== 'all' && event.visibility !== 'public') {
        div.classList.add('private-event');
        div.classList.add(`visibility-${event.visibility}`);
    }

    // Phase/day indicator
    const phaseLabel = `<span class="event-phase">${event.phase.toUpperCase()} ${event.day}</span>`;

    // Build content based on event type
    let content = '';

    if (['discussion', 'vote', 'mafia_chat', 'role_action'].includes(event.type)) {
        // Message with player name
        const playerInfo = playerMap[event.player] || {};
        const nameClass = getPlayerNameClass(playerInfo);
        const roleDisplay = playerInfo.role || 'Unknown';

        content = `${phaseLabel}`;
        if (event.player) {
            content += `<span class="${nameClass}">${escapeHtml(event.player)} (${escapeHtml(roleDisplay)}):</span>`;
        }
        content += `<span class="event-message">${escapeHtml(event.message)}</span>`;

        if (event.visibility !== 'all' && event.visibility !== 'public') {
            content += `<span class="visibility-badge">${getVisibilityLabel(event.visibility)}</span>`;
        }
    } else {
        // System/game event (phase_change, death, vote_result, system)
        content = `${phaseLabel}<span class="event-message">${escapeHtml(event.message)}</span>`;
    }

    div.innerHTML = content;
    return div;
}

function getPlayerNameClass(playerInfo) {
    let className = 'player-name';
    if (playerInfo.team === 'mafia') {
        className += ' mafia';
    } else if (playerInfo.team === 'town') {
        className += ' town';
    }
    return className;
}

function getVisibilityLabel(visibility) {
    const labels = {
        'mafia': 'MAFIA ONLY',
        'sheriff': 'SHERIFF ONLY',
        'doctor': 'DOCTOR ONLY',
        'vigilante': 'VIGILANTE ONLY'
    };
    if (typeof visibility === 'string') {
        return labels[visibility] || visibility.toUpperCase();
    }
    return 'PRIVATE';
}

async function handleStart() {
    if (gameStarted || !currentGameId) return;

    const startBtn = document.getElementById('start-btn');
    const pauseBtn = document.getElementById('pause-btn');

    startBtn.disabled = true;
    startBtn.textContent = 'Starting...';

    try {
        const response = await fetch(`/game/${currentGameId}/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const data = await response.json();

        if (response.ok) {
            gameStarted = true;
            startBtn.textContent = 'Running';
            pauseBtn.disabled = false;
        } else {
            console.error('Failed to start game:', data.error);
            startBtn.disabled = false;
            startBtn.textContent = 'Start Game';
        }
    } catch (error) {
        console.error('Error starting game:', error);
        startBtn.disabled = false;
        startBtn.textContent = 'Start Game';
    }
}

async function handlePause() {
    if (!currentGameId || !gameStarted) return;

    const pauseBtn = document.getElementById('pause-btn');
    pauseBtn.disabled = true;

    try {
        const response = await fetch(`/game/${currentGameId}/pause`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const data = await response.json();

        if (response.ok) {
            isPaused = data.paused;
            updatePauseButton();
            updateStartButton();
        } else {
            console.error('Failed to toggle pause:', data.error);
        }
    } catch (error) {
        console.error('Error toggling pause:', error);
    } finally {
        pauseBtn.disabled = false;
    }
}

function updatePauseButton() {
    const pauseBtn = document.getElementById('pause-btn');

    if (isPaused) {
        pauseBtn.textContent = 'Resume';
        pauseBtn.classList.add('paused');
    } else {
        pauseBtn.textContent = 'Pause';
        pauseBtn.classList.remove('paused');
    }
}

function updateStartButton() {
    const startBtn = document.getElementById('start-btn');

    if (gameStarted) {
        if (isPaused) {
            startBtn.textContent = 'Paused';
        } else {
            startBtn.textContent = 'Running';
        }
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Context modal state
let currentContextData = null;

async function showPlayerContext(playerName) {
    if (!currentGameId) return;

    try {
        const response = await fetch(`/game/${currentGameId}/player/${encodeURIComponent(playerName)}/context`);
        const data = await response.json();

        if (!response.ok) {
            alert('Error: ' + (data.error || 'Failed to load context'));
            return;
        }

        currentContextData = data;

        // Populate modal
        document.getElementById('modal-player-name').textContent = data.player_name;
        document.getElementById('modal-action-type').textContent = data.context.action_type || 'unknown';
        document.getElementById('modal-phase').textContent = data.context.phase || 'unknown';
        document.getElementById('modal-day').textContent = data.context.day || 'unknown';
        document.getElementById('modal-timestamp').textContent = formatTimestamp(data.context.timestamp);

        // Estimate token count (rough estimate: ~4 chars per token for English)
        const promptText = data.context.messages?.[0]?.content || '';
        const estimatedTokens = Math.ceil(promptText.length / 4);
        document.getElementById('modal-tokens').textContent = estimatedTokens.toLocaleString();

        // Display prompt
        const prompt = data.context.messages?.[0]?.content || 'No prompt available';
        document.getElementById('modal-prompt').textContent = prompt;

        // Display response
        const response_data = data.context.response || {};
        let responseText = '';
        if (response_data.content) {
            responseText = response_data.content;
        }
        if (response_data.structured_output) {
            responseText += '\n\n--- Parsed Structured Output ---\n';
            responseText += JSON.stringify(response_data.structured_output, null, 2);
        }

        // Add debug info if available
        if (data.context.debug) {
            responseText += '\n\n--- Debug Info ---\n';
            responseText += JSON.stringify(data.context.debug, null, 2);
        }

        // Add error info if available
        if (data.context.error) {
            responseText += '\n\n--- ERROR ---\n';
            responseText += data.context.error;
        }

        document.getElementById('modal-response').textContent = responseText || 'No response available';

        // Show modal
        document.getElementById('context-modal').classList.add('active');

    } catch (error) {
        alert('Error loading context: ' + error.message);
    }
}

function closeContextModal() {
    document.getElementById('context-modal').classList.remove('active');
    currentContextData = null;

    // Reset copy button
    const copyBtn = document.querySelector('.btn-copy');
    if (copyBtn) {
        copyBtn.textContent = 'Copy to Clipboard';
        copyBtn.classList.remove('copied');
    }
}

function copyContext() {
    if (!currentContextData) return;

    const ctx = currentContextData.context;
    const prompt = ctx.messages?.[0]?.content || 'No prompt';
    const response = ctx.response?.content || 'No response';
    const structured = ctx.response?.structured_output
        ? JSON.stringify(ctx.response.structured_output, null, 2)
        : 'None';
    const debug = ctx.debug
        ? JSON.stringify(ctx.debug, null, 2)
        : 'None';
    const error = ctx.error || 'None';
    const estimatedTokens = Math.ceil(prompt.length / 4);

    const textToCopy = `=== LLM Context for ${currentContextData.player_name} ===
Action: ${ctx.action_type}
Phase: ${ctx.phase}
Day: ${ctx.day}
Timestamp: ${ctx.timestamp}
Est. Tokens: ${estimatedTokens.toLocaleString()}

=== PROMPT ===
${prompt}

=== RESPONSE ===
${response}

=== STRUCTURED OUTPUT ===
${structured}

=== DEBUG INFO ===
${debug}

=== ERROR ===
${error}
`;

    navigator.clipboard.writeText(textToCopy).then(() => {
        const copyBtn = document.querySelector('.btn-copy');
        copyBtn.textContent = 'Copied!';
        copyBtn.classList.add('copied');

        setTimeout(() => {
            copyBtn.textContent = 'Copy to Clipboard';
            copyBtn.classList.remove('copied');
        }, 2000);
    }).catch(err => {
        alert('Failed to copy: ' + err);
    });
}

function formatTimestamp(isoString) {
    if (!isoString) return 'unknown';
    try {
        const date = new Date(isoString);
        return date.toLocaleTimeString();
    } catch {
        return isoString;
    }
}

// Close modal when clicking overlay
document.addEventListener('click', (e) => {
    if (e.target.id === 'context-modal') {
        closeContextModal();
    }
});

// Close modal on Escape key
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        closeContextModal();
    }
});

