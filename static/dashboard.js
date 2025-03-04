// dashboard.js
const socket = io();

let currentConversationId = null;
let currentFilter = 'unassigned'; // Default filter
let currentChannel = null; // Default: no channel filter

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
    setInterval(fetchConversations, 5000); // Poll every 5 seconds
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
                loginPage.style.display = 'none';
                dashboardSection.style.display = 'block';
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
                    document.getElementById('loginPage').style.display = 'flex';
                    document.getElementById('dashboard').style.display = 'none';
                }
            })
            .catch(error => console.error('Error during logout:', error));
    });
} else {
    console.error('Logout button not found.');
}

// Fetch conversations
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
                    return convo.assigned_agent === 'agent1'; // Replace with current user's username if needed
                } else if (currentFilter === 'team') {
                    return convo.assigned_agent && convo.assigned_agent !== 'agent1';
                }
                return true; // 'all' filter
            });

            // Update counts
            updateCounts(conversations);

            filteredConversations.forEach(convo => {
                const li = document.createElement('li');
                // Create a container for the conversation info and button
                const convoContainer = document.createElement('div');
                convoContainer.style.display = 'flex';
                convoContainer.style.justifyContent = 'space-between';
                convoContainer.style.alignItems = 'center';

                // Conversation info
                const convoInfo = document.createElement('span');
                convoInfo.textContent = `${convo.username} (${convo.channel})`;
                convoInfo.onclick = () => loadConversation(convo.id);
                convoInfo.style.cursor = 'pointer';
                convoContainer.appendChild(convoInfo);

                // Add "Take Over" button for unassigned conversations
                if (!convo.assigned_agent) {
                    const takeOverButton = document.createElement('button');
                    takeOverButton.textContent = 'Take Over';
                    takeOverButton.onclick = () => takeOverConversation(convo.id);
                    takeOverButton.style.marginLeft = '10px';
                    takeOverButton.style.padding = '5px 10px';
                    takeOverButton.style.backgroundColor = '#007bff';
                    takeOverButton.style.color = 'white';
                    takeOverButton.style.border = 'none';
                    takeOverButton.style.borderRadius = '3px';
                    takeOverButton.style.cursor = 'pointer';
                    convoContainer.appendChild(takeOverButton);
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
        yourCount.textContent = conversations.filter(c => c.assigned_agent === 'agent1').length;
        teamCount.textContent = conversations.filter(c => c.assigned_agent && c.assigned_agent !== 'agent1').length;
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
        .then(messages => {
            chatBox.innerHTML = '';

            messages.forEach(msg => {
                const div = document.createElement('div');
                const isUser = msg.sender === 'user';
                div.className = isUser ? 'user-message' : 'agent-message';

                // Message text
                const textSpan = document.createElement('span');
                textSpan.textContent = msg.message;
                div.appendChild(textSpan);

                // Timestamp
                const timestampSpan = document.createElement('span');
                timestampSpan.className = 'message-timestamp';
                // Extract time (e.g., "2025-03-04 21:24:54" -> "21:24")
                const timeMatch = msg.timestamp.match(/\d{2}:\d{2}/);
                timestampSpan.textContent = timeMatch ? timeMatch[0] : msg.timestamp;
                div.appendChild(timestampSpan);

                chatBox.appendChild(div);
            });

            chatBox.scrollTop = chatBox.scrollHeight;

            // Update client name (username)
            fetch('/conversations')
                .then(response => response.json())
                .then(conversations => {
                    const convo = conversations.find(c => c.id === convoId);
                    if (convo) {
                        clientName.textContent = convo.username;
                    }
                });
        })
        .catch(error => console.error('Error loading messages:', error));
}

// Send a message
function sendMessage() {
    if (!currentConversationId) {
        alert('Please select a conversation.');
        return;
    }

    const messageInput = document.getElementById('messageInput');
    if (!messageInput) {
        console.error('Message input not found.');
        return;
    }

    const message = messageInput.value.trim();
    if (!message) return;

    fetch('/chat', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            conversation_id: currentConversationId,
            message: message,
        }),
    })
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}, StatusText: ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.reply) {
                // If AI responds, it will be handled via Socket.IO
            } else if (data.status === 'success') {
                // Agent message sent successfully
                messageInput.value = '';
            } else {
                console.error('Error sending message:', data.error);
            }
        })
        .catch(error => console.error('Error sending message:', error));
}

// Socket.IO event listeners
socket.on('new_message', (data) => {
    console.log('New message received:', data);
    if (data.convo_id === currentConversationId) {
        const chatBox = document.getElementById('chatBox');
        if (chatBox) {
            const div = document.createElement('div');
            const isUser = data.sender === 'user';
            div.className = isUser ? 'user-message' : 'agent-message';

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
