const socket = io({
    transports: ["websocket", "polling"], // Prioritize WebSocket, fall back to polling
});

let currentConversationId = null;
let currentFilter = 'unassigned'; // Default filter
let currentChannel = null; // Default: no channel filter
let currentAgent = null; // Store the current agent's username

// Check authentication status on page load
document.addEventListener('DOMContentLoaded', () => {
    const loginPage = document.getElementById('loginPage');
    const dashboardSection = document.getElementById('dashboard');
    if (!loginPage || !dashboardSection) {
        console.error('Required DOM elements (loginPage, dashboard) are missing.');
        return;
    }

    checkAuthStatus();
    fetchConversations();
    setInterval(fetchConversations, 10000); // Poll every 10 seconds

    // Add event listener for Enter key to send messages
    const messageInput = document.getElementById('messageInput');
    if (messageInput) {
        messageInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter' || e.keyCode === 13) {
                e.preventDefault(); // Prevent default behavior (e.g., new line)
                sendMessage(); // Call the sendMessage function
            }
        });
    } else {
        console.error('Message input not found for adding Enter key listener.');
    }
});

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
                currentAgent = data.agent; // Assuming /check-auth returns the agent's username
                console.log("Current Agent:", currentAgent); // Debug log
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
                currentAgent = data.agent; // Assuming /login returns the agent's username
                console.log("Current Agent:", currentAgent); // Debug log
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
                    currentAgent = null; // Clear the current agent
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
    if (!conversationList) {
        console.error('Conversation list (conversationList) is missing.');
        return;
    }

    fetch('/conversations')
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
            }
            return response.json();
        })
        .then(conversations => {
            conversationList.innerHTML = '';

            // Filter conversations based on currentFilter and currentChannel
            let filteredConversations = conversations.filter(convo => {
                // Filter by channel
                if (currentChannel && convo.channel !== currentChannel) {
                    return false;
                }
                // Filter by assignment
                if (currentFilter === 'unassigned') {
                    return !convo.assigned_agent;
                } else if (currentFilter === 'you') {
                    return convo.assigned_agent === currentAgent;
                } else if (currentFilter === 'team') {
                    return convo.assigned_agent && convo.assigned_agent !== currentAgent;
                }
                return true; // 'all' filter
            });

            // Update counts
            updateCounts(conversations);

            filteredConversations.forEach(convo => {
                console.log("Filter Check:", currentFilter, convo.username, convo.assigned_agent, currentAgent); // Debug log
                const li = document.createElement('li');
                // Add spacing via CSS class
                li.className = 'conversation-item'; // Add a class for styling

                // Create a container for the conversation info and button
                const convoContainer = document.createElement('div');
                convoContainer.style.display = 'flex';
                convoContainer.style.justifyContent = 'space-between';
                convoContainer.style.alignItems = 'center';

                // Conversation info
                const convoInfo = document.createElement('span');
                convoInfo.textContent = `${convo.username} (${convo.channel}): Assigned to ${convo.assigned_agent || 'unassigned'}`;
                convoInfo.onclick = () => loadConversation(convo.id);
                convoInfo.style.cursor = 'pointer';
                convoContainer.appendChild(convoInfo);

                // Add "Take Over" button for unassigned conversations
                if (currentFilter === 'unassigned' && !convo.assigned_agent) {
                    const takeOverButton = document.createElement('button');
                    takeOverButton.textContent = 'Take Over';
                    takeOverButton.onclick = (e) => {
                        e.stopPropagation(); // Prevent triggering loadConversation
                        takeOverConversation(convo.id);
                    };
                    takeOverButton.className = 'take-over-btn'; // Use a CSS class for consistency
                    convoContainer.appendChild(takeOverButton);
                }

                // Add "Hand Back to AI" button for conversations assigned to the current agent
                if (currentFilter === 'you' && convo.assigned_agent === currentAgent) {
                    const handBackButton = document.createElement('button');
                    handBackButton.textContent = 'Hand Back to AI';
                    handBackButton.onclick = (e) => {
                        e.stopPropagation(); // Prevent triggering loadConversation
                        handBackToAI(convo.id);
                    };
                    handBackButton.className = 'handback-button'; // Use the CSS class from dashboard.html
                    convoContainer.appendChild(handBackButton);
                }

                li.appendChild(convoContainer);
                conversationList.appendChild(li);
            });
        })
        .catch(error => console.error('Error fetching conversations:', error));
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
                currentFilter = 'you'; // Force switch to "You" filter after taking over
                fetchConversations(); // Refresh the conversation list
                if (currentConversationId === convoId) {
                    loadConversation(convoId); // Reload the current conversation
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
                div.className = 'message'; // Add the base message class
                div.classList.add(isUser ? 'user-message' : 'agent-message');

                // Message text
                const textSpan = document.createElement('span');
                textSpan.textContent = msg.message;
                div.appendChild(textSpan);

                // Timestamp
                const timestampSpan = document.createElement('span');
                timestampSpan.className = 'message-timestamp';
                const timeMatch = msg.timestamp.match(/\d{2}:\d{2}/);
                timestampSpan.textContent = timeMatch ? timeMatch[0] : msg.timestamp;
                div.appendChild(timestampSpan);

                chatBox.appendChild(div);
            });

            chatBox.scrollTop = chatBox.scrollHeight;

            // Update client name
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
            convo_id: currentConversationId, // Changed from conversation_id to convo_id
            message: message,
            channel: 'whatsapp' // Added the channel field
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
                // Message sent successfully
                messageInput.value = ''; // Clear the input after sending
                // Optionally, add the sent message to the chat area immediately
                const chatMessages = document.getElementById('chat-messages');
                const messageDiv = document.createElement('div');
                messageDiv.className = 'message user-message';
                messageDiv.textContent = message;
                messageDiv.dataset.timestamp = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                chatMessages.appendChild(messageDiv);
                chatMessages.scrollTop = chatMessages.scrollHeight;
            } else if (data.reply) {
                // If AI responds, it will be handled via Socket.IO
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
        body: JSON.stringify({ conversation_id: convoId }),
    })
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.message) {
                alert(data.message); // Temporary feedback; consider a better UI solution
                socket.emit('refresh_conversations', { conversation_id: convoId });
                if (currentConversationId === convoId) {
                    loadConversation(convoId); // Refresh the chat panel
                }
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
        const chatBox = document.getElementById('chatBox');
        if (chatBox) {
            const div = document.createElement('div');
            const isUser = data.sender === 'user';
            div.className = 'message'; // Add the base message class
            div.classList.add(isUser ? 'user-message' : 'agent-message');

            const textSpan = document.createElement('span');
            textSpan.textContent = data.message;
            div.appendChild(textSpan);

            const timestampSpan = document.createElement('span');
            timestampSpan.className = 'message-timestamp';
            const now = new Date();
            timestampSpan.textContent = `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}`;
            div.appendChild(timestampSpan);

            chatBox.appendChild(div);
            chatBox.scrollTop = chatBox.scrollHeight;
        }
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

socket.on("reconnect", (attempt) => {
    console.log(`Reconnected to Socket.IO after ${attempt} attempts`);
    // Optionally reload conversations to ensure the UI is up-to-date
    loadConversations();
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
                fetchConversations(); // Refresh the list again to be sure
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
