const socket = io({
    transports: ["websocket", "polling"],
});

let currentConversationId = null;
let currentFilter = 'unassigned';
let currentChannel = null;
let currentAgent = null;

// Check authentication status on page load
document.addEventListener('DOMContentLoaded', () => {
    const loginPage = document.getElementById('loginPage');
    const dashboardSection = document.getElementById('dashboard');
    if (!loginPage || !dashboardSection) {
        console.error('Required DOM elements (loginPage, dashboard) are missing.');
        return;
    }

    checkAuthStatus();
    fetchAISetting();
    fetchConversations();
    setInterval(fetchConversations, 10000);

    const messageInput = document.getElementById('messageInput');
    if (messageInput) {
        messageInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter' || e.keyCode === 13) {
                e.preventDefault();
                sendMessage();
            }
        });
    } else {
        console.error('Message input not found for adding Enter key listener.');
    }

    // Auto-logout after 1 hour of inactivity
    const INACTIVITY_TIMEOUT = 60 * 60 * 1000; // 1 hour in milliseconds
    let inactivityTimer;

    function resetInactivityTimer() {
        console.log("Resetting inactivity timer");
        clearTimeout(inactivityTimer);
        inactivityTimer = setTimeout(() => {
            console.log("Inactivity timeout reached, logging out");
            fetch("/logout", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
            })
                .then((response) => response.json())
                .then((data) => {
                    if (data.message === "Logged out successfully") {
                        alert("Logged out due to inactivity");
                        window.location.href = "/login";
                    } else {
                        alert("Auto-logout failed: " + (data.message || "Unknown error"));
                    }
                })
                .catch((error) => {
                    console.error("Error during auto-logout:", error);
                    alert("An error occurred during auto-logout: " + error.message);
                });
        }, INACTIVITY_TIMEOUT);
    }

    // Track user activity
    ["mousemove", "mousedown", "keypress", "scroll", "touchstart"].forEach((event) => {
        document.addEventListener(event, resetInactivityTimer, { passive: true });
    });

    // Start the timer on page load
    resetInactivityTimer();
});

// Fetch AI setting on page load
function fetchAISetting() {
    fetch('/settings')
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            const aiToggle = document.getElementById('ai-toggle');
            const aiToggleError = document.getElementById('ai-toggle-error');
            if (aiToggle && aiToggleError) {
                aiToggle.checked = data.ai_enabled === '1';
                aiToggleError.style.display = 'none';
                aiToggle.addEventListener('change', () => {
                    toggleAI(aiToggle.checked);
                });
            } else {
                console.error('AI toggle or error element not found on dashboard page.');
            }
        })
        .catch(error => {
            console.error('Error fetching AI setting:', error);
            const aiToggleError = document.getElementById('ai-toggle-error');
            if (aiToggleError) {
                aiToggleError.textContent = 'Failed to load AI setting: ' + error.message;
                aiToggleError.style.display = 'inline';
            }
        });
}

// Toggle global AI setting
function toggleAI(enabled) {
    const aiToggleError = document.getElementById('ai-toggle-error');
    fetch('/settings', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ key: 'ai_enabled', value: enabled ? '1' : '0' }),
    })
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.status === 'success') {
                console.log('Global AI setting updated successfully');
                if (aiToggleError) {
                    aiToggleError.style.display = 'none';
                }
            } else {
                throw new Error(data.error || 'Failed to update AI settings');
            }
        })
        .catch(error => {
            console.error('Error updating AI settings:', error);
            if (aiToggleError) {
                aiToggleError.textContent = 'Error updating AI settings: ' + error.message;
                aiToggleError.style.display = 'inline';
            }
            const aiToggle = document.getElementById('ai-toggle');
            if (aiToggle) {
                aiToggle.checked = !enabled; // Revert on error
            }
        });
}

// Check if user is authenticated
function checkAuthStatus() {
    fetch('/check-auth')
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            const loginPage = document.getElementById('loginPage');
            const dashboardSection = document.getElementById('dashboard');
            if (data.is_authenticated) {
                currentAgent = data.agent;
                console.log("Current Agent:", currentAgent);
                loginPage.style.display = 'none';
                dashboardSection.style.display = 'block';
                fetchConversations();
            } else {
                loginPage.style.display = 'flex';
                dashboardSection.style.display = 'none';
            }
        })
        .catch(error => console.error('Error checking auth status:', error));
}

// Login function for the button
function login() {
    const usernameInput = document.getElementById('username');
    const passwordInput = document.getElementById('password');
    if (!usernameInput || !passwordInput) {
        console.error('Username or password input missing.');
        return;
    }

    const username = usernameInput.value;
    const password = passwordInput.value;

    fetch('/login', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ username, password }),
    })
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.message === 'Login successful') {
                currentAgent = data.agent;
                console.log("Current Agent:", currentAgent);
                document.getElementById('loginPage').style.display = 'none';
                document.getElementById('dashboard').style.display = 'block';
                fetchConversations();
            } else {
                alert('Login failed: ' + data.message);
            }
        })
        .catch(error => console.error('Error during login:', error));
}

// Logout button
const logoutButton = document.getElementById('logout-button');
if (logoutButton) {
    logoutButton.addEventListener('click', () => {
        fetch('/logout', {
            method: 'POST',
        })
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
                }
                return response.json();
            })
            .then(data => {
                if (data.message === 'Logged out successfully') {
                    currentAgent = null;
                    document.getElementById('loginPage').style.display = 'flex';
                    document.getElementById('dashboard').style.display = 'none';
                }
            })
            .catch(error => console.error('Error during logout:', error));
    });
} else {
    console.error('Logout button not found.');
}

function fetchConversations() {
    const conversationList = document.getElementById('conversationList');
    const conversationError = document.getElementById('conversation-error');
    if (!conversationList) {
        console.error('Conversation list (conversationList) is missing.');
        return;
    }
    if (!conversationError) {
        console.error('Conversation error element (conversation-error) is missing.');
    }

    fetch('/conversations')
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
            }
            return response.json();
        })
        .then(conversations => {
            console.log('Fetched Conversations:', conversations);
            conversationList.innerHTML = '';
            if (conversationError) {
                conversationError.style.display = 'none';
            }

            console.log('Current Filter:', currentFilter, 'Current Channel:', currentChannel, 'Current Agent:', currentAgent);
            let filteredConversations = conversations.filter(convo => {
                // Only show conversations that need agent intervention
                if (!convo.needs_agent) {
                    return false;
                }
                if (currentChannel && convo.channel !== currentChannel) {
                    return false;
                }
                if (currentFilter === 'unassigned') {
                    return !convo.assigned_agent;
                } else if (currentFilter === 'you') {
                    return convo.assigned_agent === currentAgent;
                } else if (currentFilter === 'team') {
                    return convo.assigned_agent && convo.assigned_agent !== currentAgent;
                }
                return true;
            });

            console.log('Filtered Conversations:', filteredConversations);

            updateCounts(conversations);

            filteredConversations.forEach(convo => {
                console.log("Filter Check:", currentFilter, convo.username, convo.assigned_agent, currentAgent);
                const li = document.createElement('li');
                li.className = 'conversation-item';

                const convoContainer = document.createElement('div');
                convoContainer.style.display = 'flex';
                convoContainer.style.justifyContent = 'space-between';
                convoContainer.style.alignItems = 'center';

                const convoInfo = document.createElement('span');
                convoInfo.textContent = `${convo.username} (${convo.channel}): Assigned to ${convo.assigned_agent || 'unassigned'}`;
                convoInfo.onclick = () => loadConversation(convo.id);
                convoInfo.style.cursor = 'pointer';
                convoContainer.appendChild(convoInfo);

                if (currentFilter === 'unassigned' && !convo.assigned_agent) {
                    const takeOverButton = document.createElement('button');
                    takeOverButton.textContent = 'Take Over';
                    takeOverButton.onclick = (e) => {
                        e.stopPropagation();
                        takeOverConversation(convo.id);
                    };
                    takeOverButton.className = 'take-over-btn';
                    convoContainer.appendChild(takeOverButton);
                }

                if (currentFilter === 'you' && convo.assigned_agent === currentAgent) {
                    const handBackButton = document.createElement('button');
                    handBackButton.textContent = 'Hand Back to AI';
                    handBackButton.onclick = (e) => {
                        e.stopPropagation();
                        handBackToAI(convo.id);
                    };
                    handBackButton.className = 'handback-button';
                    convoContainer.appendChild(handBackButton);
                }

                li.appendChild(convoContainer);
                conversationList.appendChild(li);
            });
        })
        .catch(error => {
            console.error('Error fetching conversations:', error);
            if (conversationError) {
                conversationError.textContent = 'Failed to load conversations: ' + error.message;
                conversationError.style.display = 'inline';
            }
        });
}

// Update conversation counts
function updateCounts(conversations) {
    const unassignedCount = document.getElementById('unassignedCount');
    const yourCount = document.getElementById('yourCount');
    const teamCount = document.getElementById('teamCount');
    const allCount = document.getElementById('allCount');

    if (unassignedCount && yourCount && teamCount && allCount) {
        unassignedCount.textContent = conversations.filter(c => !c.assigned_agent).length;
        yourCount.textContent = conversations.filter(c => c.assigned_agent === currentAgent).length;
        teamCount.textContent = conversations.filter(c => c.assigned_agent && c.assigned_agent !== currentAgent).length;
        allCount.textContent = conversations.length;
    }
}

// Load conversations based on filter
function loadConversations(filter) {
    currentFilter = filter;
    fetchConversations();
}

// Filter by channel
function filterByChannel(channel) {
    currentChannel = channel;
    fetchConversations();
}

// Take over a conversation
function takeOverConversation(convoId) {
    fetch('/handoff', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            conversation_id: convoId,
            disable_ai: true, // Indicate that AI should be disabled for this conversation
        }),
    })
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.message) {
                currentFilter = 'you';
                fetchConversations();
                if (currentConversationId === convoId) {
                    loadConversation(convoId);
                }
            } else {
                alert('Error assigning chat: ' + data.error);
            }
        })
        .catch(error => console.error('Error during handoff:', error));
}

// Load a conversation into the active panel
function loadConversation(convoId) {
    currentConversationId = convoId;
    const chatBox = document.getElementById('chatBox');
    const clientName = document.getElementById('clientName');
    if (!chatBox || !clientName) {
        console.error('Chat box or client name element not found.');
        return;
    }

    fetch(`/messages?conversation_id=${convoId}`)
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            const messages = data.messages;
            const username = data.username;
            chatBox.innerHTML = '';

            messages.forEach(msg => {
                const div = document.createElement('div');
                const isUser = msg.sender === 'user';
                const isAgent = msg.sender === 'agent';
                div.className = 'message';
                div.classList.add(isUser ? 'user-message' : isAgent ? 'agent-message' : 'ai-message');
                const textSpan = document.createElement('span');
                textSpan.textContent = msg.message;
                div.appendChild(textSpan);

                const timestampSpan = document.createElement('span');
                timestampSpan.className = 'message-timestamp';
                const timeMatch = msg.timestamp.match(/\d{2}:\d{2}/);
                timestampSpan.textContent = timeMatch ? timeMatch[0] : msg.timestamp;
                div.appendChild(timestampSpan);

                chatBox.appendChild(div);
            });

            chatBox.scrollTop = chatBox.scrollHeight;
            clientName.textContent = username;
        })
        .catch(error => console.error('Error loading messages:', error));
}

function sendMessage() {
    if (!currentConversationId) {
        alert('Please select a conversation.');
        return;
    }

    const messageInput = document.getElementById('messageInput');
    if (!messageInput) {
        console.error('Message input not found.');
        alert('Error: Message input field not found.');
        return;
    }

    const message = messageInput.value.trim();
    if (!message) {
        alert('Please enter a message to send.');
        return;
    }

    fetch('/chat', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            convo_id: currentConversationId,
            message: message,
            channel: 'whatsapp'
        }),
    })
        .then(response => {
            if (!response.ok) {
                throw new Error(`Failed to send message: ${response.status} ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.status === 'success') {
                messageInput.value = '';
                loadConversation(currentConversationId); // Refresh the chat box
            } else if (data.reply) {
                console.log('AI response will be handled via Socket.IO:', data.reply);
            } else {
                console.error('Error sending message:', data.error);
                alert('Failed to send message: ' + (data.error || 'Unknown error'));
            }
        })
        .catch(error => {
            console.error('Error sending message:', error);
            alert('Error sending message: ' + error.message);
        });
}

// Hand back to AI
function handBackToAI(convoId) {
    fetch('/handback-to-ai', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ 
            conversation_id: convoId,
            enable_ai: true, // Indicate that AI should be re-enabled for this conversation
            clear_needs_agent: true // Indicate that this conversation no longer needs agent intervention
        }),
    })
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.message) {
                alert(data.message);
                socket.emit('refresh_conversations', { conversation_id: convoId });
                if (currentConversationId === convoId) {
                    currentConversationId = null;
                    const chatBox = document.getElementById('chatBox');
                    const clientName = document.getElementById('clientName');
                    if (chatBox && clientName) {
                        chatBox.innerHTML = '';
                        clientName.textContent = 'Select a conversation';
                    }
                }
                fetchConversations();
            } else {
                alert('Failed to hand back to AI: ' + data.error);
            }
        })
        .catch(error => console.error('Error handing back to AI:', error));
}

// Socket.IO event listeners
socket.on('new_message', (data) => {
    console.log('New message received:', data);
    if (data.convo_id === currentConversationId) {
        loadConversation(currentConversationId); // Refresh the chat box
    }
    fetchConversations();
});

socket.on('error', (data) => {
    console.error('Error received:', data);
    alert(data.message);
});

socket.on('refresh_conversations', (data) => {
    console.log('Refresh conversations event received:', data);
    const conversationId = data.conversation_id;
    pollVisibility(conversationId);
    fetchConversations();
});

socket.on('settings_updated', (data) => {
    const aiToggle = document.getElementById('ai-toggle');
    const aiToggleError = document.getElementById('ai-toggle-error');
    if (aiToggle && aiToggleError) {
        aiToggle.checked = data.ai_enabled === '1';
        aiToggleError.style.display = 'none';
    }
});

socket.on("reconnect", (attempt) => {
    console.log(`Reconnected to Socket.IO after ${attempt} attempts`);
    fetchConversations();
});

socket.on("reconnect_error", (error) => {
    console.error("Reconnection failed:", error);
});

// Poll visibility of a conversation
function pollVisibility(conversationId) {
    fetch(`/check-visibility?conversation_id=${conversationId}`)
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.visible) {
                console.log(`Conversation ${conversationId} is now visible`);
                fetchConversations();
            } else {
                console.log(`Conversation ${conversationId} is not yet visible, polling again...`);
                setTimeout(() => pollVisibility(conversationId), 1000);
            }
        })
        .catch(error => {
            console.error('Error polling visibility:', error);
            setTimeout(() => pollVisibility(conversationId), 1000);
        });
}
