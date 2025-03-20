const socket = io({
    transports: ["websocket", "polling"],
});

let currentConversationId = null;
let currentFilter = 'unassigned';
let currentChannel = null;
let currentAgent = null;
let lastMessageDate = null;

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
    if (!conversationList) {
        console.error('Conversation list (conversationList) is missing.');
        return;
    }

    fetch(`/conversations?filter=${currentFilter}${currentChannel ? `&channel=${currentChannel}` : ''}`)
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
            }
            return response.json();
        })
        .then(conversations => {
            conversationList.innerHTML = '';

            updateCounts();

            conversations.forEach(convo => {
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

                if (!convo.assigned_agent) {
                    const takeOverButton = document.createElement('button');
                    takeOverButton.textContent = 'Take Over';
                    takeOverButton.onclick = (e) => {
                        e.stopPropagation();
                        takeOverConversation(convo.id);
                    };
                    takeOverButton.className = 'take-over-btn';
                    convoContainer.appendChild(takeOverButton);
                } else if (convo.assigned_agent === currentAgent) {
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
        .catch(error => console.error('Error fetching conversations:', error));
}

// Update conversation counts
function updateCounts() {
    fetch('/conversations?filter=unassigned')
        .then(response => response.json())
        .then(data => {
            const unassignedCount = document.getElementById('unassignedCount');
            if (unassignedCount) unassignedCount.textContent = data.length;
        });
    fetch('/conversations?filter=you')
        .then(response => response.json())
        .then(data => {
            const yourCount = document.getElementById('yourCount');
            if (yourCount) yourCount.textContent = data.length;
        });
    fetch('/conversations?filter=team')
        .then(response => response.json())
        .then(data => {
            const teamCount = document.getElementById('teamCount');
            if (teamCount) teamCount.textContent = data.length;
        });
    fetch('/conversations?filter=all')
        .then(response => response.json())
        .then(data => {
            const allCount = document.getElementById('allCount');
            if (allCount) allCount.textContent = data.length;
        });
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
    fetch('/takeover', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ convo_id: convoId }),
    })
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.status === 'success') {
                currentFilter = 'you';
                fetchConversations();
                if (currentConversationId === convoId) {
                    loadConversation(convoId);
                }
            } else {
                alert('Error assigning chat: ' + data.error);
            }
        })
        .catch(error => console.error('Error during takeover:', error));
}

// Hand back to AI
function handBackToAI(convoId) {
    fetch('/handback', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ convo_id: convoId }),
    })
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.status === 'success') {
                alert('Conversation handed back to AI successfully');
                socket.emit('refresh_conversations', { conversation_id: convoId });
                if (currentConversationId === convoId) {
                    currentConversationId = null;
                    const chatBox = document.getElementById('chatBox');
                    const clientName = document.getElementById('clientName');
                    if (chatBox) chatBox.innerHTML = '';
                    if (clientName) clientName.textContent = '-';
                }
            } else {
                alert('Failed to hand back to AI: ' + data.error);
            }
        })
        .catch(error => console.error('Error handing back to AI:', error));
}

// Format date for separator
function formatDateForSeparator(timestamp) {
    const date = new Date(timestamp);
    const today = new Date();
    if (date.toDateString() === today.toDateString()) {
        return 'Today';
    }
    const yesterday = new Date(today);
    yesterday.setDate(today.getDate() - 1);
    if (date.toDateString() === yesterday.toDateString()) {
        return 'Yesterday';
    }
    return date.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' });
}

// Add date separator
function addDateSeparator(dateStr) {
    const chatBox = document.getElementById('chatBox');
    const separator = document.createElement('div');
    separator.className = 'date-separator';
    separator.textContent = dateStr;
    chatBox.appendChild(separator);
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

    fetch(`/messages/${convoId}`)
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
            }
            return response.json();
        })
        .then(messages => {
            chatBox.innerHTML = '';
            lastMessageDate = null;

            messages.forEach(msg => {
                const messageDate = new Date(msg.timestamp);
                const dateStr = formatDateForSeparator(msg.timestamp);

                if (!lastMessageDate || formatDateForSeparator(lastMessageDate) !== dateStr) {
                    addDateSeparator(dateStr);
                    lastMessageDate = messageDate;
                }

                const div = document.createElement('div');
                let senderClass = msg.sender.toLowerCase();
                if (senderClass === 'user') {
                    senderClass = 'user';
                } else if (senderClass === 'ai') {
                    senderClass = 'ai';
                } else if (senderClass === 'agent') {
                    senderClass = 'agent';
                } else {
                    senderClass = 'user'; // Default to user
                }
                div.className = `message ${senderClass}`;
                div.innerHTML = `
                    <div class="message-bubble">${msg.message}</div>
                    <span class="message-timestamp">${new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                `;
                chatBox.appendChild(div);
            });

            chatBox.scrollTop = chatBox.scrollHeight;

            // Update client name
            fetch(`/conversations?filter=${currentFilter}${currentChannel ? `&channel=${currentChannel}` : ''}`)
                .then(response => response.json())
                .then(conversations => {
                    const convo = conversations.find(c => c.id === convoId);
                    clientName.textContent = convo ? convo.username : '-';
                });
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
                // Message will be appended via Socket.IO 'new_message' event
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

// Socket.IO event listeners
socket.on('new_message', (data) => {
    console.log('New message received:', data);
    if (data.convo_id === currentConversationId) {
        const chatBox = document.getElementById('chatBox');
        if (chatBox) {
            const messageDate = new Date(data.timestamp || new Date());
            const dateStr = formatDateForSeparator(messageDate);

            if (!lastMessageDate || formatDateForSeparator(lastMessageDate) !== dateStr) {
                addDateSeparator(dateStr);
                lastMessageDate = messageDate;
            }

            const div = document.createElement('div');
            let senderClass = data.sender.toLowerCase();
            if (senderClass === 'user') {
                senderClass = 'user';
            } else if (senderClass === 'ai') {
                senderClass = 'ai';
            } else if (senderClass === 'agent') {
                senderClass = 'agent';
            } else {
                senderClass = 'user'; // Default to user
            }
            div.className = `message ${senderClass}`;
            div.innerHTML = `
                <div class="message-bubble">${data.message}</div>
                <span class="message-timestamp">${new Date(data.timestamp || new Date()).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
            `;
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
    fetchConversations();
    if (currentConversationId === conversationId) {
        fetch(`/conversations?filter=${currentFilter}${currentChannel ? `&channel=${currentChannel}` : ''}`)
            .then(response => response.json())
            .then(conversations => {
                if (!conversations.some(convo => convo.id === conversationId)) {
                    currentConversationId = null;
                    const chatBox = document.getElementById('chatBox');
                    const clientName = document.getElementById('clientName');
                    if (chatBox) chatBox.innerHTML = '';
                    if (clientName) clientName.textContent = '-';
                }
            });
    }
});

socket.on("reconnect", (attempt) => {
    console.log(`Reconnected to Socket.IO after ${attempt} attempts`);
    fetchConversations();
    if (currentConversationId) {
        loadConversation(currentConversationId);
    }
});

socket.on("reconnect_error", (error) => {
    console.error("Reconnection failed:", error);
});
