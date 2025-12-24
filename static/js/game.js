// Game page JavaScript

let currentGameId = null;
let isProcessing = false;
let displayedEventCount = 0; // Track how many events we've displayed
let socket = null;
let waitingPlayer = null; // Track which player we're waiting for

function initializeGame(gameId) {
    currentGameId = gameId;
    
    // Set up event listeners
    document.getElementById('next-btn').addEventListener('click', handleNext);
    
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
        document.getElementById('next-btn').disabled = true;
    } else {
        statusEl.textContent = 'Game in progress';
        statusEl.className = 'status';
        document.getElementById('next-btn').disabled = false;
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

        const roleDisplay = player.role || 'Unknown';
        const hasContext = player.has_context;

        card.className = className;
        card.innerHTML = `
            <span class="player-name">${escapeHtml(player.name)}</span>
            ${isWaiting ? '<span class="waiting-indicator">...</span>' : ''}
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
        // Hide panel and clear waiting state
        panel.classList.add('hidden');
        waitingPlayer = null;
        // Re-render players to remove waiting indicator
        loadGameState();
        return;
    }

    // Show the panel
    panel.classList.remove('hidden');

    // Update action
    const actionEl = document.getElementById('status-action');
    const actionLabels = {
        'discussion_start': 'Starting discussion',
        'priority_polling': 'Polling priorities',
        'waiting_priority': 'Getting priority',
        'waiting_message': 'Getting message',
        'urgent_check': 'Checking for urgent info'
    };
    actionEl.textContent = actionLabels[status.action] || status.action;

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

    // Update queues
    const accusedEl = document.getElementById('status-accused');
    if (status.accused_queue && status.accused_queue.length > 0) {
        accusedEl.textContent = status.accused_queue.join(', ');
        accusedEl.classList.add('has-items');
    } else {
        accusedEl.textContent = '-';
        accusedEl.classList.remove('has-items');
    }

    const questionedEl = document.getElementById('status-questioned');
    if (status.questioned_queue && status.questioned_queue.length > 0) {
        questionedEl.textContent = status.questioned_queue.join(', ');
        questionedEl.classList.add('has-items');
    } else {
        questionedEl.textContent = '-';
        questionedEl.classList.remove('has-items');
    }

    // Re-render players to show waiting indicator
    // We need to fetch current players or cache them
    if (status.waiting_player) {
        updateWaitingIndicator(status.waiting_player);
    }
}

function updateWaitingIndicator(playerName) {
    // Update player cards to show/hide waiting indicator
    const cards = document.querySelectorAll('.player-card');
    cards.forEach(card => {
        const nameEl = card.querySelector('.player-name');
        const existingIndicator = card.querySelector('.waiting-indicator');

        if (nameEl && nameEl.textContent === playerName) {
            card.classList.add('waiting');
            if (!existingIndicator) {
                const indicator = document.createElement('span');
                indicator.className = 'waiting-indicator';
                indicator.textContent = '...';
                nameEl.after(indicator);
            }
        } else {
            card.classList.remove('waiting');
            if (existingIndicator) {
                existingIndicator.remove();
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
        if (event.priority) {
            content += `<span class="priority-badge ${getPriorityClass(event.priority)}" title="Priority: ${event.priority}/10">P${event.priority}</span>`;
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

function getPriorityClass(priority) {
    if (priority >= 9) return 'priority-urgent';
    if (priority >= 7) return 'priority-high';
    if (priority <= 3) return 'priority-low';
    return 'priority-normal';
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

async function handleNext() {
    if (isProcessing || !currentGameId) return;
    
    isProcessing = true;
    const nextBtn = document.getElementById('next-btn');
    nextBtn.disabled = true;
    nextBtn.textContent = 'Processing...';
    
    try {
        const response = await fetch(`/game/${currentGameId}/next`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        
        const data = await response.json();
        
        if (response.ok) {
            updateDisplay(data.game_state);
            
            if (data.message === 'Game over') {
                alert(`Game Over! ${data.winner === 'mafia' ? 'Mafia' : 'Town'} wins!`);
            }
        } else {
            alert('Error: ' + (data.error || 'Failed to process next action'));
            loadGameState(); // Refresh to get current state
        }
    } catch (error) {
        alert('Error: ' + error.message);
        loadGameState(); // Refresh to get current state
    } finally {
        isProcessing = false;
        nextBtn.disabled = false;
        nextBtn.textContent = 'Next Action';
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

